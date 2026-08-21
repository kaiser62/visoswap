"""``EngineContext`` carries exactly seven fields. This is what pins that.

VisoMaster's processor tree reaches into the Qt main window for fifteen distinct
attributes. ``EngineContext`` replaces that god object with seven fields of plain
data -- and the only thing keeping it seven is this file.

That matters more than it looks. A context object with no enforced boundary is
how the god object comes back: one field at a time, each individually reasonable,
none reviewed against the whole. This test makes widening the surface cost a
deliberate edit here, which is a place a reviewer will look, rather than a
one-line addition to a dataclass, which is a place nobody looks.

Threat **T-01-09** in plan 01-03's register names this test as its mitigation.

Nothing here may ``skip``. ``visoswap/processors/context.py`` imports only the
standard library on purpose, so it loads on any interpreter -- including one with
none of the engine dependencies. If this file cannot import it, that is a
failure, not a reason to stand down.
"""

import ast
import dataclasses
from pathlib import Path

from visoswap.processors.context import EngineContext

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The seven attributes that survive the ``main_window`` cull, per
#: ``.planning/phases/01-vendor-the-engine-strip-qt/01-CONTEXT-SURFACE.md``.
#: The other eight die with ``video_processor.py``, which is never vendored.
EXPECTED_FIELDS = frozenset(
    {
        "control",
        "parameters",
        "target_faces",
        "models_processor",
        "dfm_models_data",
        "swap_faces_enabled",
        "edit_faces_enabled",
    }
)


def test_engine_context_is_a_dataclass():
    """Guard against the rest of this file passing over a plain class.

    ``dataclasses.fields()`` raises on a non-dataclass, but a future refactor to
    something with a ``__dataclass_fields__``-shaped attribute would not. Say
    what is expected rather than inferring it from a call that happened to work.
    """
    assert dataclasses.is_dataclass(EngineContext), (
        "EngineContext is no longer a dataclass; the field-surface assertions "
        "below no longer mean what they say."
    )


def test_engine_context_carries_exactly_the_kept_fields():
    """No more, no fewer. Both directions are failures worth naming."""
    actual = {f.name for f in dataclasses.fields(EngineContext)}

    added = sorted(actual - EXPECTED_FIELDS)
    removed = sorted(EXPECTED_FIELDS - actual)

    assert not added, (
        "EngineContext gained {}. Adding a field widens the engine's input "
        "surface, which is the mechanism by which the main_window god object "
        "returns. If the addition is genuinely right, update "
        "01-CONTEXT-SURFACE.md with the reason first, then this test.".format(added)
    )
    assert not removed, (
        "EngineContext lost {}. Something in the vendored tree reads each of "
        "these; removing one breaks it at first call rather than at import, "
        "which is the worst place to find out.".format(removed)
    )


def test_no_qt_type_in_the_field_definitions():
    """The point of this object is that no Qt type crosses the boundary.

    Annotations are strings here (``from __future__ import annotations``), so
    this reads them as text -- which is the right level anyway: a Qt name in an
    annotation is a Qt name in the file.
    """
    qt_names = (
        "PySide",
        "PyQt",
        "qtpy",
        "shiboken",
        "QObject",
        "QWidget",
        "QPixmap",
        "Signal",
        "Slot",
    )
    offenders = [
        (f.name, str(f.type))
        for f in dataclasses.fields(EngineContext)
        if any(q.lower() in str(f.type).lower() for q in qt_names)
    ]
    assert not offenders, "Qt types found in EngineContext fields: {}".format(offenders)


def test_mutable_defaults_are_not_shared_between_instances():
    """A bare ``{}`` default would make every context alias one dict.

    ``dataclass`` refuses a literal mutable default, but a
    ``default_factory=SHARED_DICT.copy``-style mistake, or a later hand-written
    ``__init__``, would not be refused. Two engines silently sharing one settings
    dict is the kind of bug that only shows up under concurrency, which is
    exactly where the scheduler will put it.
    """
    a, b = EngineContext(), EngineContext()
    for name in ("control", "parameters", "target_faces", "dfm_models_data"):
        assert getattr(a, name) is not getattr(b, name), (
            "{} is shared between EngineContext instances".format(name)
        )
        assert getattr(a, name) == {}, "{} does not default empty".format(name)


