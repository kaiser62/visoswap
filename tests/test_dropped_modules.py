"""What VisoSwap deliberately does *not* contain.

Two gates live here, and both assert an absence rather than a behaviour.

The first is ``video_processor``. VisoMaster's ``app/processors/video_processor.py``
is the Qt playback loop -- 422 lines owning frame scheduling, a frame queue, seek
state and playback completion. VisoSwap has its own scheduler, and the roadmap
requires this module be **dropped whole rather than stubbed**: two schedulers is
the failure mode the project exists to avoid, and a stub is the shortest path to
having two.

A stub is exactly what a well-meaning contributor adds on hitting an
``ImportError`` -- it makes the error go away, it looks harmless, and it
reintroduces the coupling one attribute at a time. So the check is on the
*filesystem*, not on an import attempt: ``import ...video_processor`` raising is
ambiguous between "correctly absent" and "present but broken", and only one of
those is a pass.

The second is the ``backend`` package. That is Phase 2's criterion 3 -- the engine
must not reach into the web layer -- but the check costs almost nothing here and
catches an accidental coupling at the commit that introduces it rather than a
phase later, when it has grown call sites. ``app`` is checked alongside it: no
vendored module may resolve an import back into the VisoMaster tree it came from.

Imports are read with ``ast``, not with grep. A line regex has to guess at
leading whitespace, parenthesised continuations, aliasing and imports nested
inside a function body, and a regex that guesses wrong in the permissive
direction is a gate that silently passes. ``ast`` does not guess: it reports the
imports the interpreter will actually perform.

That is not hypothetical. Plan 01-04's own verification specified
``grep -rEn "^\\s*(from|import)\\s+(backend|app)\\b" visoswap/``, and on the
machine it was executed on that command returned *no matches* against a control
file containing five such imports, while ``/usr/bin/grep`` with the identical
pattern and file returned all five -- the shell's ``grep`` is shadowed there. A
gate whose verdict depends on which ``grep`` is on ``PATH`` is not a gate. This
test is the authoritative check; the grep form is at best a convenience.

Nothing here imports the vendored tree, so it runs on any interpreter.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VISOSWAP_ROOT = REPO_ROOT / "visoswap"

#: Package roots a vendored module may never import from. ``backend`` is Phase
#: 2's web layer -- the engine is a library and must not know it exists.
#: ``app`` is VisoMaster's own package: a surviving ``app.*`` import means the
#: rewrite map missed a line and the module only works inside the source tree.
FORBIDDEN_IMPORT_ROOTS = frozenset({"backend", "app"})


def _all_sources() -> list[Path]:
    return sorted(VISOSWAP_ROOT.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    """The top-level package name of every import in one file.

    ``level > 0`` (a relative import) is skipped: ``from . import x`` cannot
    reach ``backend`` or ``app`` by definition, and it has no module name to
    read a root off.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            roots.add((node.module or "").partition(".")[0])
    return roots


def test_the_scan_has_something_to_read():
    """Guard against both assertions below passing over an empty tree."""
    assert VISOSWAP_ROOT.is_dir(), "missing package root: {}".format(VISOSWAP_ROOT)
    assert _all_sources(), (
        "no .py files under {} -- both gates in this file would pass "
        "vacuously.".format(VISOSWAP_ROOT)
    )


def test_no_video_processor_artefact_exists():
    """No module, no stub, no re-export. Checked on disk, not by importing.

    Case-insensitive and substring-matched on purpose: ``VideoProcessor.py``,
    ``video_processor_shim.py`` and ``_video_processor.py`` are all the same
    mistake wearing a different name.
    """
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in VISOSWAP_ROOT.rglob("*")
        if "video_processor" in path.name.lower()
    )
    assert not offenders, (
        "video_processor artefacts found under visoswap/: {}. VisoMaster's "
        "playback loop is dropped whole, not stubbed -- VisoSwap's scheduler "
        "owns playback, and a second one is the failure this project exists to "
        "avoid. If something needs a frame-retrieval route, that is Phase 2's "
        "Engine.swap(), not a revived video_processor.".format(offenders)
    )


