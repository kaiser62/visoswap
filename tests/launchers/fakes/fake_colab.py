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
    open(sf, "w").write("[t4] gpu-t4-fake | Hardware: T4 | Shape: Standard | Variant: GPU\n")
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
elif cmd in ("upload", "stop"):
    print("ok")