def test_default_construction_is_inert():
    """``EngineContext()`` must construct with no arguments and reach nothing.

    Phase 2 builds one before it has a models processor, and the tests below it
    build one holding nothing at all.
    """
    context = EngineContext()
    assert context.models_processor is None
    assert context.swap_faces_enabled is False
    assert context.edit_faces_enabled is False


def test_surface_document_names_every_kept_field():
    """The doc is the contract; the dataclass is the implementation.

    Plan 01-03 records the drift risk explicitly: Phase 2's Engine API and Phase
    3's settings tiers are both designed against 01-CONTEXT-SURFACE.md, so if the
    two diverge, Phase 3 designs tiers around fields that do not exist. Cheap to
    check, expensive to discover two phases later.

    The document is located by name rather than by a fixed path so that archiving
    the phase directory relocates it instead of breaking this test.
    """
    matches = sorted((REPO_ROOT / ".planning").rglob("01-CONTEXT-SURFACE.md"))
    assert matches, (
        "01-CONTEXT-SURFACE.md not found anywhere under .planning/. It is a "
        "required artifact of phase 01 and the contract Phase 2 is built "
        "against; if it was deleted rather than moved, that is the finding."
    )

    text = matches[0].read_text(encoding="utf-8")
    missing = sorted(name for name in EXPECTED_FIELDS if name not in text)
    assert not missing, (
        "EngineContext fields absent from {}: {}".format(
            matches[0].relative_to(REPO_ROOT), missing
        )
    )



def _context_reads(path):
    """Every ``<x>.context.<attr>`` attribute name read in one source file.

    Parsed rather than imported: this file must load on an interpreter with no
    engine dependencies, and ``models_processor`` needs torch and onnxruntime.

    Scoped to files that name ``EngineContext``, because ``self.context`` is not
    a unique name in the vendored tree: ``utils/tensorrt_predictor.py`` uses it
    for a TensorRT execution context and reads ``set_tensor_address``,
    ``execute_v2`` and friends off it. Those are nothing to do with this
    boundary. Keying on the import is what tells the two apart, and it picks up
    ``frame_worker.py`` automatically when plan 01-04 lands it.
    """
    source = path.read_text(encoding="utf-8")
    if "EngineContext" not in source:
        return set()
    tree = ast.parse(source)
    reads = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if isinstance(base, ast.Attribute) and base.attr == "context":
            reads.add(node.attr)
        elif isinstance(base, ast.Name) and base.id == "context":
            reads.add(node.attr)
    return reads


def test_every_context_read_in_the_vendored_tree_resolves_to_a_field():
    """The consumer side of the contract, which the field list alone does not cover.

    ``test_engine_context_carries_exactly_the_kept_fields`` pins what the context
    *offers*. This pins what the vendored code *asks for*. The two can diverge in
    the direction that hurts: a read of a field that was dropped raises
    ``AttributeError`` at first call, deep inside model loading, with no import-time
    warning -- and Phase 1 executes none of these paths, so nothing else here would
    catch it. Plan 01-03 names exactly this as the risk in its key_links.

    Plan 01-04 rewrites roughly fifteen more of these reads in ``frame_worker.py``;
    this test covers them the moment that file lands, with no edit.
    """
    field_names = {f.name for f in dataclasses.fields(EngineContext)}

    offenders = {}
    files_read = 0
    for path in sorted((REPO_ROOT / "visoswap").rglob("*.py")):
        reads = _context_reads(path)
        if reads:
            files_read += 1
        unknown = sorted(reads - field_names)
        if unknown:
            offenders[str(path.relative_to(REPO_ROOT))] = unknown

    assert files_read, (
        "No EngineContext consumer under visoswap/ reads self.context at all. "
        "tree lost its EngineContext consumers or this scan stopped finding them; "
        "either way it is passing vacuously."
    )
    assert not offenders, (
        "Vendored code reads context attributes that EngineContext does not "
        "define: {}. Either the read should have been dropped with the rest of "
        "the main_window surface, or 01-CONTEXT-SURFACE.md is wrong about what "
        "is kept.".format(offenders)
    )
