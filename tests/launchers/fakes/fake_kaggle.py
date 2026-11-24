#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
st = os.environ.get("FAKE_KSTATE", "MISSING")
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
