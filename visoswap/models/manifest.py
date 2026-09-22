"""A derived view over the vendored model lists, and the only reader of them.

This module is project-authored (exempted from the attribution gate in
``tests/test_vendor_headers.py``). It is the one place that reads
``visoswap/models/models_data.py``; every other consumer — the bootstrap verifier
in ``bootstrap.py``, the startup gate, the schema's DFM scan — sees the model set
through this view. A second reader is how two ideas of "the model set" start
disagreeing, which is exactly the disagreement the roadmap's own "56-plus-6"
wording is trying to avoid.

The tracked set is read from the vendored lists **at call time**, not snapshotted
at import. ``models_trt_list`` is either 6 entries or empty depending on whether
``import tensorrt`` succeeded in the current process, so a constant computed at
import would answer for the wrong interpreter — a test process that imports this
module would freeze the 56-entry shape forever. Reading the lists on every call
is what makes "derived from the manifest at runtime, never a hardcoded count" a
property of the code rather than a promise in a comment. Nowhere here, and in no
test of this module, does the literal 56, 6, 62 or 12 appear as an expectation.

**The required/optional partition is a stated rule, not a list of names.** An
entry is *required* exactly when it can be fetched *and* the execution provider
that consumes it is one ``visoswap/engine.py`` accepts. Today that classifies the
six TensorRT engine caches as optional: they carry no download URL (so nothing
can supply them), and the only provider that reads a ``.trt`` file is TensorRT,
which the engine refuses in its constructor because its provider options write a
relative cache path. A missing ``.trt`` therefore cannot produce the mid-inference
failure the bootstrap exists to prevent — the very test of whether something
belongs in the required set. If a later phase re-enables TensorRT, flipping the
accepted-provider set flips those six back into required with no edit here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from visoswap.models import models_data
from visoswap.schema import resolve_models_dir

__all__ = ["ManifestEntry", "tracked", "required_entries", "optional_entries", "default_entries", "DEFAULT_SWAP_MODELS"]

#: The minimal set of models required for default face swap (RetinaFace detector,
#: 4 ArcFace embedders for face card generation plus the CSCS ID adapter that
#: ``recognize_cscs`` always loads alongside CSCSArcFace, and Inswapper128 engine).
DEFAULT_SWAP_MODELS = frozenset({
    "RetinaFace",
    "Inswapper128ArcFace",
    "SimSwapArcFace",
    "GhostArcFace",
    "CSCSArcFace",
    "CSCSIDArcFace",
    "Inswapper128",
})

#: The execution providers ``visoswap/engine.py`` accepts. Mirrored here rather
#: than imported from ``engine.py`` so this module stays importable with no torch
#: or cv2 installed — the manifest is read by the startup gate, which has to run
#: on an interpreter that may not carry the inference stack. The engine's
#: ``SAFE_PROVIDERS`` and this tuple are the same contract; both must be flipped
#: together if TensorRT is ever re-enabled.
ACCEPTED_PROVIDERS = frozenset({"CUDA", "CPU"})

#: The file extension of a TensorRT engine cache. These are the only artifacts
#: whose consuming provider is TensorRT rather than an accepted ONNX Runtime
#: provider.
TENSORRT_SUFFIX = ".trt"


def _consuming_provider(path: Path) -> str:
    """The execution provider that would load the artifact at ``path``.

    A ``.trt`` file is a TensorRT engine cache, loadable only by the TensorRT
    provider. Everything else in the manifest is an ONNX file consumed by the
    accepted CUDA/CPU providers.
    """
    return "TensorRT" if path.suffix.lower() == TENSORRT_SUFFIX else "CUDA"


@dataclass(frozen=True)
class ManifestEntry:
    """One tracked model file: where it is, what it should hash to, how to get it.

    ``required`` is derived from a rule (fetchable AND consumable), never from a
    list of names — so the day a seventh unfetchable entry appears upstream it is
    classified by that rule rather than missed.
    """

    name: str
    path: Path
    digest: str
    url: Optional[str]

    @property
    def fetchable(self) -> bool:
        """Whether the manifest supplied a way to download this entry."""
        return bool(self.url)

    @property
    def consumable(self) -> bool:
        """Whether the provider that loads this entry is one the engine accepts."""
        return _consuming_provider(self.path) in ACCEPTED_PROVIDERS

    @property
    def required(self) -> bool:
        """True when something could supply this file and something could load it.

        ``fetchable and consumable``. An entry that cannot be fetched or cannot
        be loaded by an accepted provider must not block a working install from
        starting — that is the whole point of the optional class today.
        """
        return self.fetchable and self.consumable


def _resolve_one(models_dir: Path, local_path: str) -> Path:
    """Resolve a vendored ``local_path`` under ``models_dir``, traversal refused.

    The vendored paths are written as ``./model_assets/<tail>``. We strip the
    vendored ``models_dir`` prefix (``models_data.models_dir``) and join the tail
    under the caller-supplied directory — then assert the result is inside that
    directory, so a manifest entry containing traversal cannot make the verifier
    stat or hash a file elsewhere. The manifest is vendored and currently benign;
    the check costs nothing and the alternative is trusting a file this project
    does not author.
    """
    vendor_prefix = Path(models_data.models_dir)
    vendored = Path(local_path)
    try:
        relative = vendored.relative_to(vendor_prefix)
    except ValueError:
        # A path that does not start with the vendored prefix (e.g. a future
        # absolute-path entry) falls back to its tail; the containment assertion
        # below still guards it.
        relative = Path(vendored.name)
    resolved = (models_dir / relative).resolve()
    if not resolved.is_relative_to(models_dir.resolve()):
        raise ValueError(
            "manifest entry {!r} resolves outside the models directory {}: {} "
            "(refusing traversal)".format(local_path, models_dir, resolved)
        )
    return resolved


def tracked(models_dir: Optional[os.PathLike] = None) -> list[ManifestEntry]:
    """Every entry in the manifest, resolved against a models directory.

    Reads ``models_list`` and ``models_trt_list`` from the vendored data on every
    call, so the tracked set moves with the interpreter's TensorRT availability.
    The directory resolves argument-first, then ``MODELS_DIR``, then the
    repository default — the same order ``visoswap/schema/__init__.py`` already
    uses, imported and reused rather than restated.
    """
    directory = resolve_models_dir(models_dir)
    entries: list[ManifestEntry] = []
    for record in list(models_data.models_list) + list(models_data.models_trt_list):
        entries.append(
            ManifestEntry(
                name=record["model_name"],
                path=_resolve_one(directory, record["local_path"]),
                digest=record["hash"],
                url=record.get("url"),
            )
        )
    return entries


def required_entries(models_dir: Optional[os.PathLike] = None) -> list[ManifestEntry]:
    """The subset of the manifest that must be present for the backend to start."""
    return [entry for entry in tracked(models_dir) if entry.required]


def optional_entries(models_dir: Optional[os.PathLike] = None) -> list[ManifestEntry]:
    """The subset the manifest deliberately does not require."""
    return [entry for entry in tracked(models_dir) if not entry.required]


def default_entries(models_dir: Optional[os.PathLike] = None) -> list[ManifestEntry]:
    """The minimal subset of models needed for default face swapping."""
    return [entry for entry in tracked(models_dir) if entry.name in DEFAULT_SWAP_MODELS]

