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
VISOSWAP_ROOT = REPO_ROOT / "visoswap"
PROBE = Path(__file__).resolve().parent / "_qt_guard_probe.py"

#: The only vendored modules allowed to fail the Qt gate, because they have not
#: been de-Qt'd yet. Plan 01-03 lands ``models_processor``; plan 01-04 lands
#: ``frame_worker`` and **empties this set** -- deleting these two lines is the
#: whole of that plan's gate work.
#:
#: Both the import gate and the static source scan read this one set, so a
#: module can never be excluded from one and not the other. Everything else is
#: discovered by walking the tree, which is what makes forgetting to cover a
#: newly vendored file impossible rather than merely unlikely.
PENDING_QT_STRIP = frozenset(
    {
        "visoswap.processors.models_processor",
        "visoswap.processors.workers.frame_worker",
    }
)

#: CPython 3.10.13 with torch 2.4.1+cu124, torchvision, kornia, onnxruntime,
#: tensorrt -- and PySide6 6.7.2.
DEFAULT_ENGINE_PYTHON = Path("D:/Visomaster/dependencies/Python/python.exe")

ENGINE_PYTHON_ENV_VAR = "VISOSWAP_ENGINE_PYTHON"


def module_name_for(path) -> str:
    """``visoswap/processors/face_masks.py`` -> ``visoswap.processors.face_masks``."""
    relative = Path(path).resolve().relative_to(REPO_ROOT).with_suffix("")
    return ".".join(relative.parts)


def vendored_sources() -> list[Path]:
    """Every ``.py`` file under ``visoswap/`` that the gates must cover.

    Includes ``__init__.py``: package scaffolding is source too, and a Qt import
    smuggled into an ``__init__`` would be the most damaging place to miss one.
    """
    return sorted(
        path
        for path in VISOSWAP_ROOT.rglob("*.py")
        if module_name_for(path) not in PENDING_QT_STRIP
    )


def vendored_modules() -> list[str]:
    """The importable module names the Qt probe should be handed.

    ``__init__.py`` files are dropped here -- importing a package is implied by
    importing anything inside it, and naming them separately would only make the
    probe's argv longer.
    """
    return sorted(
        module_name_for(path)
        for path in vendored_sources()
        if path.name != "__init__.py"
    )


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
