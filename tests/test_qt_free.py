"""The Qt-reachability gate.

Phase 1's entire success criterion is an import proof: vendored engine code must
not be able to reach a Qt binding. Nothing in this file may ``skip``. A skip here
is a false green on the one thing this phase exists to demonstrate -- the suite
would report success while proving nothing at all.

The module list is *discovered*, not written down. A hardcoded list silently
under-covers the moment a file is added: the new module is not probed, nothing
fails, and the gate reports green over code it never looked at. Discovery plus an
explicit, commented ``PENDING_QT_STRIP`` inverts that -- coverage is the default
and every exception is a line someone had to write on purpose.
"""

import subprocess

from tests.conftest import (
    ENGINE_PYTHON_ENV_VAR,
    PENDING_QT_STRIP,
    run_probe,
    vendored_modules,
)


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


def test_gate_discovers_the_vendored_tree():
    """Guard against the gate passing because discovery found nothing.

    ``assert code == 0`` on an empty argv list is not a pass, it is a probe that
    was asked to import nothing. This test is the thing that should say so.
    """
    modules = vendored_modules()
    assert modules, (
        "no vendored modules discovered under visoswap/ -- the Qt gate would "
        "pass vacuously."
    )
    for pending in PENDING_QT_STRIP:
        assert pending not in modules, (
            "{} is in PENDING_QT_STRIP but was still handed to the probe".format(
                pending
            )
        )


def test_every_vendored_module_imports_without_qt(engine_python):
    """Every vendored module imports with all seven Qt binding roots blocked."""
    modules = vendored_modules()
    assert modules, "no vendored modules discovered under visoswap/"

    code, out = run_probe(engine_python, modules)

    covered = "{} modules probed in one run:\n  {}".format(
        len(modules), "\n  ".join(modules)
    )

    assert code != 2, (
        "the probe could not import a vendored module because an *engine* "
        "dependency is missing, not because of Qt. This is not a Qt-cleanliness "
        "result.\nprobe output: {}\n{}\n".format(out, covered)
        + _interpreter_hint(engine_python)
    )
    assert code != 1, (
        "a vendored module reached a Qt binding with the Qt roots blocked.\n"
        "probe output: {}\n{}".format(out, covered)
    )
    assert code == 0, (
        "the probe failed to import a vendored module for a non-Qt reason.\n"
        "exit code: {}\nprobe output: {}\n{}\n".format(code, out, covered)
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
