"""Roadmap criterion 4: no ComfyUI remains in ``backend/``.

Criterion 4 is ``grep -ril comfyui backend/`` returning no matches outside
comments explicitly noting the removal. It has two halves, and one scan cannot
answer both, so there are two:

* a **text** scan over ``backend/**/*.py``, case-insensitive, with comment lines
  stripped by ``tokenize`` -- the same technique ``tests/test_no_qt_source.py``
  uses -- so a comment recording the removal is a non-hit by construction.
  ``__pycache__`` is excluded: bytecode is not source, and a gate that fails on
  stale ``.pyc`` files is a gate that gets deleted. This half proves no *source*
  reference remains.
* a **route** scan that builds the FastAPI app and walks ``app.routes``,
  asserting no path contains the name. This is the half a text scan cannot
  reach: a route registered from a variable would pass a grep and still serve.
  This half proves no *served path* remains.

Both scans report how many files, and how many routes, they examined, and the
tests assert those counts are greater than zero -- a scanner pointed at nothing
passes perfectly, and a perfect pass that proves nothing is the failure this
module exists to prevent.
"""

import re
import tokenize
from pathlib import Path

import pytest

from backend.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"

#: The name under test, case-insensitive. ``re.IGNORECASE`` because an import
#: is case-sensitive but a half-finished rename is not.
PATTERN = re.compile(r"comfyui", re.IGNORECASE)


def strip_comments(path: Path) -> list[str]:
    """The file's lines with every COMMENT token blanked out (token-based)."""
    with open(path, "rb") as handle:
        lines = handle.read().decode("utf-8-sig").splitlines()
    with open(path, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type != tokenize.COMMENT:
                continue
            row = token.start[0] - 1
            start_col, end_col = token.start[1], token.end[1]
            line = lines[row]
            lines[row] = line[:start_col] + " " * (end_col - start_col) + line[end_col:]
    return lines


def backend_sources() -> list[Path]:
    """Every ``.py`` file under ``backend/``, ``__pycache__`` excluded."""
    return sorted(
        p
        for p in BACKEND_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def text_hits(path: Path) -> list[tuple[int, str]]:
    """(line number, matched text) for every reference in ``path``."""
    hits = []
    for lineno, line in enumerate(strip_comments(path), 1):
        for match in PATTERN.finditer(line):
            hits.append((lineno, match.group(0)))
    return hits


def test_text_scan_has_something_to_read():
    """The text scan must not pass by examining nothing."""
    sources = backend_sources()
    assert sources, "no .py files discovered under backend/ -- scan is vacuous"


def test_text_scanner_is_not_inert(tmp_path):
    """The gate must be shown capable of failing, and of ignoring a comment."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from comfyui.client import ComfyUIClient\n"
        "value = 1  # was a comfyui client, removed\n"
        "# ComfyUI branch dropped here\n"
        "clean = 2\n",
        encoding="utf-8",
    )
    hits = text_hits(sample)
    lines_hit = sorted({lineno for lineno, _ in hits})
    # Line 1 flagged; lines 2 and 3 are comments (stripped); line 4 clean.
    assert lines_hit == [1], (
        "expected only line 1 flagged -- trailing/whole-line comments must be "
        "stripped, and the clean line must not match.\nhits: {}".format(hits)
    )


def test_no_comfyui_source_references():
    sources = backend_sources()
    assert sources, "no .py files discovered under backend/"

    findings = []
    for path in sources:
        for lineno, match in text_hits(path):
            findings.append(
                "  {}:{}: {!r}".format(path.relative_to(BACKEND_ROOT), lineno, match)
            )
    assert not findings, (
        "ComfyUI references found in backend source, outside comments:\n"
        + "\n".join(findings)
    )
    # The gate must state what it examined, or it can silently narrow itself.
    assert len(sources) > 0


def test_no_comfyui_route():
    """No ComfyUI path is registered on the FastAPI app."""
    app = create_app()
    routes = [r.path for r in app.routes]
    assert routes, "the app registered no routes -- route scan is vacuous"
    bad = [p for p in routes if PATTERN.search(p)]
    assert not bad, (
        "ComfyUI paths registered on the app: {}".format(bad)
    )


def test_route_scanner_is_not_inert(tmp_path):
    """A decoy route must be flagged by the route scan."""
    from fastapi import APIRouter, FastAPI

    decoy = APIRouter()

    @decoy.get("/comfyui/status")
    async def _status():
        return {}

    app = FastAPI()
    app.include_router(decoy)
    bad = [r.path for r in app.routes if PATTERN.search(r.path)]
    assert bad, "the route scan did not flag a planted /comfyui/status route"
