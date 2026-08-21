"""The engine runner's seal: armed, provably capable of failing, and not inert.

``tests/_engine_runner.py`` is the only thing in this project that executes
engine code, and it does so with three groups of package roots made unimportable:
the seven Qt bindings, VisoMaster's ``app`` package, and VisoSwap's ``backend``.

That third block is what turns the roadmap's Phase 2 criterion 3 from a text
search into a runtime proof. ``grep -rn "import backend" visoswap/`` says no line
imports it. The seal says the engine *cannot* import it. Both are asserted here,
because they fail in different ways: a grep misses an ``importlib`` call built
from a string, and a runtime seal misses a line nothing executes.

The tests are shaped by one lesson from plan 01-04: **a blocker is inert wherever
the thing it blocks is absent, and an inert blocker reports exactly the same
CLEAN as a working one.** So the runner puts a VisoMaster checkout on
``sys.path`` before sealing, and this file asserts that it genuinely resolved --
otherwise "VisoMaster could not be imported" would be a fact about the machine
rather than about the seal.
"""

import ast

from tests.conftest import (
    ALL_SEALED_ROOTS,
    BACKEND_ROOTS,
    QT_ROOTS,
    REPO_ROOT,
    SEALED_GROUPS,
    VISOMASTER_ROOTS,
    run_engine_runner,
    vendored_sources,
)
from tests.test_dropped_modules import FORBIDDEN_IMPORT_ROOTS

EXIT_CLEAN = 0
EXIT_SEAL_BREACHED = 1
EXIT_ASSET_MISSING = 3

#: One module per sealed group, each named the way real code would name it, so
#: the breach proof covers all three groups rather than generalising from Qt.
#: ``backend.api`` does not exist yet -- the seal refuses the *name* before any
#: path search, which is exactly the property that has to hold before Phase 5
#: creates the package.
BREACH_CASES = {
    "qt": "PySide6.QtCore",
    "visomaster": "app.ui.widgets.common_layout_data",
    "backend": "backend.api.projects",
}


def test_selftest_exits_clean_on_the_engine_interpreter(engine_python):
    """The seal arms, all three groups refuse when provoked, nothing leaks."""
    code, output = run_engine_runner(engine_python, ["--selftest"])
    assert code == EXIT_CLEAN, (
        "engine runner self-test failed on {} (exit {}):\n{}".format(
            engine_python, code, output
        )
    )
    assert output.startswith("CLEAN:selftest:"), (
        "expected one machine-readable CLEAN line, got:\n{}".format(output)
    )
    assert "groups=qt+visomaster+backend" in output, (
        "the self-test must provoke all three groups separately -- concluding "
        "'the seal is armed' from one group firing is how the other two end up "
        "unenforced.\n{}".format(output)
    )


def test_the_seal_is_not_inert_against_visomaster(engine_python):
    """VisoMaster must have been genuinely importable at the moment it was sealed.

    This is the whole difference between a proof and a coincidence. ``app`` is an
    implicit namespace package under ``D:/Visomaster``; the runner appends that
    checkout to ``sys.path`` before arming, so a refusal afterwards is the seal
    firing rather than the package never having been there.

    Qt's non-inertness is not re-proven here -- ``tests/test_qt_free.py`` already
    runs the Qt gate on an interpreter where PySide6 is installed, and this file
    would only be restating it. ``backend`` cannot be proven non-inert until
    Phase 5 creates the package; the runner reports its reachability rather than
    asserting it, so this test is honest about which of the three is which.
    """
    code, output = run_engine_runner(engine_python, ["--selftest"])
    assert code == EXIT_CLEAN, output
    assert "app=yes" in output, (
        "VisoMaster's `app` package did not resolve before the seal armed, so "
        "the visomaster seal proved nothing -- it refused a package that was "
        "not there. Point VISOMASTER_DIR at a real checkout.\n{}".format(output)
    )


def test_an_unsealed_import_is_reported_clean(engine_python):
    """The seal is a filter, not a blanket refusal.

    Without this, a runner that refused *every* import would pass every breach
    case below while proving nothing at all.
    """
    code, output = run_engine_runner(engine_python, ["--import", "json"])
    assert code == EXIT_CLEAN, output
    assert output.startswith("CLEAN:import:json:"), output


def test_the_runner_is_capable_of_reporting_a_breach(engine_python):
    """Exit 1 is demonstrated, never assumed.

    A gate nobody has watched fail is a gate whose state nobody knows. Each of
    the three groups is provoked with a module that real code would plausibly
    import.
    """
    failures = []
    for group, dotted in sorted(BREACH_CASES.items()):
        code, output = run_engine_runner(engine_python, ["--import", dotted])
        if code != EXIT_SEAL_BREACHED or not output.startswith("SEAL_BREACHED:"):
            failures.append(
                "  {} seal: importing {!r} gave exit {} / {!r}, expected exit "
                "{} and a SEAL_BREACHED line".format(
                    group, dotted, code, output, EXIT_SEAL_BREACHED
                )
            )
    assert not failures, "sealed imports that were not refused:\n" + "\n".join(failures)