def _docstring_node_ids(tree) -> set:
    """``id()`` of every node that is a module/class/function docstring."""
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _video_processor_references(path: Path) -> list[tuple[int, str]]:
    """``(line, what)`` for every *code* reference to ``video_processor``.

    Identifiers of every kind -- imports, attributes, names, parameters,
    definitions -- plus string literals, which is how ``importlib`` imports a
    module. Comments and docstrings are exempt: explaining why this module is
    absent is the documentation this project wants, and ``context.py`` and
    ``processors/__init__.py`` both do exactly that.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_node_ids(tree)
    hits = []

    def flag(node, what):
        hits.append((getattr(node, "lineno", 0), what))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for name in (alias.name, alias.asname):
                    if name and "video_processor" in name:
                        flag(node, "import {}".format(name))
        elif isinstance(node, ast.ImportFrom):
            if node.module and "video_processor" in node.module:
                flag(node, "from {} import ...".format(node.module))
            for alias in node.names:
                for name in (alias.name, alias.asname):
                    if name and "video_processor" in name:
                        flag(node, "imported name {}".format(name))
        elif isinstance(node, ast.Attribute) and "video_processor" in node.attr:
            flag(node, "attribute .{}".format(node.attr))
        elif isinstance(node, ast.Name) and "video_processor" in node.id:
            flag(node, "name {}".format(node.id))
        elif isinstance(node, ast.arg) and "video_processor" in node.arg:
            flag(node, "parameter {}".format(node.arg))
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) and "video_processor" in node.name:
            flag(node, "definition {}".format(node.name))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if "video_processor" in node.value:
                flag(node, "string literal {!r}".format(node.value))

    return sorted(set(hits))


def test_no_vendored_module_names_video_processor_in_code():
    """A re-export or an attribute read is the same coupling without the file.

    ``self.video_processor = main_window.video_processor`` in ``frame_worker``
    was the only reference in the vendored subset, and plan 01-04 removed it with
    the display path. This catches it coming back under any name that does not
    create a file for ``test_no_video_processor_artefact_exists`` to find.
    """
    findings = []
    for path in _all_sources():
        for lineno, what in _video_processor_references(path):
            findings.append(
                "  {}:{}: {}".format(path.relative_to(REPO_ROOT), lineno, what)
            )
    assert not findings, (
        "video_processor referenced in vendored code (comments and docstrings "
        "are exempt; these are not):\n" + "\n".join(findings)
    )


def test_the_video_processor_code_scan_is_not_inert(tmp_path):
    """Shown capable of failing on each shape, and of allowing the prose."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""video_processor is dropped whole -- this docstring must be allowed."""\n'
        "import importlib\n"
        "from visoswap.processors.video_processor import VideoProcessor\n"
        "self_like = object()\n"
        "x = self_like.video_processor\n"
        "m = importlib.import_module"
        '("visoswap.processors.video_processor")\n'
        "def make_video_processor():\n"
        "    # video_processor in a comment must be allowed\n"
        "    pass\n",
        encoding="utf-8",
    )

    hits = _video_processor_references(sample)
    kinds = {what.split()[0] for _, what in hits}
    assert kinds == {"from", "attribute", "string", "definition"}, (
        "the scan missed a reference shape: {}".format(hits)
    )
    assert not any(lineno == 1 for lineno, _ in hits), (
        "the module docstring was flagged; prose explaining the absence must be "
        "allowed: {}".format(hits)
    )
    assert not any(lineno == 8 for lineno, _ in hits), (
        "a comment was flagged: {}".format(hits)
    )


def test_no_vendored_module_imports_the_backend_or_the_upstream_app():
    """The engine is a library: it is imported by callers and imports none of them."""
    findings = []
    for path in _all_sources():
        for root in sorted(_imported_roots(path) & FORBIDDEN_IMPORT_ROOTS):
            findings.append("  {}: imports {!r}".format(path.relative_to(REPO_ROOT), root))

    assert not findings, (
        "vendored modules import a forbidden package root:\n"
        + "\n".join(findings)
        + "\n\n'backend' inverts the dependency the whole design rests on -- the "
        "engine must not know the web layer exists. 'app' means an import was "
        "left pointing back at VisoMaster and the module only works inside the "
        "source tree it was vendored out of."
    )


def test_the_forbidden_import_check_is_not_inert(tmp_path):
    """Shown capable of failing, on the exact syntax it has to catch.

    Including the two shapes a line regex is most likely to miss: an import
    nested inside a function body, and an aliased dotted import.
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import backend\n"
        "from backend.api import projects\n"
        "import backend.services.cache as cache\n"
        "def f():\n"
        "    from app.ui.main_ui import MainWindow\n"
        "from visoswap.processors.context import EngineContext\n"
        "from . import sibling\n",
        encoding="utf-8",
    )

    roots = _imported_roots(sample)
    assert "backend" in roots, "a plain `import backend` was not detected"
    assert "app" in roots, "an `app` import nested inside a function was not detected"
    assert "visoswap" in roots, "the parser missed a legitimate import"
    assert roots & FORBIDDEN_IMPORT_ROOTS == {"app", "backend"}
