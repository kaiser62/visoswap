"""Run a launcher as a subprocess (shared by the web UI and the host bridge). Reads its environment lazily."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
TARGET_SCRIPTS = {"kaggle": "kaggle_launch.py", "colab": "colab_launch.py"}


def launcher_dir() -> Path:
    return Path(os.environ.get("LAUNCHER_DIR") or HERE.parent)


def fake() -> bool:
    return os.environ.get("LAUNCHER_FAKE") == "1"


def launcher_argv(target: str, flags: List[str]) -> List[str]:
    if fake():
        return [sys.executable, str(HERE / "fake_launcher.py"), target, *flags, "--json"]
    return [sys.executable, str(launcher_dir() / TARGET_SCRIPTS[target]), *flags, "--json", "--no-clipboard"]


def spawn_local(target: str, flags: List[str], stdin_text: Optional[str] = None, keep_stdin: bool = False) -> subprocess.Popen:
    """stdin: the secret (then EOF), or an open pipe for the relay login, or nothing at all."""
    p = subprocess.Popen(launcher_argv(target, flags), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         stdin=subprocess.PIPE if (stdin_text or keep_stdin) else subprocess.DEVNULL,
                         env={**os.environ, "PYTHONUNBUFFERED": "1"})
    if stdin_text:
        try:
            p.stdin.write(stdin_text)
            p.stdin.close()
        except OSError:
            pass
    return p
