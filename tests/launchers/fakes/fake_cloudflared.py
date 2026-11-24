#!/usr/bin/env python3
"""Fake cloudflared: noisy on stderr (like the real one) so stdout-only parsing is exercised."""
import json, os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
print("2026-01-01T00:00:00Z WRN Your version is outdated {not json}", file=sys.stderr)
if a[:2] == ["tunnel", "list"]:
    # the real CLI prints Go's zero time for live tunnels; FAKE_CF_DELETED marks the tunnel as really deleted
    deleted = "2026-01-01T00:00:00Z" if os.environ.get("FAKE_CF_DELETED") else "0001-01-01T00:00:00Z"
    print("not json" if os.environ.get("FAKE_CF_BAD_LIST") else json.dumps([{"id": "tid", "name": "visoswap", "deleted_at": deleted}]))
elif a[:2] == ["tunnel", "token"]:
    print("short" if os.environ.get("FAKE_CF_BAD_TOKEN") else "eyJhIjoiZmFrZXRva2VuMiIsInQiOiJmYWtlLXR1bm5lbC1pZC0xMjM0NTYiLCJzIjoiYWJjZGVmZ2hpamtsbW5vcCJ9")
else:
    print("ok")
