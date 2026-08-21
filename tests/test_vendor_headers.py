"""Every vendored file must carry the VisoMaster attribution header.

This is the mechanism that keeps LICENSE-01 true as plans 01-02 through 01-04
push roughly ten thousand more vendored lines through the same pipeline. Without
a test, attribution decays silently: a file gets added without the header, a
refactor strips it, and the GPL obligation quietly stops being met with nothing
failing to say so.

The header is byte-identical across every vendored file on purpose -- no
per-file origin line, no date -- so this test can match it exactly rather than
pattern-guessing at something that looks like attribution.
"""

from pathlib import Path

VISOSWAP_ROOT = Path(__file__).resolve().parent.parent / "visoswap"

#: The canonical attribution header. Four lines, exact, applied immediately
#: above the first line of vendored code.
ATTRIBUTION_HEADER = (
    "# Vendored from VisoMaster (https://github.com/visomaster/VisoMaster).",
    "# VisoMaster is licensed GPLv3; this vendored copy inherits that license.",
    "# This file was vendored into VisoSwap and may have been modified from upstream.",
    "# See NOTICE for vendoring provenance and LICENSE for the full GPLv3 text.",
)


def _is_package_scaffolding(path: Path) -> bool:
    """True for an ``__init__.py`` that contains no vendored code.

    Package scaffolding is written by this project, not copied from VisoMaster,
    so it carries no attribution. The exemption is deliberately narrow: only
    ``__init__.py``, and only when it holds nothing but a docstring, comments,
    and simple assignments -- so it cannot be used to smuggle vendored code past
    the gate by naming a file ``__init__.py``.
    """
    if path.name != "__init__.py":
        return False
    body = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    in_docstring = False
    for line in body:
        if in_docstring:
            if line.endswith('"""') or line.endswith("'''"):
                in_docstring = False
            continue
        if line.startswith('"""') or line.startswith("'''"):
            # A one-line docstring opens and closes on the same line.
            if not (len(line) > 3 and (line.endswith('"""') or line.endswith("'''"))):
                in_docstring = True
            continue
        if line.startswith("#"):
            continue
        if line.startswith("__") and "=" in line:
            continue
        return False
    return True


def _vendored_files() -> list[Path]:
    return sorted(
        path
        for path in VISOSWAP_ROOT.rglob("*.py")
        if not _is_package_scaffolding(path)
    )


def test_vendored_tree_is_not_empty():
    """Guard against the header test passing because it found nothing to check."""
    assert VISOSWAP_ROOT.is_dir(), "missing package root: {}".format(VISOSWAP_ROOT)
    assert _vendored_files(), (
        "no vendored .py files found under {} -- the attribution gate would pass "
        "vacuously. If the tree really is empty, this test is the thing that "
        "should say so.".format(VISOSWAP_ROOT)
    )


def test_every_vendored_file_carries_the_attribution_header():
    missing = []
    for path in _vendored_files():
        # newline=None: universal newlines, so a CRLF vendored file (most of
        # VisoMaster's are) matches the same header text as an LF one.
        with path.open("r", encoding="utf-8", newline=None) as handle:
            first_lines = tuple(
                handle.readline().rstrip("\n") for _ in ATTRIBUTION_HEADER
            )
        if first_lines != ATTRIBUTION_HEADER:
            missing.append((path, first_lines))

    assert not missing, "vendored files missing the attribution header:\n" + "\n".join(
        "  {}\n    expected: {!r}\n    found:    {!r}".format(
            path.relative_to(VISOSWAP_ROOT.parent), ATTRIBUTION_HEADER[0], found[0]
        )
        for path, found in missing
    )
