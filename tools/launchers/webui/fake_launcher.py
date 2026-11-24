"""LAUNCHER_FAKE=1 stand-in for kaggle_launch.py / colab_launch.py: same flags, same JSON-lines protocol,
no network, no credentials. Used to exercise the web UI."""
import json, sys, time

target, args = sys.argv[1], sys.argv[2:]
out = lambda **o: print(json.dumps(o), flush=True)
url = f"https://{target}.example.org"
if "--status" in args:
    out(t="result", ok=True, state="RUNNING", healthy=True, url=url, summary=f"fake {target} running")
elif "--down" in args:
    out(t="step", msg="stopping (fake)"); out(t="result", ok=True, state="MISSING", summary="stopped (fake)")
else:
    for i in range(3):
        out(t="step" if i == 0 else "log", msg=f"fake step {i} FAKE_TOKEN=eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop")
        time.sleep(0.3)
    out(t="result", ok=True, state="RUNNING", healthy=True, url=url, mobile_url=url + "/mobile")
