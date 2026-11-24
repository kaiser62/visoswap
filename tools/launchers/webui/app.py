"""VisoSwap launcher web UI: trigger / monitor the Kaggle and Colab launchers from any device.

It runs the SAME launcher scripts (`--json` mode) as subprocesses; no launcher logic lives here.
Security model: see docs/launchers.md. In short: bearer token required for everything under /api,
fixed (target, action) allowlist, one job per target, rate limits, secrets redacted from all output,
no endpoint ever returns env-file contents.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
LAUNCHER_DIR = Path(os.environ.get("LAUNCHER_DIR") or HERE.parent)
sys.path.insert(0, str(LAUNCHER_DIR))
from vs_common import Redactor, load_env_file, secrets_of, visoswap_home  # noqa: E402

FAKE = os.environ.get("LAUNCHER_FAKE") == "1"
TARGETS = {"kaggle": "kaggle_launch.py", "colab": "colab_launch.py"}
ACTIONS = {"launch": [], "restart": ["--restart"], "stop": ["--down", "--yes"], "provision": ["--setup", "--yes"], "login": ["--login"]}
IN_DOCKER = os.environ.get("WEBUI_IN_DOCKER") == "1"
#: server-side constants shown when a login cannot run from the UI (browser/Keychain/terminal needed)
LOGIN_CMD = {"kaggle": "kaggle auth login --force", "colab": "colab usage"}
CHECK_FIELDS = ("id", "service", "state", "detail", "fix", "runnable", "blocking")
RESULT_FIELDS = ("ok", "state", "healthy", "url", "mobile_url", "summary", "error", "next", "session", "gpu", "kernel",
                 "configured", "log_url", "tunnel")
MAX_LINES = 2000
STATUS_TTL = 20.0


def token_path() -> Path:
    return visoswap_home() / "webui_token"


def load_or_create_token() -> str:
    p = token_path()
    if p.is_file():
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    # shown ONCE, here only; it is never returned by any endpoint
    print(f"\n  VisoSwap launcher web UI access token (also in {p}):\n  {tok}\n", flush=True)
    return tok


class State:
    def __init__(self):
        self.token = load_or_create_token()
        self.lock = threading.Lock()
        self.jobs: Dict[str, dict] = {}
        self.busy: Dict[str, str] = {}              # target -> job id
        self.status_cache: Dict[str, tuple] = {}    # target -> (time, data)
        self.fail_times: Dict[str, Deque[float]] = {}
        self.action_times: Deque[float] = deque()

    def redactor(self) -> Redactor:
        r = Redactor([self.token])
        for t in TARGETS:
            for s in secrets_of(load_env_file(visoswap_home() / f"{t}.env")):
                r.add(s)
        return r


S: Optional[State] = None


def state() -> State:
    global S
    if S is None:
        S = State()
    return S


def launcher_argv(target: str, extra: List[str]) -> List[str]:
    if FAKE:
        return [sys.executable, str(HERE / "fake_launcher.py"), target, *extra, "--json"]
    return [sys.executable, str(LAUNCHER_DIR / TARGETS[target]), *extra, "--json", "--no-clipboard"]


def clean_result(d: dict) -> dict:
    out = {k: d[k] for k in RESULT_FIELDS if k in d}
    if isinstance(d.get("checks"), list):   # nested whitelist
        out["checks"] = [{k: c[k] for k in CHECK_FIELDS if k in c} for c in d["checks"] if isinstance(c, dict)]
    return out


def run_launcher(target: str, extra: List[str], on_line=None, timeout: float = 4 * 3600, stdin_text: Optional[str] = None) -> dict:
    st = state()
    red = st.redactor()
    if stdin_text:
        red.add(stdin_text.strip())
    result: dict = {"ok": False, "error": "launcher produced no result"}
    p = subprocess.Popen(launcher_argv(target, extra), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    t0 = time.time()
    if stdin_text:
        try:
            p.stdin.write(stdin_text)
            p.stdin.close()
        except OSError:
            pass
    try:
        for raw in p.stdout:
            raw = raw.rstrip("\n")
            try:
                obj = json.loads(raw)
            except ValueError:
                obj = {"t": "log", "msg": raw}
            if obj.get("t") == "result":
                result = clean_result(json.loads(red(json.dumps(obj))))
            elif on_line:
                on_line(red(str(obj.get("msg", ""))), obj.get("t"))
            if time.time() - t0 > timeout:
                p.kill()
                result = {"ok": False, "error": "timed out"}
                break
    finally:
        p.wait()
    return result


def start_job(target: str, action: str) -> str:
    st = state()
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "target": target, "action": action, "state": "running", "lines": [], "result": None, "started": time.time()}
    st.jobs[jid] = job
    st.busy[target] = jid
    if len(st.jobs) > 50:
        for k in sorted(st.jobs, key=lambda k: st.jobs[k]["started"])[:10]:
            if st.jobs[k]["state"] != "running":
                st.jobs.pop(k, None)

    def work():
        try:
            def on_line(msg, t):
                job["lines"].append(("== " if t == "step" else "") + msg)
                del job["lines"][:-MAX_LINES]
            job["result"] = run_launcher(target, ACTIONS[action], on_line)
            job["state"] = "done" if job["result"].get("ok") else "failed"
        except Exception as e:  # never leak internals
            job["result"] = {"ok": False, "error": f"web UI error: {type(e).__name__}"}
            job["state"] = "failed"
        finally:
            with st.lock:
                st.busy.pop(target, None)
                st.status_cache.pop(target, None)
                st.status_cache.pop("check:" + target, None)
    threading.Thread(target=work, daemon=True).start()
    return jid


# --------------------------------------------------------------------------- app
app = FastAPI(title="VisoSwap launchers", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def require_auth(request: Request) -> None:
    st = state()
    ip = request.client.host if request.client else "?"
    now = time.time()
    fails = st.fail_times.setdefault(ip, deque())
    while fails and now - fails[0] > 300:
        fails.popleft()
    if len(fails) >= 10:
        raise HTTPException(429, "too many failed attempts; wait a few minutes")
    auth = request.headers.get("authorization", "")
    given = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not given or not hmac.compare_digest(given.encode(), st.token.encode()):
        fails.append(now)
        raise HTTPException(401, "invalid or missing token")


def _status(target: str, force: bool) -> dict:
    st = state()
    cached = st.status_cache.get(target)
    busy = st.busy.get(target)
    if cached and (busy or time.time() - cached[0] < (5 if force else STATUS_TTL)):
        return {**cached[1], "busy": bool(busy), "job": busy}
    if busy:
        return {"state": "BUSY", "summary": "an action is running", "busy": True, "job": busy}
    res = run_launcher(target, ["--status"], timeout=120)
    st.status_cache[target] = (time.time(), res)
    return {**res, "busy": False, "job": None}


@app.get("/api/targets", dependencies=[Depends(require_auth)])
def targets(refresh: int = 0):
    return {t: _status(t, bool(refresh)) for t in TARGETS}


@app.get("/api/checks", dependencies=[Depends(require_auth)])
def checks(refresh: int = 0):
    """Per-service login/token state (read-only `--check`), cached briefly. Returns whitelisted fields only."""
    st = state()
    out = {}
    for t in TARGETS:
        cached = st.status_cache.get("check:" + t)
        if cached and time.time() - cached[0] < (5 if refresh else STATUS_TTL):
            out[t] = cached[1]
            continue
        res = run_launcher(t, ["--check"], timeout=180)
        st.status_cache["check:" + t] = (time.time(), res)
        out[t] = res
    return {"targets": out, "login_commands": LOGIN_CMD, "in_docker": IN_DOCKER}


def _begin(target: str):
    st = state()
    with st.lock:
        now = time.time()
        while st.action_times and now - st.action_times[0] > 60:
            st.action_times.popleft()
        if len(st.action_times) >= 6:
            raise HTTPException(429, "too many actions; wait a minute")
        if target in st.busy:
            raise HTTPException(409, f"{target} already has a running action")
        st.action_times.append(now)
        st.busy[target] = "pending"


@app.post("/api/targets/{target}/cloudflare-token", dependencies=[Depends(require_auth)])
def cloudflare_token(target: str, body: dict):
    """Store a pasted tunnel token (mode 600, via the launcher's stdin). Never echoed; refused if one is stored."""
    if target not in TARGETS:
        raise HTTPException(404, "unknown target")
    tok = str((body or {}).get("token", "")).strip()
    if not (40 <= len(tok) <= 4096) or any(ch.isspace() for ch in tok):
        raise HTTPException(400, "does not look like a tunnel token")
    _begin(target)
    st = state()
    try:
        res = run_launcher(target, ["--store-cf-token"], timeout=60, stdin_text=tok + "\n")
    finally:
        st.busy.pop(target, None)
        st.status_cache.pop("check:" + target, None)
    return {"ok": bool(res.get("ok")), "error": res.get("error", "") if not res.get("ok") else ""}


@app.post("/api/targets/{target}/{action}", dependencies=[Depends(require_auth)])
def act(target: str, action: str, body: Optional[dict] = None):
    if target not in TARGETS or action not in ACTIONS:
        raise HTTPException(404, "unknown target or action")
    if action == "stop" and not (body or {}).get("confirm"):
        raise HTTPException(400, "stop requires {\"confirm\": true}")
    if action == "login" and (target == "colab" or IN_DOCKER):
        # needs a terminal / the host's browser or Keychain: show the exact command instead
        raise HTTPException(400, f"cannot run from here; run in a terminal: {LOGIN_CMD[target]}")
    st = state()
    _begin(target)
    try:
        return {"job": start_job(target, action)}
    except Exception:
        st.busy.pop(target, None)
        raise


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def job(job_id: str, since: int = 0):
    j = state().jobs.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    return {"id": j["id"], "target": j["target"], "action": j["action"], "state": j["state"],
            "lines": j["lines"][since:], "next": len(j["lines"]), "result": j["result"]}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.exception_handler(Exception)
async def boom(request, exc):
    return JSONResponse({"detail": "internal error"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    state()
    uvicorn.run(app, host=os.environ.get("WEBUI_HOST", "127.0.0.1"), port=int(os.environ.get("WEBUI_PORT", "8765")),
                log_level="warning", server_header=False)
