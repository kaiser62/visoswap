"""Python source executed ON the Colab VM through `colab exec -f`. Each script prints a final
``VS_OK <step>`` or ``VS_FAIL <step> <why>`` marker; the launcher keys off that (colab exec's own
exit status is not reliable). No script ever prints the tunnel token. Config is injected as JSON
(no secrets: the token travels as a 600 file via `colab upload`)."""
from __future__ import annotations

import inspect
import json

HEADER = '''
import json, os, shutil, subprocess, sys, time, glob, importlib.util, urllib.request
CFG = json.loads(r"""__CFG__""")
REPO = CFG["repo_dir"]; MODELS = REPO + "/model_assets_owned"
def sh(cmd, timeout=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)
def pgrep(pat):
    rc, out = sh(["pgrep", "-f", pat]); return [p for p in out.split() if p.isdigit() and int(p) != os.getpid()]
def local_health():
    try:
        return '"ok"' in urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5).read(2000).decode()
    except Exception:
        return False
'''

PROBE = HEADER + '''
import shutil
def has(mod): return importlib.util.find_spec(mod) is not None
mods = ["onnxruntime", "pydantic_settings", "yt_dlp", "ftfy", "regex", "numexpr", "onnxsim", "kornia", "skimage", "psutil", "onnx", "fastapi", "uvicorn", "aiosqlite", "httpx", "cv2", "PIL", "requests", "tqdm", "pydantic"]
rc, gpu = sh("nvidia-smi --query-gpu=name --format=csv,noheader")
rc2, sha = sh(["git", "-C", REPO, "rev-parse", "--short", "HEAD"])
print("VS_PROBE " + json.dumps({
    "gpu": gpu.strip().splitlines()[0] if rc == 0 and gpu.strip() else None,
    "deps_missing": [m for m in mods if not has(m)],
    "repo": os.path.isdir(REPO + "/.git"), "sha": sha.strip() if rc2 == 0 else None,
    "models": len(glob.glob(MODELS + "/*.onnx")),
    "cloudflared": bool(shutil.which("cloudflared")),
    "token_file": os.path.isfile(CFG["token_file"]),
    "uvicorn": bool(pgrep("uvicorn backend.main")), "tunnel": bool(pgrep("cloudflared tunnel")),
    "health": local_health()}))
print("VS_OK probe")
'''

DEPS = HEADER + '''
PIP = {"pydantic_settings": "pydantic-settings", "yt_dlp": "yt-dlp", "ftfy": "ftfy", "regex": "regex", "numexpr": "numexpr",
       "onnxsim": "onnxsim", "kornia": "kornia", "skimage": "scikit-image", "psutil": "psutil", "onnx": "onnx",
       "fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "aiosqlite": "aiosqlite", "httpx": "httpx", "multipart": "python-multipart",
       "cv2": "opencv-python", "PIL": "Pillow", "requests": "requests", "tqdm": "tqdm", "pydantic": "pydantic"}   # the last five were missing on the first real L4 deploy
need = [p for m, p in PIP.items() if importlib.util.find_spec(m) is None]
def pip(*a):
    rc, out = sh([sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir", *a], timeout=1500)
    if rc: print(out[-800:]); print("VS_FAIL deps pip " + " ".join(a)[:80]); sys.exit(0)
if need:
    print("installing:", " ".join(need)); pip(*need)
if not shutil.which("ffmpeg"):
    sh("apt-get update -qq && apt-get install -y -qq ffmpeg >/dev/null 2>&1", timeout=600)
# onnxruntime-gpu must match torch's CUDA major (same rule as visoswap_kaggle.ipynb cell 4); torch is never touched
rc, prov = sh([sys.executable, "-c", "import onnxruntime as o;print('CUDAExecutionProvider' in o.get_available_providers())"])
if "True" not in prov:
    rc, cu = sh([sys.executable, "-c", "import torch;print((torch.version.cuda or '0').split('.')[0])"])
    cu = cu.strip().splitlines()[-1] if cu.strip() else "0"
    print("CUDA major from torch:", cu)
    sh([sys.executable, "-m", "pip", "uninstall", "-y", "onnxruntime", "onnxruntime-gpu"])
    if cu == "12":
        pip("onnxruntime-gpu>=1.20,<1.27", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12", "nvidia-cublas-cu12", "nvidia-cufft-cu12", "nvidia-curand-cu12", "nvidia-cusolver-cu12", "nvidia-cusparse-cu12", "nvidia-nvjitlink-cu12")
    else:
        pip("onnxruntime-gpu>=1.27")
    rc, prov = sh([sys.executable, "-c", "import onnxruntime as o;print('CUDAExecutionProvider' in o.get_available_providers())"])
    if "True" not in prov: print("VS_FAIL deps CUDAExecutionProvider missing after install"); sys.exit(0)
print("VS_OK deps")
'''