def test_a_missing_fixture_reports_asset_missing_not_clean(engine_python, tmp_path):
    """A harness fault must never be readable as an engine pass.

    The settings fixture is the runner's only settings source. If it goes
    missing, the run has to stop with its own code -- reporting CLEAN over an
    absent fixture is precisely the false green the exit vocabulary exists to
    prevent.
    """
    absent = tmp_path / "no-such-fixture.json"
    code, output = run_engine_runner(
        engine_python,
        ["--selftest"],
        env={"VISOSWAP_SETTINGS_FIXTURE": str(absent)},
    )
    assert code == EXIT_ASSET_MISSING, (
        "expected exit {} for a missing fixture, got {}:\n{}".format(
            EXIT_ASSET_MISSING, code, output
        )
    )
    assert output.startswith("ASSET_MISSING:selftest:"), output


def test_the_static_scan_and_the_runtime_seal_block_the_same_roots():
    """The two mechanisms must not drift apart.

    ``test_dropped_modules`` scans source for forbidden imports; the runner
    refuses them at runtime. If one set grows a root and the other does not, the
    weaker mechanism is the one that keeps passing -- and it is the one everybody
    reads.
    """
    assert FORBIDDEN_IMPORT_ROOTS == VISOMASTER_ROOTS | BACKEND_ROOTS, (
        "the static scan's forbidden roots {} and the runtime seal's "
        "non-Qt roots {} have diverged".format(
            sorted(FORBIDDEN_IMPORT_ROOTS), sorted(VISOMASTER_ROOTS | BACKEND_ROOTS)
        )
    )
    assert QT_ROOTS <= ALL_SEALED_ROOTS
    assert dict(SEALED_GROUPS) == {
        "qt": QT_ROOTS,
        "visomaster": VISOMASTER_ROOTS,
        "backend": BACKEND_ROOTS,
    }


def backend_imports(path) -> list[tuple[int, str]]:
    """``(line, dotted name)`` for every import of a backend root in one file.

    Parsed with ``ast`` rather than matched with a regex over comment-stripped
    text. ``test_no_qt_source.py`` strips comments with ``tokenize`` because it
    has to reason about *text*: a Qt name is reachable inside a string literal,
    so it cannot simply parse imports. Here the question is narrower and exactly
    what the grammar answers -- is this an import statement? -- so a removal note
    mentioning ``backend`` is not a hit by construction rather than by filtering,
    and neither is a variable called ``backend_url``.

    ``level > 0`` (a relative import) is skipped: ``from . import x`` cannot
    reach ``backend`` by definition, and carries no module name to read a root
    from.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.partition(".")[0] in BACKEND_ROOTS:
                    hits.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            module = node.module or ""
            if module.partition(".")[0] in BACKEND_ROOTS:
                hits.append((node.lineno, module))
    return hits


def test_no_vendored_source_imports_the_backend():
    """Roadmap Phase 2, criterion 3, as a scan that cannot pass vacuously.

    The engine is a library. ``from backend...`` inside ``visoswap/`` inverts the
    dependency the entire design rests on -- the web layer may know about the
    engine, never the reverse.
    """
    sources = vendored_sources()
    assert sources, (
        "no .py files discovered under visoswap/ -- this scan would pass over an "
        "empty tree and report nothing wrong."
    )

    findings = []
    for path in sources:
        for lineno, dotted in backend_imports(path):
            findings.append(
                "  {}:{}: imports {!r}".format(
                    path.relative_to(REPO_ROOT), lineno, dotted
                )
            )

    assert not findings, (
        "the engine imports the backend package:\n"
        + "\n".join(findings)
        + "\n\nThe engine is a library and must not know the web layer exists."
    )


def test_the_backend_scanner_is_not_inert(tmp_path):
    """The scan above must be shown capable of failing, and of ignoring a note.

    A broken scanner makes ``test_no_vendored_source_imports_the_backend`` pass
    over backend-riddled code while looking exactly as green as a real pass.
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        "# the backend import was removed here\n"
        '"""backend is mentioned in this docstring"""\n'
        "import backend\n"
        "from backend.api import projects\n"
        "from . import backend_shim\n"
        "backend_url = 'http://localhost'  # not an import\n"
        "def later():\n"
        "    import backend.services.cache\n",
        encoding="utf-8",
    )

    hits = backend_imports(sample)
    assert sorted(hits) == [
        (3, "backend"),
        (4, "backend.api"),
        (8, "backend.services.cache"),
    ], (
        "expected exactly the three real imports -- the comment, the docstring, "
        "the relative import and the variable are all non-hits, and the "
        "function-scoped import on line 8 is a hit.\nhits: {}".format(hits)
    )
