"""LAUNCHER_FAKE=1 stand-in for kaggle_launch.py / colab_launch.py: same flags, same JSON-lines protocol,
no network, no credentials. Used to exercise the web UI."""
import json, sys, time

target, args = sys.argv[1], sys.argv[2:]
out = lambda **o: print(json.dumps(o), flush=True)
url = f"https://{target}.example.org"
if "--check" in args:
    out(t="result", ok=True, summary="ready", checks=[
        {"id": f"{target}.auth", "service": f"{target} login", "state": "ok", "detail": "credentials valid", "fix": "", "runnable": False, "blocking": True},
        {"id": "cloudflare", "service": "Cloudflare tunnel", "state": "missing", "detail": "no token stored", "fix": f"{target}-launch --setup", "runnable": True, "blocking": False}])
elif "--login" in args:
    out(t="result", ok=True, summary="login valid (fake)")
elif "--store-cf-token" in args:
    tok = sys.stdin.readline().strip()
    out(t="result", ok=len(tok) >= 40, summary="token stored (fake)" if len(tok) >= 40 else "", error="" if len(tok) >= 40 else "does not look like a token")
elif "--status" in args:
    out(t="result", ok=True, state="RUNNING", healthy=True, url=url, summary=f"fake {target} running")
elif "--down" in args:
    out(t="step", msg="stopping (fake)"); out(t="result", ok=True, state="MISSING", summary="stopped (fake)")
else:
    for i in range(3):
        out(t="step" if i == 0 else "log", msg=f"fake step {i} FAKE_TOKEN=eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop")
        time.sleep(0.3)
    out(t="result", ok=True, state="RUNNING", healthy=True, url=url, mobile_url=url + "/mobile")
