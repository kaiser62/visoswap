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


#: Files under ``visoswap/`` that are project-authored rather than vendored, and
#: therefore carry no VisoMaster attribution. Each entry is a repo-relative POSIX
#: path and needs a stated reason -- this list is the one way vendored code could
#: be smuggled past the attribution gate, so it stays short and explicit rather
#: than becoming a pattern.
#:
#: ``processors/context.py`` -- the Qt-free ``EngineContext`` that replaces
#: VisoMaster's ``main_window``. Written for VisoSwap; nothing is copied from
#: upstream. Only the attribute *surface* it has to cover was derived from
#: upstream, and a measured list of attribute names is not copyrightable
#: expression.
#:
#: ``engine.py`` -- ``Engine`` and ``FaceCard``, the project's own published API.
#: The three-method surface is this project's design, and ``FaceCard`` is a
#: deliberate *reduction* of upstream's ``TargetFaceCardButton`` to the three
#: members the swap pipeline actually reads. The call sequences it performs
#: against vendored code are interface use, not copied expression.
#:
#: ``schema/__init__.py``, ``settings/__init__.py``, ``settings/db.py`` and
#: ``settings/store.py`` -- Phase 3's typed settings schema and its three-tier
#: store. One stated reason covers all of them: they are written for VisoSwap and
#: copy nothing from upstream. What was derived is the *measured shape* of
#: upstream's data -- a list of key names, the widget shape each one has, and
#: which of two tiers it belongs to -- and a list of measured names is not
#: copyrightable expression. The SQL, the resolution order and the JSON encoding
#: are this project's own design; upstream has no database at all.
#: ``schema.json`` is not a ``.py`` file so no gate walks it, but it *is* derived
#: from GPLv3 material -- that is what the licence note in its own header object
#: records, and it is why it needs no exemption here rather than being an
#: oversight.
#:
#: ``settings/faces.py`` -- the face-identity layer. It needs its own reason
#: because, unlike the four above, it does contain a *transcription* rather than
#: only a measured shape: ``cosine_similarity`` restates the five lines of
#: arithmetic in ``ModelsProcessor.findCosineDistance``. Nothing is being
#: smuggled past attribution by that. The original is vendored in full, under the
#: header, a few directories away, and this repository's NOTICE and LICENSE
#: already carry the GPLv3 obligation for the whole tree; the transcription
#: exists so that ``import visoswap.settings`` does not require torch, and it
#: carries a documented seam for deleting itself once Phase 4 can pass the
#: engine's own bound method in. It is not vendored *code* -- it imports nothing
#: from upstream and is written against the stdlib ``array`` and ``math``
#: modules -- so the vendored-file header would be the wrong claim to make about
#: it.
#:
#: This stays an explicit per-file entry rather than becoming a pattern. A
#: pattern -- "anything not under ``processors/``", say -- would exempt the next
#: vendored file that happened to land outside the matched tree, and would do it
#: silently. Adding a name here costs a reviewed edit to a test, which is exactly
#: the price this gate exists to charge.
PROJECT_AUTHORED = frozenset(
    {
        "processors/context.py",
        "engine.py",
        "schema/__init__.py",
        "settings/__init__.py",
        "settings/db.py",
        "settings/faces.py",
        "settings/store.py",
        # Written for this project. Unlike ``faces.py`` it transcribes nothing:
        # every rule it applies is read out of the generated schema at run time,
        # and a test pins that no settings key name appears in it as a literal.
        "settings/validate.py",
    }
)


def _is_project_authored(path: Path) -> bool:
    return path.relative_to(VISOSWAP_ROOT).as_posix() in PROJECT_AUTHORED


def test_project_authored_exemptions_all_exist():
    """An exemption for a file that no longer exists is a hole waiting to open.

    Rename ``context.py`` and the stale entry sits there until some future file
    lands on the same path and is silently exempted. Fail while the mismatch is
    still cheap to fix.
    """
    stale = sorted(
        name for name in PROJECT_AUTHORED if not (VISOSWAP_ROOT / name).is_file()
    )
    assert not stale, (
        "PROJECT_AUTHORED names files that do not exist: {}. Remove the entry "
        "or fix the path -- a stale exemption silently un-gates whatever lands "
        "there next.".format(stale)
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
        if not _is_package_scaffolding(path) and not _is_project_authored(path)
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
