"""The published API is exactly three methods, and ``FaceCard`` is exactly four
members.

Both are pinned by parsing the source with ``ast`` rather than by importing it.
``visoswap.engine`` pulls in torch, onnxruntime and cv2, none of which exist on
the interpreter that runs pytest -- and an import-based check would therefore
have to be skipped here, which Phase 1 established is a false green. The grammar
answers the question being asked ("what public methods does this class define")
exactly, so parsing is not a workaround; it is the right tool that also happens
to need no dependencies.

**This replaces the grep the plan proposed.** ``grep -nE '^    def [a-zA-Z]'``
over ``engine.py`` matches every method at four-space indent in the file, which
includes ``FaceCard.get_embedding`` and ``FaceCard.assign`` -- both methods the
same plan requires. The grep could therefore never have passed as written. A
class-scoped check is what was meant, and it is strictly sharper: it cannot be
satisfied by moving a method into another class in the same file.
"""

import ast
from pathlib import Path

ENGINE_SOURCE = Path(__file__).resolve().parent.parent / "visoswap" / "engine.py"

#: The design's published API. Every later phase builds against these three
#: names and their signatures; Phase 4 wires a backend to them and Phase 5 a web
#: layer, in a different repository. Widening this set is a cross-repository
#: commitment, so it costs a reviewed edit to a test rather than a one-line
#: addition to a class.
ENGINE_PUBLIC_METHODS = frozenset({"load", "detect_faces", "swap"})

#: ``FaceCard``'s whole surface. ``frame_worker`` is 1,292 lines and touches
#: exactly three members on the values of the target-face mapping -- ``face_id``,
#: ``get_embedding(...)`` and ``assigned_input_embedding`` -- counted across the
#: file, not assumed. The other two entries here are what those three are built
#: from: ``embedding_store`` backs ``get_embedding``, ``recognition_model`` is
#: the model ``face_id`` is derived under, and ``crop`` is the one member kept
#: for callers rather than for the worker.
#:
#: Upstream's ``TargetFaceCardButton`` also carries a media path, a Qt checkable
#: state, a context menu, an assigned-input-faces dict and an
#: assigned-merged-embeddings dict, and the swap pipeline reads none of them.
#: That is the object this set exists to stop growing back.
FACE_CARD_MEMBERS = frozenset(
    {
        "embedding_store",
        "crop",
        "recognition_model",
        "assigned_input_embedding",
        "face_id",
        "get_embedding",
        "assign",
    }
)


def _module() -> ast.Module:
    return ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8"), str(ENGINE_SOURCE))


def _class(name: str) -> ast.ClassDef:
    for node in _module().body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(
        "{} defines no class named {!r} -- this test cannot pass vacuously, and "
        "a renamed class must be a visible failure rather than a silent "
        "no-op.".format(ENGINE_SOURCE.name, name)
    )


def _public_names(cls: ast.ClassDef) -> set[str]:
    """Every non-underscore name the class body binds: methods and fields alike.

    Dataclass fields are ``AnnAssign`` nodes, methods are ``FunctionDef``, and a
    ``@property`` is a ``FunctionDef`` too. All three are surface a caller can
    reach, so all three are counted; a check that looked only at methods would
    let a public attribute in unnoticed.
    """
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return {name for name in names if not name.startswith("_")}


def test_engine_source_exists():
    """Guard against every other test in this file passing over a missing file."""
    assert ENGINE_SOURCE.is_file(), "missing {}".format(ENGINE_SOURCE)


def test_engine_publishes_exactly_three_methods():
    found = _public_names(_class("Engine"))
    assert found == ENGINE_PUBLIC_METHODS, (
        "Engine's public surface is {} but must be exactly {}. Everything else "
        "belongs behind an underscore: these three signatures are the design's "
        "published API and changing them after Phase 4 means changing call "
        "sites in two repositories.".format(sorted(found), sorted(ENGINE_PUBLIC_METHODS))
    )


def test_face_card_holds_nothing_that_came_from_a_widget():
    found = _public_names(_class("FaceCard"))
    assert found == FACE_CARD_MEMBERS, (
        "FaceCard's surface is {} but must be exactly {}. The swap pipeline "
        "reads three members; anything beyond what those three are built from "
        "is upstream's Qt card button growing back.".format(
            sorted(found), sorted(FACE_CARD_MEMBERS)
        )
    )


def test_face_card_carries_no_stored_identifier():
    """``face_id`` must be *derived*, never assigned.

    The roadmap forbids an identifier tied to a UI element, and upstream's is an
    integer handed out by the widget that owns the card. A stored ``face_id``
    field would satisfy the surface test above while quietly reintroducing
    exactly that, so the mechanism is pinned as well as the name: it has to be a
    ``property``.
    """
    node = next(
        (
            child
            for child in _class("FaceCard").body
            if isinstance(child, ast.FunctionDef) and child.name == "face_id"
        ),
        None,
    )
    assert node is not None, "FaceCard.face_id is not a method or property"
    decorators = {
        decorator.id
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Name)
    }
    assert "property" in decorators, (
        "FaceCard.face_id must be a @property derived from the recognition "
        "embedding, not a stored value. Found decorators: {}".format(sorted(decorators))
    )


def test_the_surface_check_is_not_inert():
    """Show ``_public_names`` capable of seeing a method it should reject.

    A surface test that silently parsed nothing would pass forever. This feeds
    the same extractor a class with one public and one private method and
    asserts it reports exactly the public one.
    """
    sample = ast.parse(
        "class Sample:\n"
        "    field: int = 0\n"
        "    _hidden: int = 0\n"
        "    def visible(self): pass\n"
        "    def _invisible(self): pass\n"
    ).body[0]
    assert _public_names(sample) == {"field", "visible"}
