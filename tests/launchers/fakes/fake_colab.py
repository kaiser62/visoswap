#!/usr/bin/env python3
"""Fake `colab` CLI. State in $FAKE_DIR: sessions.txt (lines as the real CLI prints), calls.log."""
import json, os, sys
d = os.environ["FAKE_DIR"]
args = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(" ".join(args) + "\n")
sf = os.path.join(d, "sessions.txt")
cmd = args[0] if args else ""
if cmd == "sessions":
    if os.path.exists(sf): print(open(sf).read(), end="")
elif cmd == "new":
    if os.environ.get("FAKE_NEW_FAIL"): print("Error: Service Unavailable"); sys.exit(1)
    if os.environ.get("FAKE_NEW_REJECT"):
        print("Error: Backend rejected accelerator L4: quota or entitlement. Try a different one (e.g. --gpu T4)."); sys.exit(1)
    gpu = args[args.index("--gpu") + 1] if "--gpu" in args else "T4"
    name = args[args.index("-s") + 1] if "-s" in args else gpu.lower()
    open(sf, "w").write(f"[{name}] gpu-{gpu.lower()}-fake | Hardware: {gpu} | Shape: Standard | Variant: GPU\n")
elif cmd == "exec":
    script = open(args[args.index("-f") + 1]).read()
    if "VS_PROBE" in script:
        p = {"gpu": "Tesla T4", "deps_missing": [], "repo": True, "sha": "abc1234", "models": 7, "cloudflared": True,
             "token_file": bool(os.environ.get("FAKE_TOKEN_FILE")), "uvicorn": False, "tunnel": False, "health": False}
        print("VS_PROBE " + json.dumps(p)); print("VS_OK probe")
    else:
        for step in ("deps", "repo", "models", "start", "logs"):
            if f'"VS_OK {step}"' in script or f"VS_OK {step}" in script:
                print(f"VS_OK {step}")
elif cmd == "usage":
    if os.environ.get("FAKE_USAGE_LOGIN") and os.environ.get("FAKE_COLAB_EXPIRED"):   # the user finished the login flow
        open(os.path.join(d, "logged_in"), "w").write("1")
    if os.path.exists(os.path.join(d, "logged_in")) or not os.environ.get("FAKE_COLAB_EXPIRED"):
        print("Current balance: %s compute units\nUsage rate: 0.00/hr\nActive assignments: 1" % os.environ.get("FAKE_UNITS", "12.50"))
    else:
        print("To authorize colab-cli, visit this URL:\n  https://accounts.google.com/o/oauth2/auth?client_id=abc&redirect_uri=urn%3Aietf&scope=x&state=s1", file=sys.stderr, flush=True)
        _code = sys.stdin.readline().strip()   # piped by the relay login; EOF (no stdin) -> fails like the real CLI
        if _code == "GOODCODE-123456789":
            open(os.path.join(d, "logged_in"), "w").write("1")
            print("Current balance: 12.50 compute units\nUsage rate: 0.00/hr")
            sys.exit(0)
        print("EOFError" if not _code else "invalid_grant", file=sys.stderr); sys.exit(1)
elif cmd in ("upload", "stop"):
    print("ok")
