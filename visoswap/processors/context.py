"""The Qt-free replacement for VisoMaster's ``main_window`` god object.

Upstream, the processor tree reaches back into the Qt main window for settings,
detected faces, model metadata and two toggle-button states. Fifteen distinct
attributes across three files. Eight of them die with ``video_processor.py``,
which VisoSwap drops whole; the remaining seven are carried here as plain data.

The full enumeration, with per-file line evidence and a kept/dropped verdict for
each attribute, lives in
``.planning/phases/01-vendor-the-engine-strip-qt/01-CONTEXT-SURFACE.md``. Read
that before changing anything in this file.

This module is project-authored, not vendored from VisoMaster, so it carries no
attribution header -- ``tests/test_vendor_headers.py`` exempts it explicitly.

**Do not add fields casually.** ``tests/test_context_surface.py`` pins the field
set at exactly seven names. That test is the thing standing between this object
and the god object it replaced; widening the surface is meant to cost a reviewed
edit to a test, not a one-line addition to a dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    # Runtime import would be a cycle: models_processor imports EngineContext.
    from visoswap.processors.models_processor import ModelsProcessor

__all__ = ["EngineContext"]


@dataclass
class EngineContext:
    """Everything the engine needs from its caller, and nothing else.

    Every field replaces a ``main_window.<attr>`` read in the vendored code.
    No field is, holds, or constructs a Qt object.
    """

    #: Global-tier settings. Replaces ``main_window.control``; read at
    #: ``models_processor.py`` upstream line 150 and at two sites in
    #: ``frame_worker.py``. Phase 3's global tier.
    control: dict[str, Any] = field(default_factory=dict)

    #: Project-tier settings, keyed by face id. Replaces
    #: ``main_window.parameters``. Phase 3's project tier.
    parameters: dict[Any, Any] = field(default_factory=dict)

    #: The detected-face store the swap pipeline iterates. Replaces
    #: ``main_window.target_faces``.
    target_faces: dict[Any, Any] = field(default_factory=dict)

    #: The engine's own model registry. Replaces
    #: ``main_window.models_processor`` -- the engine reaching itself through
    #: the host window, which is exactly the coupling this object removes.
    models_processor: Optional["ModelsProcessor"] = None

    #: DFM model metadata, name -> descriptor. Replaces
    #: ``main_window.dfm_models_data``, read at ``models_processor.py``
    #: upstream line 159.
    #:
    #: This context carries the dict; it does not populate it. Upstream it is
    #: filled by ``get_dfm_models_data()`` in ``app/helpers/miscellaneous.py``,
    #: which plan 01-02 deliberately did not vendor. Who fills it is Phase 2's
    #: call -- see note 2 of ``01-CONTEXT-SURFACE.md``.
    dfm_models_data: dict[str, Any] = field(default_factory=dict)

    #: Replaces ``main_window.swapfacesButton.isChecked()``. The upstream
    #: attribute is a Qt toggle button, but every read of it is ``.isChecked()``
    #: and nothing else, so a plain bool is a faithful substitution.
    swap_faces_enabled: bool = False

    #: Replaces ``main_window.editFacesButton.isChecked()``. Same reasoning.
    edit_faces_enabled: bool = False
