"""The project owns its model set; no VisoMaster install is needed (plan 04-04).

Closes ENGINE-01's clause 2 ("no VisoMaster install present") at the level that
can actually be proven cheaply, in two parts split across this file and the
runtime open-guard in Task 3:

* This file proves the *defaults* — models directory, face sources, engine
  interpreter, media — all resolve to project-owned locations, and that
  ``MODELS_DIR`` relocates every tracked path.
* The open guard (``--no-visomaster`` mode on the runners) proves a real swap
  never opens a file that resolves inside a VisoMaster install.

Assertions here are written against **resolved** paths, never link-ness:
``os.path.islink('model_assets')`` returns ``False`` for the junction on this
machine (measured), so a link check proves nothing. Only ``realpath`` sees a
junction resolving into the borrowed tree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, run_engine_runner
from visoswap.models import manifest
from visoswap.schema import DEFAULT_MODELS_DIR, resolve_models_dir

#: A VisoMaster install path, for the "resolves outside" assertions. Any real
#: VisoMaster checkout on this machine lives here; the owned copy is elsewhere.
VISOMASTER_ROOT = Path("D:/Visomaster")

#: The combined interpreter: the engine runs and PySide6 is genuinely absent, so
#: the no-visomaster swap re-measures ENGINE-01 clause 1 (a fresh measurement).
COMBINED_PYTHON = REPO_ROOT / ".venv-clean" / "Scripts" / "python.exe"


def _resolves_inside_visomaster(path: Path) -> bool:
    real = path.resolve()
    return real.is_relative_to(VISOMASTER_ROOT.resolve())


def test_default_models_dir_resolves_outside_any_visomaster_install():
    default = resolve_models_dir()
    assert default == DEFAULT_MODELS_DIR.resolve()
    assert not _resolves_inside_visomaster(default), (
        "the default models dir {} resolves inside a VisoMaster install".format(default)
    )


def test_every_tracked_path_default_resolves_outside_visomaster(monkeypatch):
    monkeypatch.delenv("MODELS_DIR", raising=False)
    for entry in manifest.tracked():
        assert not _resolves_inside_visomaster(entry.path), (
            "tracked entry {} resolves inside a VisoMaster install: {}".format(
                entry.name, entry.path
            )
        )


def test_setting_modes_dir_relocates_every_tracked_path(monkeypatch, tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    monkeypatch.setenv("MODELS_DIR", str(owned))
    for entry in manifest.tracked():
        assert entry.path.is_relative_to(owned.resolve()), (
            "entry {} did not move under MODELS_DIR: {}".format(entry.name, entry.path)
        )


def test_clearing_modes_dir_returns_paths_to_the_default(monkeypatch):
    monkeypatch.delenv("MODELS_DIR", raising=False)
    for entry in manifest.tracked():
        assert entry.path.is_relative_to(DEFAULT_MODELS_DIR.resolve())


def test_the_junction_is_not_relied_upon_for_resolution():
    """Even though model_assets/ may still exist as a junction, the default and
    every tracked path must not resolve through it into VisoMaster."""
    default = resolve_models_dir()
    # islink is False for a junction, but realpath still resolves it; we assert
    # on the resolved path so a junction into VisoMaster would be caught.
    assert not _resolves_inside_visomaster(default)
    assert default != (REPO_ROOT / "model_assets").resolve()


def test_no_visomaster_literal_on_the_product_path():
    """A scan over backend/ and visoswap/ finds no VisoMaster folder name.

    The 04-01 port already dropped the face-source picker that read
    ``<visomaster_dir>/inputt``; this asserts the name does not resurface on the
    product path. ``tests/`` legitimately names the checkout in overrides and the
    seal documentation, so it is excluded on purpose.
    """
    import ast
    import re

    roots = [REPO_ROOT / "backend", REPO_ROOT / "visoswap"]
    examined = 0
    hits = []

    def first_expr_string(body):
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            return body[0].value.value
        return None

    def non_docstring_string_literals(tree):
        docstrings = {first_expr_string(tree.body)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                docstrings.add(first_expr_string(node.body))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value not in docstrings
        ]

    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            examined += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for literal in non_docstring_string_literals(tree):
                if "://" in literal:
                    continue  # a URL, not a local install path
                # A VisoMaster *install path* literal: the name, case-insensitive,
                # only when part of a filesystem path (drive letter or separator).
                # Docstrings (provenance/severance notes) and URLs are not findings.
                if re.search(r"[:\\/][^\"' ]*visomaster|visomaster[\\/]", literal, re.IGNORECASE):
                    hits.append((path.relative_to(REPO_ROOT), literal[:120]))
    assert examined > 0, "the literal scan examined no files"
    assert not hits, "VisoMaster install-path literal on the product path:\n" + "\n".join(
        "  {}: {}".format(rel, snippet) for rel, snippet in hits
    )


# ---------------------------------------------------------------------------
# the runtime open guard (plan 04-04 Task 3): close ENGINE-01 clause 2
# ---------------------------------------------------------------------------


def test_the_open_guard_is_capable_of_firing():
    """The guard must be shown to fail, not assumed to work (T-04-24)."""
    from tests._open_guard import OpenGuardFired, VisoMasterOpenGuard

    import tempfile

    with tempfile.TemporaryDirectory(prefix="visoswap-guard-plant-") as tmp:
        planted = Path(tmp)
        (planted / "seed.txt").write_text("x", encoding="utf-8")
        guard = VisoMasterOpenGuard(forbidden_roots=[planted])
        with guard:
            with pytest.raises(OpenGuardFired):
                with open(planted / "seed.txt", "r", encoding="utf-8"):
                    pass


def test_the_open_guard_observed_real_opens_and_fires():
    """Non-vacuity, both halves: a real run observes opens, and a planted root fires."""
    from tests._open_guard import assert_capable_of_firing

    # Half 2: the guard provably fires on a forbidden root the run really touches.
    fired_opens = assert_capable_of_firing()
    assert fired_opens > 0, "the firing proof observed no opens"

    # Half 1: the real swap, run under the guard, observes a non-zero open count.
    assert COMBINED_PYTHON.is_file()
    code, output = run_engine_runner(
        COMBINED_PYTHON, ["--no-visomaster"]
    )
    assert code == 0, "no-visomaster swap failed (exit {}): {}".format(code, output)
    assert output.startswith("CLEAN:"), output
    assert "guard_opens=" in output, "the guard did not report an open count: {}".format(output)
    opens = int(output.split("guard_opens=")[1].split()[0])
    assert opens > 0, "the guard observed no opens during a real swap -- vacuous: {}".format(output)
    # Clause 1, re-measured fresh on the combined interpreter.
    assert "PySide6=no" in output, "PySide6 was reachable during the no-visomaster swap: {}".format(output)


def test_the_backend_tracer_runs_clean_under_the_open_guard():
    """A full generation request through the route, guard armed (ENGINE-01 clause 2)."""
    from tests.conftest import run_backend_runner

    code, output = run_backend_runner(
        COMBINED_PYTHON, ["--tracer", "--no-visomaster"]
    )
    assert code == 0, "tracer --no-visomaster failed (exit {}): {}".format(code, output)
    assert output.startswith("CLEAN:tracer:"), output
    assert "guard_opens=" in output, "the guard did not report an open count: {}".format(output)
    opens = int(output.split("guard_opens=")[1].split()[0])
    assert opens > 0, "the guard observed no opens during a real generation -- vacuous: {}".format(output)
