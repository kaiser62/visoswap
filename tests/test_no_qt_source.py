"""A static Qt scan over vendored source, complementing the import gate.

The import gate proves Qt is not *reached* when a module is imported. It cannot
see a Qt reference the import never executes: a name inside a function body that
plan 01-03's surgery missed, a lazily-imported widget helper, a ``QPixmap``
conversion sitting in a branch nothing takes at import time. Those survive an
import proof intact and only fail once the code actually runs.

This scan reads source instead, so unreached code is covered too. It never
imports anything, which also means it runs on any interpreter -- including the
repo's default 3.13, which has none of the engine dependencies.

Comments are stripped with ``tokenize``, not with a line filter. Plans 01-03 and
01-04 rip Qt out of two modules and will leave notes saying what was removed;
those notes are how the next reader understands the surgery, and they mention Qt
by name. A ``grep -v '^#'`` style filter drops whole-line comments but keeps
trailing ones, so ``self.x = 1  # was a Signal`` would trip the gate. Widening
the filter to catch trailing comments means guessing where a ``#`` starts a
comment and where it sits inside a string -- and a filter that guesses wrong in
the other direction masks a real reference. Token-level stripping does not
guess: the tokenizer already knows exactly which characters are a comment.

Docstrings and string literals are deliberately *not* stripped. A Qt name inside
a string is reachable -- ``importlib.import_module("PySide6.QtCore")`` is a
string -- so this scan treats it as a hit.
"""

import re
import tokenize

from tests.conftest import (
    PENDING_QT_STRIP,
    REPO_ROOT,
    module_name_for,
    vendored_sources,
)

#: The seven Qt binding roots, the Qt symbol names the stripped modules use, and
#: any reference to VisoMaster's UI package. Case-insensitive: an import is
#: case-sensitive but a half-finished rename is not, and the corpus was measured
#: to contain zero case-insensitive matches before the gate was written, so the
#: looser match costs nothing and catches ``pyside6`` in a stray note.
QT_PATTERN = re.compile(
    r"\b(?:"
    r"PySide6|PySide2|PyQt5|PyQt6|qtpy|shiboken6|shiboken2"
    r"|QtCore|QtGui|QtWidgets|QObject|QPixmap|Signal|Slot"
    r")\b"
    r"|\bapp\.ui\b",
    re.IGNORECASE,
)


def strip_comments(path) -> list[str]:
    """The file's lines with every COMMENT token blanked out.

    Comment characters are replaced with spaces rather than removed, so column
    numbers in the remaining text still line up with the real file and a hit can
    be pointed at precisely.
    """
    with open(path, "rb") as handle:
        # utf-8-sig so a BOM is consumed the same way ``tokenize`` consumes it,
        # keeping column offsets aligned between the two reads.
        lines = handle.read().decode("utf-8-sig").splitlines()

    with open(path, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type != tokenize.COMMENT:
                continue
            row = token.start[0] - 1
            start_col, end_col = token.start[1], token.end[1]
            line = lines[row]
            lines[row] = (
                line[:start_col] + " " * (end_col - start_col) + line[end_col:]
            )
    return lines


def qt_hits(path) -> list[tuple[int, str, str]]:
    """(line number, matched text, the line) for every Qt reference in `path`."""
    hits = []
    for lineno, line in enumerate(strip_comments(path), 1):
        for match in QT_PATTERN.finditer(line):
            hits.append((lineno, match.group(0), line.strip()))
    return hits


def test_scan_covers_the_vendored_tree():
    """Guard against the scan passing because it found nothing to read."""
    sources = vendored_sources()
    assert sources, (
        "no .py files discovered under visoswap/ -- the static Qt scan would "
        "pass vacuously."
    )
    for pending in PENDING_QT_STRIP:
        assert pending not in {module_name_for(p) for p in sources}, (
            "{} is in PENDING_QT_STRIP but was still scanned".format(pending)
        )


def test_scanner_is_not_inert(tmp_path):
    """The scan must be shown capable of failing, and of ignoring a comment.

    Two things go wrong silently otherwise: a broken pattern makes every other
    assertion here pass over Qt-riddled code, and over-eager comment stripping
    (or none at all) makes the gate either blind or unusable for plans 01-03 and
    01-04, which will annotate what they removed.
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from PySide6.QtCore import Signal\n"
        "value = 1  # was a Signal from PySide6.QtCore\n"
        "# QPixmap conversion removed here\n"
        "clean = 2\n",
        encoding="utf-8",
    )

    hits = qt_hits(sample)
    lines_hit = sorted({lineno for lineno, _, _ in hits})

    assert lines_hit == [1], (
        "expected exactly line 1 to be flagged -- line 2's trailing comment and "
        "line 3's whole-line comment must both be stripped, and line 4 is "
        "clean.\nhits: {}".format(hits)
    )
    assert {match for _, match, _ in hits} == {"PySide6", "QtCore", "Signal"}, (
        "the pattern did not match every Qt name on the import line: {}".format(hits)
    )


def test_no_qt_source_references():
    sources = vendored_sources()
    assert sources, "no .py files discovered under visoswap/"

    findings = []
    for path in sources:
        for lineno, match, line in qt_hits(path):
            findings.append(
                "  {}:{}: {!r} in: {}".format(
                    path.relative_to(REPO_ROOT), lineno, match, line
                )
            )

    assert not findings, (
        "Qt references found in vendored source, outside comments:\n"
        + "\n".join(findings)
    )
