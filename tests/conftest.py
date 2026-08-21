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

#: Vendored modules allowed to fail the Qt gate because they have not been
#: de-Qt'd yet.
#:
#: **Closed by plan 01-04 on 2026-08-21. This set is empty, and an empty set is
#: Phase 1's actual pass condition.** Plan 01-02 put two modules here, plan 01-03
#: landed ``models_processor``, plan 01-04 landed ``workers.frame_worker`` -- the
#: last one. Nothing under ``visoswap/`` is excluded from either gate any more.
#:
#: This set is the only thing standing between a green suite and an unproven
#: module, so a green run with entries here proves strictly less than it looks
#: like it does. Adding an entry is legitimate only while a newly vendored file
#: is mid-strip, and it must come back out in the same plan; anything still here
#: at the end of a plan is a module the gates never looked at.
#:
#: Both the import gate and the static source scan read this one set, so a module
#: can never be excluded from one and not the other. Everything else is discovered
#: by walking the tree, which is what makes forgetting to cover a newly vendored
#: file impossible rather than merely unlikely.
PENDING_QT_STRIP = frozenset()

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


def importable_name_for(path) -> str:
    """The dotted name the probe can actually import.

    A module maps to its own name; an ``__init__.py`` maps to the *package* it
    defines, since ``import visoswap.processors.__init__`` is not how anyone
    imports a package.
    """
    relative = Path(path).resolve().relative_to(REPO_ROOT).with_suffix("")
    parts = relative.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def vendored_packages() -> list[str]:
    """Every package under ``visoswap/``, by the name a caller would import."""
    return sorted(
        {
            importable_name_for(path)
            for path in vendored_sources()
            if path.name == "__init__.py"
        }
    )


def vendored_modules() -> list[str]:
    """The importable names the Qt probe is handed: packages *and* modules.

    The roadmap's success criterion is written as ``import visoswap.processors``.
    Taken literally that command is close to meaningless here, and dangerously
    so: ``visoswap/processors/__init__.py`` is deliberately import-free, so the
    import succeeds having loaded an empty file and touched none of the vendored
    code. It would exit 0 over a tree riddled with Qt. That is threat T-01-13.

    The fix is *not* to make ``__init__.py`` import its submodules -- eagerly
    pulling torch, onnxruntime and tensorrt into every ``import visoswap`` is the
    wrong default for a library, and it would drag any future mid-strip module
    into the gate before its plan is finished. The fix is here: hand the probe the
    package names **and** every discovered submodule, in one run, so the literal
    roadmap command is covered *and* so is everything it would have missed.

    Do not "simplify" this back to the package name. That is the hollow pass this
    function exists to prevent.
    """
    return sorted({importable_name_for(path) for path in vendored_sources()})


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
