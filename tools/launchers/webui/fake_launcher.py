"""LAUNCHER_FAKE=1 stand-in for kaggle_launch.py / colab_launch.py: same flags, same JSON-lines protocol, no network, no
credentials. STATEFUL: launch/restart/stop change what --status reports (state file $VISOSWAP_HOME/fake_state_<target>.json).

Env switches (all optional):
  FAKE_NEEDS_LOGIN=1     launch and --check fail until a (fake) login succeeds
  FAKE_FORCE_STATE=      queued | error | session_only | down: what --status reports regardless of launches (screenshots, tests)
  FAKE_GPU_LEFT=18.92    Kaggle GPU hours remaining      FAKE_UNITS=233.55  Colab compute units      FAKE_QUOTA_FAIL=1
  FAKE_LAUNCH_FAIL=1     launch fails                    FAKE_LOGS_FAIL=1   remote logs unavailable
"""
import json, os, sys, time

target, args = sys.argv[1], sys.argv[2:]
home = os.environ.get("VISOSWAP_HOME", ".")
out = lambda **o: print(json.dumps(o), flush=True)
url = f"https://{target}.example.org"
AUTH_ID = "kaggle.auth" if target == "kaggle" else "colab.login"   # same ids as the real launchers
SF = os.path.join(home, f"fake_state_{target}.json")


def load():
    try:
        return json.load(open(SF))
    except (OSError, ValueError):
        return {}


def save(d):
    os.makedirs(home, exist_ok=True)
    json.dump(d, open(SF, "w"))


def val(flag, default=None):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default


def logged_in():
    return not os.environ.get("FAKE_NEEDS_LOGIN") or os.path.exists(os.path.join(home, f"fake_login_{target}"))


def mark_login():
    os.makedirs(home, exist_ok=True)
    open(os.path.join(home, f"fake_login_{target}"), "w").write("1")


if "--check" in args:
    ok = logged_in()
    out(t="result", ok=True, summary="ready", checks=[
        {"id": AUTH_ID, "service": f"{target} login", "state": "ok" if ok else "invalid", "detail": "credentials valid" if ok else "not logged in",
         "fix": "" if ok else "log in", "runnable": not ok, "blocking": True},
        {"id": "cloudflare", "service": "Cloudflare tunnel", "state": "missing", "detail": "no token stored", "fix": f"{target}-launch --setup", "runnable": True, "blocking": False}])
elif "--login" in args and "--relay" in args:
    colab = target == "colab"
    extra = {"url": os.environ.get("FAKE_LOGIN_URL", "https://accounts.google.com/o/oauth2/auth?client_id=abc&state=s1")} if colab else {}
    out(t="prompt", kind="code" if colab else "secret", msg="paste the code" if colab else "paste the token", **extra)
    line = sys.stdin.readline().strip()
    ok = line.startswith("GOOD")
    if ok:
        mark_login()
    out(t="log", msg="got " + line)      # a secret echoed by a careless tool must still be redacted
    out(t="result", ok=ok, summary="login valid (fake)" if ok else "", error="" if ok else "rejected")
elif "--login" in args:
    out(t="result", ok=True, summary="login valid (fake)")
elif "--cf-api-token-stdin" in args:
    tok = sys.stdin.readline().strip()
    out(t="result", ok=tok.startswith("cfapi"), summary="tunnel and DNS ready (fake)", error="" if tok.startswith("cfapi") else "rejected", token_echo=tok)
elif "--store-cf-token" in args:
    tok = sys.stdin.readline().strip()
    out(t="result", ok=len(tok) >= 40, summary="token stored (fake)" if len(tok) >= 40 else "", error="" if len(tok) >= 40 else "does not look like a token")
elif "--kaggle-json-stdin" in args or "--colab-token-stdin" in args:
    raw = sys.stdin.read()
    try:
        d = json.loads(raw)
        good = str(d.get("key", d.get("refresh_token", ""))).startswith("GOOD")
    except ValueError:
        good = False
    if good:
        mark_login()
    out(t="log", msg="received " + raw.strip())      # echoed on purpose: must be redacted
    out(t="result", ok=good, summary="login stored (fake)" if good else "", error="" if good else "the CLI rejected that file")
