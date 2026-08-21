"""The Qt-reachability gate.

Phase 1's entire success criterion is an import proof: vendored engine code must
not be able to reach a Qt binding. Nothing in this file may ``skip``. A skip here
is a false green on the one thing this phase exists to demonstrate -- the suite
would report success while proving nothing at all.
"""

import subprocess

from tests.conftest import ENGINE_PYTHON_ENV_VAR, run_probe

VENDORED_MODULE = "visoswap.processors.utils.faceutil"


def _interpreter_hint(engine_python) -> str:
    return (
        "resolved engine interpreter: {}\n"
        "override it with the {} environment variable.".format(
            engine_python, ENGINE_PYTHON_ENV_VAR
        )
    )


def test_engine_python_has_runtime_deps(engine_python):
    """The gate's verdict is only worth as much as the interpreter it runs on.

    If torch is missing, every probe run dies on ``import torch`` long before it
    could reach Qt -- and a gate that cannot reach Qt because the import died
    early is not evidence of anything. Fail here, loudly and attributably,
    rather than letting a missing dependency read as a Qt-cleanliness pass.
    """
    proc = subprocess.run(
        [str(engine_python), "-c", "import torch; print(torch.__version__)"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "the engine interpreter cannot import torch, so the Qt-reachability gate "
        "cannot produce a meaningful verdict.\n"
        + _interpreter_hint(engine_python)
        + "\nstdout: {}\nstderr: {}".format(proc.stdout.strip(), proc.stderr.strip())
    )


def test_vendored_module_imports_without_qt(engine_python):
    """The vendored module imports with every Qt binding root blocked."""
    code, out = run_probe(engine_python, [VENDORED_MODULE])

    assert code != 2, (
        "the probe could not import {} because an *engine* dependency is missing, "
        "not because of Qt. This is not a Qt-cleanliness result.\n"
        "probe output: {}\n".format(VENDORED_MODULE, out)
        + _interpreter_hint(engine_python)
    )
    assert code != 1, (
        "{} reached a Qt binding with the Qt roots blocked.\n"
        "probe output: {}".format(VENDORED_MODULE, out)
    )
    assert code == 0, (
        "the probe failed to import {} for a non-Qt reason.\n"
        "exit code: {}\nprobe output: {}\n".format(VENDORED_MODULE, code, out)
        + _interpreter_hint(engine_python)
    )
    assert out.splitlines()[-1] == "CLEAN", (
        "probe exited 0 but did not report CLEAN: {}".format(out)
    )


def test_probe_blocker_is_armed(engine_python):
    """Handed a Qt module directly, the probe must refuse it.

    Without this, an inert blocker -- a typo in BLOCKED_ROOTS, a finder that
    silently returns None -- would make every other test in this file pass while
    testing nothing. The gate has to be shown capable of failing.
    """
    code, out = run_probe(engine_python, ["PySide6.QtCore"])

    assert code == 1, (
        "the Qt blocker did not trip on a direct PySide6.QtCore import, so it is "
        "inert and the rest of this file proves nothing.\n"
        "exit code: {}\nprobe output: {}\n".format(code, out)
        + _interpreter_hint(engine_python)
    )
    assert out.startswith("QT_REACHED:PySide6.QtCore:"), (
        "expected a QT_REACHED report naming the module, got: {}".format(out)
    )