REPO_STEP = HEADER + '''
ref = CFG["ref"]; url = CFG["repo_url"]
os.makedirs(REPO, exist_ok=True)
if not os.path.isdir(REPO + "/.git"):
    sh(["git", "-C", REPO, "init", "-q"])
sh(["git", "-C", REPO, "remote", "remove", "origin"]); sh(["git", "-C", REPO, "remote", "add", "origin", url])
before = sh(["git", "-C", REPO, "rev-parse", "HEAD"])[1].strip()
rc, out = sh(["git", "-C", REPO, "fetch", "-q", "--depth", "1", "origin", ref], timeout=600)
if rc: print(out[-500:]); print("VS_FAIL repo fetch " + ref); sys.exit(0)
rc, out = sh(["git", "-C", REPO, "checkout", "-q", "-f", "FETCH_HEAD"])   # untracked/ignored model_assets_owned survives
if rc: print(out[-500:]); print("VS_FAIL repo checkout"); sys.exit(0)
after = sh(["git", "-C", REPO, "rev-parse", "HEAD"])[1].strip()
print("VS_CHANGED " + ("1" if before != after else "0"), after[:8])
print("VS_OK repo")
'''

MODELS = HEADER + '''
# The download can take many minutes and a blocking `colab exec` stalled on the first L4 deploy, so it runs as a
# detached job (own session, log /content/models.log). This script starts it if needed, then polls briefly; the
# launcher calls it again until it prints VS_OK / VS_FAIL (VS_PENDING = still running).
LOG = "/content/models.log"; JOB = "/content/models_job.py"
code = ("import os,sys;sys.path.insert(0,%r)\\nfrom pathlib import Path\\nfrom visoswap.models.bootstrap import repair, FAST\\n"
        "r=repair(models_dir=Path(%r),mode=FAST,subset=%r)\\nprint('BOOT_OK' if r.ok else 'BOOT_INCOMPLETE', len(r.present), flush=True)") % (REPO, MODELS, CFG["subset"])
os.makedirs(MODELS, exist_ok=True)
running = pgrep(JOB)
if not running:
    open(JOB, "w").write(code)
    env = dict(os.environ, MODELS_DIR=MODELS, MODELS_SUBSET=CFG["subset"], PYTHONPATH=REPO)
    subprocess.Popen([sys.executable, "-u", JOB], cwd=REPO, env=env, stdout=open(LOG, "w"), stderr=subprocess.STDOUT, start_new_session=True)
    print("models job started")
t0 = time.time()
while time.time() - t0 < CFG.get("poll", 200):
    if not pgrep(JOB): break
    time.sleep(5)
log = open(LOG, errors="replace").read() if os.path.exists(LOG) else ""
if pgrep(JOB): print("VS_PENDING models " + " ".join(log.strip().splitlines()[-1:])[:120])
elif "BOOT_OK" in log: print("VS_OK models")
else: print(log[-500:]); print("VS_FAIL models bootstrap incomplete")
'''