elif "--quota" in args:
    st = load()
    if os.environ.get("FAKE_QUOTA_FAIL"):
        out(t="result", ok=True, quota=None, summary="quota unavailable (fake)")
    elif target == "kaggle":
        rem = float(os.environ.get("FAKE_GPU_LEFT", "18.92"))
        out(t="result", ok=True, quota={"gpu": {"used": round(30 - rem, 2), "remaining": rem, "total": 30.0, "refresh_at": "2026-11-28T00:00:00+00:00"},
                                         "tpu": {"used": 0.0, "remaining": 20.0, "total": 20.0, "refresh_at": "2026-11-28T00:00:00+00:00"}}, summary=f"GPU {rem}h left of 30.0h")
    elif not logged_in():
        out(t="result", ok=True, quota=None, login_required=True, summary="no Colab quota: no Google login stored")
    else:
        up = st.get("state") == "RUNNING"
        out(t="result", ok=True, quota={"balance": float(os.environ.get("FAKE_UNITS", "233.55")), "rate": 1.9 if up else 0.0, "assignments": 1 if up else 0}, summary="compute units")
elif "--logs" in args:
    n = int(val("--lines", "60"))
    if os.environ.get("FAKE_LOGS_FAIL") or not load():
        out(t="result", ok=False, error="log unavailable: nothing running (fake)")
    else:
        src = val("--logs", "kernel") if target == "colab" else "kernel"
        lines = [f"[{src}] line {i} ok" for i in range(300)] + [f"[{src}] CF_TUNNEL_TOKEN=fakeSecretValue123", f"[{src}] auth eyJhbGciOiJIUzI1NiJ9abcdefghijklmnopqrstuv", f"[{src}] VISOSWAP_API_KEY=fakeAccessKey456"]
        out(t="result", ok=True, lines=lines[-n:], summary=f"{min(n, len(lines))} lines")
elif "--status" in args:
    st, force = load(), os.environ.get("FAKE_FORCE_STATE", "").lower()
    up = st.get("state") == "RUNNING"
    now = time.time()
    if force == "error":
        out(t="result", ok=False, error="could not read the status (fake)", next="check the login")
    elif target == "kaggle":
        state = {"queued": "QUEUED", "down": "MISSING"}.get(force) or ("RUNNING" if up else "MISSING")
        run = state == "RUNNING"
        out(t="result", ok=True, state=state, healthy=run, url=url if run else url, kernel="fakeuser/visoswap-studio", configured=True,
            started_at=(st.get("t", now) if run else None), started_source="fake lastRunTime" if run else "", summary=f"fake kernel: {state}")
    else:
        state = {"session_only": "SESSION_ONLY", "down": "NO_SESSION"}.get(force) or ("RUNNING" if up else "NO_SESSION")
        run = state == "RUNNING"
        gpu = {"L4": "NVIDIA L4", "T4": "Tesla T4"}.get(st.get("gpu", "L4"), "NVIDIA L4")
        out(t="result", ok=True, state=state, healthy=run, url=url, session="l4" if state != "NO_SESSION" else None, gpu=gpu if state != "NO_SESSION" else None,
            uptime_s=int(now - st.get("t", now)) if run else None, settings={"VISOSWAP_PROCESS_WORKERS": "1", "GENERATION_CONCURRENCY": st.get("conc", "3")} if state != "NO_SESSION" else {},
            configured=True, summary=f"fake session: {state}")
elif "--down" in args:
    out(t="step", msg="stopping (fake)")
    try:
        os.remove(SF)
    except OSError:
        pass
    out(t="result", ok=True, state="MISSING", summary="stopped (fake)")
elif not logged_in():
    out(t="result", ok=False, error=f"{target} login: not logged in", next="log in",
        checks=[{"id": AUTH_ID, "service": f"{target} login", "state": "invalid", "detail": "not logged in", "fix": "log in", "runnable": True, "blocking": True, "extra": "x"}])
elif os.environ.get("FAKE_LAUNCH_FAIL"):
    out(t="step", msg="starting (fake)")
    out(t="result", ok=False, error="fake launch failure", next="see the job log")
else:
    for i in range(3):
        out(t="step" if i == 0 else "log", msg=f"fake step {i} FAKE_TOKEN=eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop")
        time.sleep(0.3)
    save({"state": "RUNNING", "t": time.time(), "flags": [a for a in args if a not in ("--json",)], "gpu": val("--gpu", "L4"), "conc": val("--concurrency", "3")})
    out(t="result", ok=True, state="RUNNING", healthy=True, url=url, mobile_url=url + "/mobile")
