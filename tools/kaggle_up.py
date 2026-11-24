#!/usr/bin/env python3
"""Compatibility wrapper: the Kaggle launcher now lives in tools/launchers/kaggle_launch.py.

Old flags keep working (--restart, --concurrency N, --profile, --max-minutes N, --down). As before,
--concurrency/--profile/--max-minutes imply --restart (they only take effect on a new kernel).
The URL is still written to ~/.visoswap/backend_url (and ~/.visoswap/kaggle_url).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "launchers"))
import kaggle_launch  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if any(a.startswith(("--concurrency", "--profile", "--max-minutes")) for a in argv) and "--restart" not in argv:
        argv.append("--restart")
    if "--down" in argv and "--yes" not in argv and "-y" not in argv:
        argv.append("--yes")  # the old script deleted without asking
    sys.exit(kaggle_launch.main(argv))