START = HEADER + '''
import shutil
restart = CFG["restart"]; tok = CFG["token_file"]
if not shutil.which("cloudflared"):
    sh("curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared", timeout=300)
# --- settings: explicit override > running process env > state file from the last launch > defaults.
# Read BEFORE anything is killed. Only whitelisted, non-secret keys are ever read or printed.
KEYS = ["GENERATION_CONCURRENCY", "VISOSWAP_PROFILE", "VISOSWAP_PROCESS_WORKERS", "MODELS_SUBSET", "MODELS_VERIFY_MODE"]
STATE = "/content/visoswap_launch.json"
prev, src = {}, "defaults"
for pid in pgrep("uvicorn backend.main"):
    try:
        raw = open("/proc/%s/environ" % pid, "rb").read().split(b"\\0")
        e = dict(x.decode(errors="replace").split("=", 1) for x in raw if b"=" in x)
        prev, src = {k: e[k] for k in KEYS if k in e}, "running process"
        break
    except Exception:
        pass
if src == "defaults" and os.path.isfile(STATE):
    try:
        prev, src = {k: v for k, v in json.load(open(STATE)).items() if k in KEYS}, "state file"
    except Exception:
        pass
__MERGE__
settings = merge_settings(prev, src, CFG)
print("VS_SETTINGS " + json.dumps({"from": src, **settings}))
try: json.dump(settings, open(STATE, "w"))
except Exception: pass
if src == "defaults" and (restart or not pgrep("uvicorn backend.main")):
    print("VS_NOTE no earlier settings found (uvicorn is not running and there is no state file): using the defaults, i.e. the backend's own worker policy and profiling off")
if restart:
    # the engine's worker processes outlive uvicorn and keep the GPU busy: stop them too
    old = pgrep("uvicorn backend.main") + pgrep("cloudflared tunnel") + pgrep("engine-worker") + pgrep("multiprocessing.spawn")
    for p in old: sh(["kill", p])
    t_kill = time.time()
    while time.time() - t_kill < 20 and any(os.path.exists("/proc/" + p) for p in old): time.sleep(1)
    for p in old:
        if os.path.exists("/proc/" + p): sh(["kill", "-9", p])
    time.sleep(1)
env = dict(os.environ, MODELS_DIR=MODELS, PYTHONPATH=REPO)
for _k in KEYS: env.pop(_k, None)
env.update(settings)
_cu = subprocess.run([sys.executable, "-c", "import torch;print((torch.version.cuda or '0').split('.')[0])"], capture_output=True, text=True).stdout.strip()
# CUDA 12 only (pip-installed cuDNN); on CUDA 13 images torch's own libs are used and extra paths break cuDNN
libs = [] if _cu != "12" else [p for pat in ("/usr/local/lib/python3*/dist-packages/nvidia/*/lib", "/usr/local/lib/python3*/site-packages/nvidia/*/lib") for p in glob.glob(pat)]
if libs: env["LD_LIBRARY_PATH"] = ":".join(libs + [env.get("LD_LIBRARY_PATH", "")])
if not pgrep("uvicorn backend.main"):
    subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"], cwd=REPO, env=env,
                     stdout=open("/content/uvicorn.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    print("uvicorn started")
if not pgrep("cloudflared tunnel"):
    if os.path.isfile(tok):
        os.chmod(tok, 0o600)
        cmd = ["cloudflared", "tunnel", "--no-autoupdate", "run", "--token-file", tok, "--url", "http://127.0.0.1:8000"]   # token stays out of argv
    else:
        cmd = ["cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8000"]
    subprocess.Popen(cmd, stdout=open("/content/tunnel.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    print("tunnel started (named)" if os.path.isfile(tok) else "tunnel started (quick)")
t0 = time.time()
while time.time() - t0 < CFG["wait"]:
    if local_health(): break
    time.sleep(5)
if not local_health(): print("VS_FAIL start uvicorn not healthy; tail /content/uvicorn.log"); sys.exit(0)
import re
for _ in range(60):
    log = open("/content/tunnel.log").read() if os.path.exists("/content/tunnel.log") else ""
    m = re.search(r"https://[a-zA-Z0-9-]+\\.trycloudflare\\.com", log)
    if m and not os.path.isfile(tok): print("VS_QUICK_URL " + m.group(0))
    if m or "Registered tunnel connection" in log: break
    time.sleep(2)
print("VS_OK start")
'''

LOGS = HEADER + '''
for name in CFG["which"]:
    p = "/content/%s.log" % name
    print("--- " + p)
    if os.path.exists(p): print("".join(open(p, errors="replace").readlines()[-CFG["lines"]:]))
print("VS_OK logs")
'''


def merge_settings(prev, src, CFG):
    """prev = env of the running uvicorn / saved state; CFG holds explicit overrides (None = keep)."""
    settings = dict(prev)
    if CFG.get("concurrency") == "auto":
        settings.pop("GENERATION_CONCURRENCY", None)
    elif CFG.get("concurrency"):
        settings["GENERATION_CONCURRENCY"] = str(CFG["concurrency"])
    if CFG.get("profile") is not None:
        settings["VISOSWAP_PROFILE"] = "1" if CFG["profile"] else "0"
    settings.setdefault("MODELS_SUBSET", CFG["subset"])
    settings.setdefault("MODELS_VERIFY_MODE", "fast")
    if src == "defaults":    # nothing known about an earlier run: profiling off, and no worker env at all (the backend picks its own policy)
        settings.setdefault("VISOSWAP_PROFILE", "0")
    return settings


def render(script: str, **cfg) -> str:
    if "__MERGE__" in script:
        script = script.replace("__MERGE__", inspect.getsource(merge_settings))
    return script.replace("__CFG__", json.dumps(cfg))
