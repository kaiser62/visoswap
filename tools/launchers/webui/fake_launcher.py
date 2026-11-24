"""LAUNCHER_FAKE=1 stand-in for kaggle_launch.py / colab_launch.py: same flags, same JSON-lines protocol,
no network, no credentials. Used to exercise the web UI."""
import json, os, sys, time

target, args = sys.argv[1], sys.argv[2:]
out = lambda **o: print(json.dumps(o), flush=True)
url = f"https://{target}.example.org"
if "--check" in args:
    out(t="result", ok=True, summary="ready", checks=[
        {"id": f"{target}.auth", "service": f"{target} login", "state": "ok", "detail": "credentials valid", "fix": "", "runnable": False, "blocking": True},
        {"id": "cloudflare", "service": "Cloudflare tunnel", "state": "missing", "detail": "no token stored", "fix": f"{target}-launch --setup", "runnable": True, "blocking": False}])
elif "--login" in args and "--relay" in args:
    colab = target == "colab"
    extra = {"url": os.environ.get("FAKE_LOGIN_URL", "https://accounts.google.com/o/oauth2/auth?client_id=abc&state=s1")} if colab else {}
    out(t="prompt", kind="code" if colab else "secret", msg="paste the code" if colab else "paste the token", **extra)
    line = sys.stdin.readline().strip()
    ok = line.startswith("GOOD")
    if ok:
        open(os.path.join(os.environ.get("VISOSWAP_HOME", "."), f"fake_login_{target}"), "w").write("1")
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
elif "--status" in args:
    out(t="result", ok=True, state="RUNNING", healthy=True, url=url, summary=f"fake {target} running")
elif "--down" in args:
    out(t="step", msg="stopping (fake)"); out(t="result", ok=True, state="MISSING", summary="stopped (fake)")
elif os.environ.get("FAKE_NEEDS_LOGIN") and not os.path.exists(os.path.join(os.environ.get("VISOSWAP_HOME", "."), f"fake_login_{target}")):
    out(t="result", ok=False, error=f"{target} login: not logged in", next="log in",
        checks=[{"id": f"{target}.auth", "service": f"{target} login", "state": "invalid", "detail": "not logged in", "fix": "log in", "runnable": True, "blocking": True, "extra": "x"}])
else:
    for i in range(3):
        out(t="step" if i == 0 else "log", msg=f"fake step {i} FAKE_TOKEN=eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop")
        time.sleep(0.3)
    out(t="result", ok=True, state="RUNNING", healthy=True, url=url, mobile_url=url + "/mobile")
