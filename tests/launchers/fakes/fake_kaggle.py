#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
st = os.environ.get("FAKE_KSTATE", "MISSING")
mode = os.environ.get("FAKE_KAUTH", "ok")
if a[:2] == ["auth", "login"]:
    open(os.path.join(d, "kaggle_ok"), "w").write("1"); print("Logged in"); sys.exit(0)
if a[:2] == ["kernels", "list"]:
    open(os.path.join(d, "env.log"), "a").write("%s|%s|%s\n" % (os.environ.get("HOME"), os.environ.get("KAGGLE_CONFIG_DIR"), "token" if os.environ.get("KAGGLE_API_TOKEN") else "-"))
    _tok = os.environ.get("KAGGLE_API_TOKEN", "")
    _f = os.path.join(os.environ.get("KAGGLE_CONFIG_DIR") or os.path.expanduser("~/.kaggle"), "access_token")
    if not _tok and os.path.exists(_f):
        _tok = open(_f).read().strip()
    if _tok:   # a pasted token wins over any other stored login, like the real CLI
        if not _tok.startswith("GOOD"):
            print("401 Unauthorized: Authentication required"); sys.exit(1)
        print("ref title"); sys.exit(0)
if a[:2] == ["kernels", "list"] and mode == "expired" and not os.path.exists(os.path.join(d, "kaggle_ok")):
    print("Authentication required to call the Kaggle API."); sys.exit(1)
if a[:2] == ["kernels", "list"] and mode == "flaky":
    print("Temporary failure in name resolution"); sys.exit(1)
if a == ["quota"]:
    if os.environ.get("FAKE_QUOTA_FAIL"): print("Quota unavailable"); sys.exit(1)
    rem = os.environ.get("FAKE_GPU_LEFT", "18.92")
    print("resource  used    remaining  total   refreshAt\n--------  ------  ---------  ------  -------------------------\n"
          "GPU       %.2fh  %sh     30.00h  2026-11-28T00:00:00+00:00\nTPU       0.00h   20.00h     20.00h  2026-11-28T00:00:00+00:00" % (30 - float(rem), rem)); sys.exit(0)
if a[:2] == ["config", "view"]:
    if os.environ.get("FAKE_NO_USER"): print("Configuration values from /x\n- username: None"); sys.exit(0)
    print("Configuration values from /x\n- username: faker\n- auth_method: OAUTH"); sys.exit(0)
if a[:2] == ["kernels", "logs"]:
    if st in ("MISSING", "AUTH"): print("Kernel was denied"); sys.exit(1)
    print("[kernel] boot ok\n[kernel] CF_TUNNEL_TOKEN=%s\n[kernel] bearer eyJhbGciOiJIUzI1NiJ9abcdefghijklmnopqrstuv\n[kernel] uvicorn up" % os.environ.get("FAKE_LOG_SECRET", "s3cretvalue123")); sys.exit(0)
if a[:2] == ["kernels", "list"] and "--format" in a:
    print("ref,title,author,lastRunTime,totalVotes\nfaker/visoswap-studio,t,A,2026-11-24 10:00:00.000000,0"); sys.exit(0)
if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY") and a[:2] == ["kernels", "list"]:   # kaggle.json style login
    if not os.environ["KAGGLE_KEY"].startswith("GOOD"): print("401 Unauthorized: Authentication required"); sys.exit(1)
    print("ref title"); sys.exit(0)
if a[:2] == ["kernels", "list"]:
    print("ref title" + ("\nfaker/visoswap-studio x" if os.environ.get("FAKE_KLISTED") else ""))
elif a[:2] == ["kernels", "status"]:
    if st == "AUTH": print("Authentication required"); sys.exit(1)
    if st == "MISSING": print("Kernel 'faker/visoswap-studio' was denied"); sys.exit(1)
    print(f'faker/visoswap-studio has status "KernelWorkerStatus.{st}"')
elif a[:2] == ["datasets", "status"]:
    print("ready")
elif a[:2] == ["kernels", "push"]:
    print("Kernel version 1 successfully pushed")
else:
    print("ok")
