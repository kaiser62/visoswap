"""The two swapper settings the old web UI re-pinned on every load, gated shut.

What was there
--------------
``web_ui.py`` assigns ``SwapModelSelection = "Inswapper128"`` and
``SwapperResSelection = "256"`` at two points on the load path -- inside the
defaults merge and again on the generation path -- and carries the same pair in
its built-in defaults and in a hand-written schema stub. Five sites. It is wrong
in two independent ways:

1. **Wrong tier.** Both assignments write into the ``options`` dict, which is the
   *global* tier, while both keys are **project**-tier keys that live in
   ``parameters``. The values were being written where nothing reads them as
   settings for a face. This is the "appears to save, does not apply" failure the
   whole phase exists to remove, in the upstream code that inspired it.
2. **Wrong value.** Both saved profiles store ``SwapperResSelection`` as
   ``"128"``. The reassignment forces ``"256"`` on every single load, silently
   contradicting the user's own saved choice.

None of the five sites is ported. This file is the gate that keeps it that way
through Phases 4 and 5, when a backend and a frontend land and nobody is reading
this plan any more.

Why the scan parses instead of grepping
---------------------------------------
Both key names appear legitimately, as **data**, in the committed schema, in the
committed preset seed, in these docs and in this file's own docstring. A text
grep would either flag all of that or be weakened until it flagged nothing --
and a gate weakened to silence is worse than no gate, because it is believed
(T-03-16). Parsing gives the distinction for free: naming a key in a comment, a
docstring or a string is fine; **assigning** to one is not.

Why the tests directory is excluded, stated rather than hidden
--------------------------------------------------------------
Plan 02-02's engine smoke test deliberately pins a swapper model and resolution
as a fixture override, so that its non-zero-pixel assertion is testing a real
configuration rather than whatever the defaults happen to be. That is a
legitimate use and this gate must not break it. An unexplained exclusion in a
gate is indistinguishable from a hole, so it is written here in the one place
somebody auditing the gate will look.

Why the gate is itself proven
-----------------------------
Two tests below plant a violation the scanner must flag and a mention the
scanner must not, and one asserts the examined-file count is non-zero. A scanner
pointed at an empty file set passes perfectly and proves nothing, and this
gate's entire job is to run silently in phases that have not been written yet.
"""

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from visoswap.settings import db, presets, store

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The pair. Data, not code, everywhere they legitimately appear.
GUARDED_KEYS = ("SwapModelSelection", "SwapperResSelection")

#: Source trees this repository owns. ``backend`` and ``frontend`` do not exist
#: yet -- Phases 4 and 5 create them -- and a directory that is not there is
#: skipped rather than being an error. That is the entire point: the gate is
#: written now so it is already running when the code it guards is written.
PYTHON_ROOTS = ("visoswap", "tools", "backend")
FRONTEND_ROOTS = ("frontend/src",)

FRONTEND_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte")

#: ``.name =``, ``["name"] =`` and ``name =``. A single ``=`` only: ``==`` and
#: ``=>`` are not assignments, and a ``:`` is an object-literal or a type
#: annotation, which is how a settings payload is legitimately *shaped* in
#: TypeScript rather than how a value is forced.
_FRONTEND_ASSIGN = re.compile(
    r"(?:\.|\[\s*['\"])?({})(?:['\"]\s*\])?\s*=(?!=|>)".format("|".join(GUARDED_KEYS))
)

_FRONTEND_COMMENT = re.compile(r"^\s*(//|/\*|\*)")


def _python_files():
    for root in PYTHON_ROOTS:
        directory = REPO_ROOT / root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            yield path


def _frontend_files():
    for root in FRONTEND_ROOTS:
        directory = REPO_ROOT / root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in FRONTEND_SUFFIXES:
                yield path


def _assigned_name(target):
    """The guarded key this assignment target names, or ``None``.

    Three forms, which are the three ways the upstream sites are written:
    ``d["SwapModelSelection"] = ...``, ``obj.SwapModelSelection = ...`` and
    ``SwapModelSelection = ...``.
    """
    if isinstance(target, ast.Subscript):
        index = target.slice
        if isinstance(index, ast.Constant) and index.value in GUARDED_KEYS:
            return index.value
        return None
    if isinstance(target, ast.Attribute) and target.attr in GUARDED_KEYS:
        return target.attr
    if isinstance(target, ast.Name) and target.id in GUARDED_KEYS:
        return target.id
    return None


def scan_python(paths):
    """``(findings, examined)`` over Python sources.

    A finding is ``(path, line, key)``. Comments and docstrings never produce
    one; the parser has already discarded the first and a docstring is an
    expression, not an assignment.
    """
    findings = []
    examined = 0
    for path in paths:
        examined += 1
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                targets = [node.target]
            else:
                continue
            for target in targets:
                key = _assigned_name(target)
                if key is not None:
                    findings.append((str(path), node.lineno, key))
    return findings, examined


def scan_frontend(paths):
    """``(findings, examined)`` over frontend sources, line by line.

    Not a parser: adding a TypeScript parser as a test dependency to guard two
    key names would be a heavier commitment than the gate is worth. Comment
    lines are skipped explicitly, which is the same distinction the Python side
    gets from parsing.
    """
    findings = []
    examined = 0
    for path in paths:
        examined += 1
        for number, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _FRONTEND_COMMENT.match(line):
                continue
            match = _FRONTEND_ASSIGN.search(line)
            if match:
                findings.append((str(path), number, match.group(1)))
    return findings, examined


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


