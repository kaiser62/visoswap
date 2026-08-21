"""Shared fixtures for the VisoSwap test suite.

The Qt-reachability gate runs on a *different* interpreter than pytest does.
pytest runs on whatever interpreter the developer invoked; the probe runs on the
engine interpreter, which is the only one carrying torch/torchvision/kornia/
onnxruntime -- and, usefully, PySide6. Proving vendored code cannot reach Qt on
an interpreter where Qt *is installed* is a strictly stronger result than proving
it on one where Qt is simply absent.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE = Path(__file__).resolve().parent / "_qt_guard_probe.py"

#: CPython 3.10.13 with torch 2.4.1+cu124, torchvision, kornia, onnxruntime,
#: tensorrt -- and PySide6 6.7.2.
DEFAULT_ENGINE_PYTHON = Path("D:/Visomaster/dependencies/Python/python.exe")

ENGINE_PYTHON_ENV_VAR = "VISOSWAP_ENGINE_PYTHON"


def resolve_engine_python() -> Path:
    """$VISOSWAP_ENGINE_PYTHON, else the known engine interpreter, else ours."""
    override = os.environ.get(ENGINE_PYTHON_ENV_VAR)
    if override:
        return Path(override)
    if DEFAULT_ENGINE_PYTHON.exists():
        return DEFAULT_ENGINE_PYTHON
    return Path(sys.executable)


@pytest.fixture(scope="session")
def engine_python() -> Path:
    return resolve_engine_python()


def run_probe(engine_python, modules) -> tuple[int, str]:
    """Run the Qt-guard probe on ``engine_python``. Returns (exit_code, stdout).

    Anything the interpreter writes to stderr is appended to the returned text
    so a crash before the probe's own reporting can still be attributed.
    """
    proc = subprocess.run(
        [str(engine_python), str(PROBE), *modules],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if stderr:
        stdout = (stdout + "\n[stderr] " + stderr).strip()
    return proc.returncode, stdout
