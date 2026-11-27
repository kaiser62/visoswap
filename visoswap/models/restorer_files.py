"""Which restorer model files the project settings need, and which are absent.

Qt-free and model-free on purpose: this is a presence check over
``visoswap/models/models_data.py``, so the backend can refuse a run before an
engine is built and before any frame is rendered. Nothing here loads a model,
opens an image or touches a GPU.

Why this exists: with alignment ``Reference`` and no ``res50.onnx`` the restorer
used to print an exception and return the face untouched, so a run "succeeded"
while restoring nothing. With ``Original`` or ``Blend`` and no restorer weights
it failed deep inside ONNX Runtime with a path the user could not act on. Both
now fail early, here.
"""

from __future__ import annotations

from pathlib import Path

#: Restorer type selection -> the model name ``face_restorers.py`` loads for it.
#: Confirmed against the ``run_*`` methods, not guessed: ``run_GFPGAN`` loads
#: ``GFPGANv1.4``, ``run_codeformer`` loads ``CodeFormer``, ``run_GPEN_256``
#: loads ``GPENBFR256`` (and so on), ``run_RestoreFormerPlusPlus`` loads
#: ``RestoreFormerPlusPlus``, ``run_VQFR_v2`` loads ``VQFRv2``.
TYPE_TO_MODEL = {
    "GFPGAN-v1.4": "GFPGANv1.4",
    "CodeFormer": "CodeFormer",
    "GPEN-256": "GPENBFR256",
    "GPEN-512": "GPENBFR512",
    "GPEN-1024": "GPENBFR1024",
    "GPEN-2048": "GPENBFR2048",
    "RestoreFormer++": "RestoreFormerPlusPlus",
    "VQFR-v2": "VQFRv2",
}

#: The alignment that runs a second landmark model besides the swapper's own.
REFERENCE_ALIGNMENT = "Reference"

#: Loaded by ``Reference``: ``res50.onnx``, the model the default subset lacks.
REFERENCE_LANDMARK_MODEL = "FaceLandmark5"

#: (enable toggle, type selection, alignment selection) per restorer slot.
_SLOTS = (
    ("FaceRestorerEnableToggle", "FaceRestorerTypeSelection", "FaceRestorerDetTypeSelection"),
    ("FaceRestorerEnable2Toggle", "FaceRestorerType2Selection", "FaceRestorerDetType2Selection"),
)


def required_restorer_models(settings: dict) -> list[str]:
    """Model names the enabled restorer slots need, in slot order, deduplicated."""
    required: list[str] = []

    def _add(name: str) -> None:
        if name not in required:
            required.append(name)

    for enable_key, type_key, det_key in _SLOTS:
        if not settings.get(enable_key):
            continue
        model = TYPE_TO_MODEL.get(settings.get(type_key))
        if model:
            _add(model)
        if settings.get(det_key) == REFERENCE_ALIGNMENT:
            _add(REFERENCE_LANDMARK_MODEL)
    return required


def _model_paths(models_list=None) -> dict[str, str]:
    """``{model name: local path}``.

    ``models_data`` reads ``MODELS_DIR`` at import, so the list is looked up at
    call time and can be injected: tests pass a list whose paths point at a
    temporary directory instead of patching the environment.
    """
    if models_list is None:
        from visoswap.models import models_data

        models_list = models_data.models_list
    return {entry["model_name"]: entry["local_path"] for entry in models_list}


def missing_restorer_files(settings: dict, models_list=None) -> list[str]:
    """Local paths of the restorer files the enabled slots need but cannot find.

    Empty when no restorer is enabled, or when every needed file is present.
    Presence is all that is checked; hashing is deliberately left to the
    bootstrap gate, which already owns it.
    """
    paths = _model_paths(models_list)
    missing: list[str] = []
    for name in required_restorer_models(settings):
        path = paths.get(name)
        if path is None or not Path(path).is_file():
            missing.append(path or name)
    return missing