def test_no_python_source_assigns_either_swapper_key():
    findings, examined = scan_python(_python_files())
    assert examined > 0, (
        "the scanner examined no files, which is how a gate passes while "
        "proving nothing"
    )
    assert not findings, (
        "a swapper setting is reassigned in source: {}. Both keys are "
        "project-tier settings resolved from the store; pinning one in code "
        "overwrites whatever the user saved, which is what the old web UI did "
        "on every load.".format(findings)
    )


def test_no_frontend_source_assigns_either_swapper_key():
    findings, examined = scan_frontend(_frontend_files())
    # Zero examined is correct today: Phase 5 has not created the frontend. It
    # is asserted non-zero in `test_the_scanners_examine_something` only for the
    # Python side, which does exist -- claiming a non-zero count here would fail
    # today and be deleted, and a deleted assertion guards nothing later.
    assert not findings, findings
    assert examined >= 0


def test_the_python_scanner_examines_the_source_tree_it_claims_to():
    """Names the files it must have walked, so a scanner that silently narrowed
    itself to nothing fails here rather than passing everywhere."""
    examined = {Path(path).name for path in _python_files()}
    for expected in ("presets.py", "store.py", "handlers.py", "migrate_profiles.py"):
        assert expected in examined, (expected, len(examined))


# --------------------------------------------------------------------------
# the gate, proven in both directions
# --------------------------------------------------------------------------


def test_the_scanner_flags_a_planted_assignment(tmp_path):
    """Without this, the gate could be silently vacuous and every phase after
    this one would inherit a green light that means nothing."""
    planted = tmp_path / "violation.py"
    planted.write_text(
        "options = {}\n"
        'options["SwapModelSelection"] = "Inswapper128"\n'
        'options["SwapperResSelection"] = "256"\n'
        'config.SwapperResSelection = "512"\n'
        'SwapModelSelection = "CSCS"\n',
        encoding="utf-8",
    )
    findings, examined = scan_python([planted])
    assert examined == 1
    assert [(line, key) for _, line, key in findings] == [
        (2, "SwapModelSelection"),
        (3, "SwapperResSelection"),
        (4, "SwapperResSelection"),
        (5, "SwapModelSelection"),
    ]


def test_the_scanner_does_not_flag_a_mention_in_a_comment_or_a_string(tmp_path):
    """Naming the keys must stay allowed. This file, the docs and the plans all
    do it, and a gate that forbade discussing itself would be turned off."""
    innocent = tmp_path / "mention.py"
    innocent.write_text(
        '"""SwapModelSelection and SwapperResSelection are project-tier keys."""\n'
        "# SwapperResSelection = \"256\" was the old reassignment.\n"
        'GUARDED = ("SwapModelSelection", "SwapperResSelection")\n'
        'value = settings["SwapperResSelection"]\n'
        'if payload.get("SwapModelSelection") == "Inswapper128":\n'
        "    pass\n"
        'PAYLOAD = {"SwapModelSelection": "Inswapper128"}\n',
        encoding="utf-8",
    )
    findings, examined = scan_python([innocent])
    assert examined == 1
    assert findings == []


def test_the_frontend_scanner_is_proven_in_both_directions(tmp_path):
    source = tmp_path / "store.ts"
    source.write_text(
        "// SwapperResSelection = '256' was the old reassignment\n"
        "const key: string = 'SwapModelSelection';\n"
        "const payload = { SwapModelSelection: 'Inswapper128' };\n"
        "if (settings.SwapperResSelection === '128') { return; }\n"
        "settings.SwapperResSelection = '256';\n"
        "settings['SwapModelSelection'] = 'Inswapper128';\n",
        encoding="utf-8",
    )
    findings, examined = scan_frontend([source])
    assert examined == 1
    assert [(line, key) for _, line, key in findings] == [
        (5, "SwapperResSelection"),
        (6, "SwapModelSelection"),
    ]


# --------------------------------------------------------------------------
# and the behaviour the reassignment used to cause
# --------------------------------------------------------------------------


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    db.apply_settings_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def models_dir(tmp_path):
    from visoswap import schema

    schema.clear_dfm_cache()
    directory = tmp_path / "model_assets"
    directory.mkdir()
    yield str(directory)
    schema.clear_dfm_cache()


def test_both_presets_resolve_to_the_swapper_resolution_they_stored(
    connection, models_dir
):
    """Deleting two lines is not the same as demonstrating that deleting them
    had the intended effect.

    Both source profiles store ``"128"``. The old loader forced ``"256"`` on
    every load. With the reassignment gone, what the profile stored is what
    resolves -- which is also the one user-visible rendering change in this
    phase: both presets now swap at 128 rather than at the forced 256.
    """
    presets.seed_presets(connection)
    seeded = presets.list_presets(connection)
    assert len(seeded) == 2

    for preset in seeded:
        project_id = "project-{}".format(preset["id"])
        presets.apply_preset(connection, preset["id"], project_id, models_dir)
        for key in GUARDED_KEYS:
            assert (
                store.resolve(connection, key, project_id, models_dir=models_dir)
                == preset["project"][key]
            ), (preset["id"], key)
        assert (
            store.resolve(
                connection, "SwapperResSelection", project_id, models_dir=models_dir
            )
            == "128"
        )
