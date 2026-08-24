"""The public face of VisoSwap: three methods, one detected-face record.

This module is project-authored, not vendored from VisoMaster, so it carries no
attribution header -- ``tests/test_vendor_headers.py`` exempts it explicitly,
the same way it exempts ``processors/context.py``.

``Engine`` is the whole API. ``load`` binds a video, ``detect_faces`` finds the
people in it, ``swap`` returns one swapped frame as a BGR ndarray. Everything
else on the class is underscore-prefixed and
``tests/test_engine_surface.py`` enforces that, because these three signatures
are what Phase 4's backend and Phase 5's web layer are written against.

**Path validation is the caller's responsibility (threat T-02-07).** ``load``
and ``swap`` take filesystem paths and open them. This is a library with no
notion of a project root, so it cannot tell a legitimate path from a traversal;
the check belongs at the boundary that *does* know, which is Phase 4's generator.
Phase 4 inherits this acceptance deliberately -- it is in this phase's threat
register so that it is carried rather than forgotten.

**The execution provider is pinned away from TensorRT on purpose (T-02-09).**
``ModelsProcessor`` defaults to ``TensorRT``, whose provider options write an
engine and a timing cache into a *relative* ``tensorrt-engines/`` directory. Run
from the wrong working directory that writes into read-only source material --
there is a nested ``tensorrt-engines/tensorrt-engines/`` under the VisoMaster
checkout that is the signature of exactly that accident. So the constructor
switches the provider before any model is loaded and refuses any provider that
would write a cache. CUDA measured 21.2 fps against TensorRT's 27.8 at 1080p in
the reference implementation; that is a deliberate trade, and a later phase can
revisit it by making the cache path absolute first.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

import cv2
import numpy as np
import torch
from torchvision.transforms import v2

from visoswap.processors.context import EngineContext
from visoswap.processors.models_processor import ModelsProcessor
from visoswap.processors.workers.frame_worker import FrameWorker

__all__ = ["Engine", "FaceCard", "RECOGNITION_MODELS", "SAFE_PROVIDERS"]

#: Every ArcFace recogniser the swappers can ask a face card for, in the order
#: upstream's ``RecognitionModelSelection`` dropdown lists them.
#:
#: A card is populated under **all four**, not only the one detection ran under:
#: ``frame_worker`` reads ``get_embedding(get_arcface_model(SwapModelSelection))``
#: at swap time, and the swapper the caller picks need not map to the recogniser
#: detection used. A card holding one embedding works until someone selects
#: SimSwap512, at which point ``get_embedding`` returns an empty array and the
#: swap silently degrades rather than failing.
RECOGNITION_MODELS = (
    "Inswapper128ArcFace",
    "SimSwapArcFace",
    "GhostArcFace",
    "CSCSArcFace",
)

#: Providers that write nothing to disk. ``switch_providers_priority`` also
#: accepts ``TensorRT`` and ``TensorRT-Engine``; both are refused here because
#: their options carry relative cache paths -- see the module docstring.
SAFE_PROVIDERS = ("CUDA", "CPU")

DEFAULT_PROVIDER = "CUDA"

#: Fractions of the clip sampled when the caller's frame yields no face.
#: Fractions rather than fixed indices, so the spread means the same thing on a
#: ten-second clip and a ninety-minute one.
DETECT_SAMPLE_FRACTIONS = (0.02, 0.1, 0.25, 0.5, 0.75)

MERGE_METHODS = ("Mean", "Median")


@dataclass
class FaceCard:
    """One detected face: embeddings, a crop, and the source assigned to it.

    Deliberately three members and no more. ``frame_worker`` is 1,292 lines and
    touches exactly ``face_id``, ``get_embedding(...)`` and
    ``assigned_input_embedding`` on the values of the target-face mapping --
    counted, not assumed. Upstream's ``TargetFaceCardButton`` also carries a
    media path, a Qt checkable state, a context menu, an assigned-input-faces
    dict and an assigned-merged-embeddings dict, and the swap pipeline reads
    none of it. Anything added here beyond these members is the god object
    growing back.

    ``crop`` is stored **BGR**, matching upstream, which flips
    ``run_recognize_direct``'s RGB output before storing it on the card. This is
    not cosmetic: the reference implementation's gender classifier flips it back
    to RGB before inference, and its docstring records that feeding the stored
    BGR straight through flipped a face's label between adjacent frames.
    """

    #: Recognition-model name -> embedding. Populated for all of
    #: :data:`RECOGNITION_MODELS`, not only the detection-time one.
    embedding_store: dict[str, np.ndarray]

    #: The aligned face crop, HxWx3 **BGR** uint8. See the class docstring.
    crop: np.ndarray

    #: The recogniser detection ran under. ``face_id`` is derived from the
    #: embedding stored under this name, so it is part of the identity.
    recognition_model: str

    #: ArcFace-model name -> the merged source embedding assigned to this face.
    #: Read by ``frame_worker`` at the swap call; empty means "no source
    #: assigned", which the worker treats as a no-op swap rather than an error.
    assigned_input_embedding: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def face_id(self) -> str:
        """A stable, content-addressed identifier derived from the embedding.

        Upstream's ``face_id`` is an incrementing integer handed out by the Qt
        widget that owns the card, which is precisely the identifier the roadmap
        forbids. Deriving it from the recognition embedding instead means the
        same face in the same video always keys the same way, across processes
        and across runs, with no UI anywhere in the chain -- and it is the key
        Phase 3's per-face settings tier needs.

        **A near-duplicate embedding produces a different key.** Two detections
        of one person, one frame apart, differ in the last few bits and hash to
        two ids. What stops that mattering is the cosine-distance dedupe in
        ``Engine.detect_faces``: a face similar enough to an existing card never
        becomes a second card, so two ids for one person are never both live.
        Phase 3 replaces exact-digest keying with threshold matching, at which
        point the dedupe stops being the only thing holding this together.
        """
        embedding = self.embedding_store.get(self.recognition_model)
        if embedding is None or np.size(embedding) == 0:
            raise ValueError(
                "FaceCard has no embedding under its own detection model "
                "{!r}; its identity cannot be derived. Cards must be built by "
                "Engine.detect_faces.".format(self.recognition_model)
            )
        digest = hashlib.blake2b(digest_size=16)
        digest.update(self.recognition_model.encode("utf-8"))
        digest.update(np.ascontiguousarray(embedding, dtype=np.float32).tobytes())
        return digest.hexdigest()

    def get_embedding(self, embedding_swap_model: str) -> np.ndarray:
        """The stored embedding, or an empty array.

        An empty array rather than ``None`` or a ``KeyError``, matching upstream
        exactly: ``frame_worker`` feeds the result straight into
        ``findCosineDistance``, which ``ravel()``s it, and an empty vector there
        yields ``nan`` similarity that fails the threshold. Returning ``None``
        would raise inside the comparison instead, a long way from the cause.
        """
        return self.embedding_store.get(embedding_swap_model, np.array([]))

    def assign(
        self,
        source_stores: Sequence[Mapping[str, np.ndarray]],
        merge_method: str = "Mean",
    ) -> None:
        """Merge source embedding stores into ``assigned_input_embedding``.

        The same shape as upstream's ``calculate_assigned_input_embedding``:
        collect every model any store mentions, then reduce across the stores
        that have it. With one source the merge is the identity -- the mean of
        one is one -- and it is implemented as a merge anyway because Phase 3
        assigns more than one and a shortcut would have to be unwritten then.

        Upstream leaves ``assigned_input_embedding`` *untouched* when the merge
        method is neither ``Mean`` nor ``Median``, because a Qt combo box can
        only ever hold one of the two. Here the value arrives from an
        unvalidated caller mapping, so an unknown method raises (T-02-11).
        """
        if merge_method not in MERGE_METHODS:
            raise ValueError(
                "unknown embedding merge method {!r}; expected one of {}".format(
                    merge_method, ", ".join(MERGE_METHODS)
                )
            )
        stores = [store for store in source_stores if store]
        if not stores:
            self.assigned_input_embedding = {}
            return
        reduce = np.mean if merge_method == "Mean" else np.median
        models: set[str] = set()
        for store in stores:
            models.update(store.keys())
        self.assigned_input_embedding = {
            model: reduce(
                [store[model] for store in stores if model in store], axis=0
            )
            for model in sorted(models)
        }


def _apply_overrides(
    base: Mapping[str, Any], overrides: Optional[Mapping[str, Any]]
) -> dict[str, Any]:
    """``base`` with ``overrides`` applied, refusing any key ``base`` lacks.

    An override introducing a key the tier does not have is a typo, and a typo
    that lands silently in a settings dict surfaces as a ``KeyError`` deep inside
    a tensor operation many frames later. Fail at the boundary instead (T-02-11).
    Full typed validation is SCHEMA-01, in Phase 3.
    """
    merged = dict(base)
    if not overrides:
        return merged
    unknown = sorted(key for key in overrides if key not in merged)
    if unknown:
        raise KeyError(
            "settings override names {} unknown key(s): {}".format(
                len(unknown), ", ".join(unknown)
            )
        )
    merged.update(overrides)
    return merged


def _processing_frame(frame: np.ndarray, target_width: Optional[int]) -> np.ndarray:
    """``frame`` resized down to ``target_width``, or ``frame`` itself.

    Module level and private: ``tests/test_engine_surface.py`` counts every
    non-underscore name a *class body* binds, and the resize is not something a
    caller reaches for anyway -- it is what ``swap`` does at the frame boundary.

    Never upscales. A target at or above the native width returns the identical
    object, mirroring ``backend.workers.generation_worker._processing_width``'s
    rule so the two paths cannot disagree about what one project row means.
    Both dimensions are forced even: odd dimensions break the encoders the
    recorder feeds.
    """
    if not target_width:
        return frame
    native_height, native_width = frame.shape[:2]
    width = int(target_width) // 2 * 2
    if width <= 0 or width >= native_width:
        return frame
    height = max(2, int(round(native_height * width / native_width)) // 2 * 2)
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


class Engine:
    """A loaded video, the faces in it, and the swapper that rewrites them.

    Three public methods and nothing else. See the module docstring for the
    provider pin and for who owns path validation.
    """

    def __init__(
        self,
        device: str = "cuda",
        global_settings: Optional[Mapping[str, Any]] = None,
        project_settings: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Build the models processor and pin the provider.

        ``device`` is the torch device the processor stages tensors on;
        ``global_settings`` and ``project_settings`` are Phase 3's global and
        project tiers, taken here as plain mappings because the typed schema
        does not exist until that phase.

        The context is built first and handed to ``ModelsProcessor``, which
        keeps a reference to it -- the processor reads ``control`` and
        ``dfm_models_data`` back off the context at load time.

        ``dfm_models_data`` stays empty. Populating it means scanning a models
        directory for ``.dfm`` files, of which there are none on this machine,
        and inventing that scan now would be inventing it blind. That is a
        decided question, not an open one: see
        ``.planning/phases/02-engine-api-first-swap/02-DECISION-deferred-paths.md``,
        which assigns the populator to Phase 4's model bootstrap.
        """
        self._project_settings: dict[str, Any] = dict(project_settings or {})
        self.context = EngineContext(
            control=dict(global_settings or {}),
            parameters={},
            target_faces={},
            models_processor=None,
            dfm_models_data={},
            swap_faces_enabled=True,
            edit_faces_enabled=False,
        )
        self.context.models_processor = ModelsProcessor(self.context, device=device)

        provider = self.context.control.get("ProvidersPrioritySelection", DEFAULT_PROVIDER)
        if provider not in SAFE_PROVIDERS:
            raise ValueError(
                "refusing execution provider {!r}: its provider options write an "
                "engine and timing cache to the relative path 'tensorrt-engines', "
                "which lands wherever the process happens to be. Use one of {} "
                "until a phase makes that path absolute.".format(
                    provider, ", ".join(SAFE_PROVIDERS)
                )
            )
        self.context.models_processor.switch_providers_priority(provider)

        threads = self.context.control.get("nThreadsSlider")
        if threads is not None:
            self.context.models_processor.set_number_of_threads(int(threads))

        # One decoder, one position, guarded so several swap threads can share
        # it. Rebinding is exclusive with reads for the same reason.
        self._reader: Optional[Any] = None
        self._reader_lock = threading.Lock()
        self._video_path: Optional[str] = None
        self._fps: float = 0.0
        self._frame_count: int = 0
        self._reader_pos: int = -1

        # Memoised source-image embedding stores, keyed by
        # (realpath, st_mtime_ns, st_size). ``swap`` asks for the same source
        # image's store once per frame, and the four-call detection sequence
        # behind it never changes its answer while the file is untouched --
        # measuring that tax is what docs/benchmark-baseline.md calls a 52%
        # throughput loss. The stat rides in the key so an in-place file
        # replacement can never be served from here, and ``load`` clears the
        # memo on rebind so an embedding computed under one project's resolved
        # parameter tier cannot survive into another. Private by construction:
        # ``tests/test_engine_surface.py`` counts plain assignments as surface.
        self._source_store_cache: dict[
            tuple[str, int, int], dict[str, np.ndarray]
        ] = {}

    # -- public API -------------------------------------------------------

    def load(self, video_path: str) -> dict[str, Any]:
        """Bind a video. Returns its path, frame rate and frame count.

        No detection happens here. Opening and detecting are separate because
        detection is the expensive half and a caller may want to choose which
        frame it runs on.
        """
        path = str(video_path)
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError("could not open video {}".format(path))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0:
            capture.release()
            raise RuntimeError("video {} reports no frame rate".format(path))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        with self._reader_lock:
            if self._reader is not None:
                self._reader.release()
            self._reader = capture
            self._video_path = path
            self._fps = fps
            self._frame_count = frame_count
            self._reader_pos = 0

        # A rebind invalidates everything keyed off the previous video.
        self.context.target_faces.clear()
        self.context.parameters.clear()
        # The embedding stores too: they are computed under the project tier
        # resolved at detect time, and a rebind can change that tier, so a
        # store carried across it would be an answer to the wrong question.
        self._source_store_cache.clear()
        return {"path": path, "fps": fps, "frame_count": frame_count}

    def detect_faces(self, frame_number: int = 0) -> list[FaceCard]:
        """Detect the faces in the bound video and register them.

        Sampling does not stop at ``frame_number``. Frame 0 is routinely a black
        frame, a title card or an empty establishing shot; the reference
        implementation recorded a whole 96-second clip failing *every* frame
        with "no target faces detected" for exactly that reason. So the caller's
        frame is tried first and then a spread across the clip, and the first
        frame that yields a face wins.

        The cards are written into the context's target-face mapping keyed by
        ``face_id``, and each gets its own entry in the context's parameters
        mapping holding the full project tier. That per-card entry is not
        optional: ``frame_worker`` indexes ``parameters[target_face.face_id]``
        directly, so a missing entry raises rather than falling back to some
        global set -- which is correct, since the settings a swap reads are the
        settings of the *target face*.
        """
        with self._reader_lock:
            if self._reader is None:
                raise RuntimeError("engine is not bound to a video; call load() first")

        cards: list[FaceCard] = []
        for candidate in self._detect_candidates(frame_number):
            frame = self._read_frame(candidate)
            cards = self._detect_in_image(frame)
            if cards:
                break

        self.context.target_faces.clear()
        self.context.parameters.clear()
        for card in cards:
            face_id = card.face_id
            self.context.target_faces[face_id] = card
            self.context.parameters[face_id] = dict(self._project_settings)
        return cards

    def swap(
        self,
        frame_number: int,
        source_path: str,
        settings: Optional[Mapping[str, Any]] = None,
        target_width: Optional[int] = None,
    ) -> np.ndarray:
        """Swap the source face onto every detected face of one frame.

        Returns a contiguous **BGR** ndarray, the same convention
        ``cv2.imread`` and ``cv2.imwrite`` use, so a caller can write the result
        without thinking about channel order.

        ``settings`` is applied over the project tier for every card and every
        key must already exist there.

        ``target_width`` runs the whole pipeline -- detection, landmarks,
        paste-back and the colour stages -- on a frame downscaled to that width,
        then resizes the result back to the media's native dimensions before
        returning (D-15b). A caller therefore buys time without changing the
        size of the frame the recorder and the overlay receive; a target at or
        above the native width is ignored rather than upscaled. It is a keyword
        with a default, so the published three-method surface is unchanged.

        The frame is driven through ``FrameWorker.process_frame()`` directly
        rather than through ``run()``. Plan 01-04 deleted the display path out of
        ``run()``, leaving the processed frame on the worker instance with no
        exit route; this method *is* that exit route, and it is the seam between
        Phase 1 and Phase 2.
        """
        if not self.context.target_faces:
            raise RuntimeError(
                "no target faces registered; call detect_faces() before swap()"
            )
        source_store = self._source_embedding_store(source_path)
        merge_method = self.context.control.get("EmbMergeMethodSelection", "Mean")
        parameters = _apply_overrides(self._project_settings, settings)

        for face_id, card in self.context.target_faces.items():
            card.assign([source_store], merge_method)
            self.context.parameters[face_id] = dict(parameters)

        frame_bgr = self._read_frame(frame_number)
        native_height, native_width = frame_bgr.shape[:2]
        # The resize happens at the frame boundary and nowhere else: down here,
        # back up on the way out. Everything in between is the pipeline running
        # on fewer pixels.
        frame_bgr = _processing_frame(frame_bgr, target_width)
        # FrameWorker consumes RGB and returns BGR -- see the flip at the top of
        # process_frame and the one on its return. Upstream's headless entry
        # point does this same flip on the way in.
        frame_rgb = np.ascontiguousarray(frame_bgr[..., ::-1])
        worker = FrameWorker(
            frame_rgb, self.context, int(frame_number), is_single_frame=True
        )
        worker.parameters = self.context.parameters.copy()
        worker.target_faces = self.context.target_faces
        swapped = worker.process_frame()
        if swapped.shape[1] != native_width or swapped.shape[0] != native_height:
            swapped = cv2.resize(
                swapped,
                (native_width, native_height),
                interpolation=cv2.INTER_LANCZOS4,
            )
        return np.ascontiguousarray(swapped)

    # -- internals --------------------------------------------------------

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._release()

    def _release(self) -> None:
        with self._reader_lock:
            if self._reader is not None:
                self._reader.release()
                self._reader = None
            self._reader_pos = -1

    def _detect_candidates(self, first: int) -> list[int]:
        """``first``, then a spread across the clip. Out-of-range frames dropped.

        Seeking past the end returns nothing and costs a detection pass for it.
        """
        total = self._frame_count
        candidates = [int(first)]
        if total > 0:
            candidates += [int(total * fraction) for fraction in DETECT_SAMPLE_FRACTIONS]
        seen: set[int] = set()
        ordered: list[int] = []
        for candidate in candidates:
            if candidate < 0 or (total > 0 and candidate >= total):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
        return ordered

    def _read_frame(self, frame_number: int) -> np.ndarray:
        """Decode one frame of the bound video, BGR.

        Sequential on purpose, seeking only on a jump. The reference
        implementation measured 0.230s for a seek against roughly 0.030s for
        reading the frame the decoder is already sitting on; a seek per frame is
        the difference between a usable engine and an unusable one.
        """
        wanted = int(frame_number)
        with self._reader_lock:
            if self._reader is None:
                raise RuntimeError("engine is not bound to a video; call load() first")
            if wanted != self._reader_pos:
                self._reader.set(cv2.CAP_PROP_POS_FRAMES, wanted)
                self._reader_pos = wanted
            ok, frame = self._reader.read()
            self._reader_pos += 1
            if not ok or frame is None:
                raise RuntimeError(
                    "could not read frame {} of {}".format(wanted, self._video_path)
                )
            return frame

    def _detect_in_image(self, image_bgr: np.ndarray) -> list[FaceCard]:
        """The four-call detection sequence, over one BGR image.

        Read BGR, flip to RGB, stage as a channel-first uint8 tensor on the
        processor's device, ``run_detect``, then ``run_recognize_direct`` per
        keypoint set. Established by upstream's ``find_target_faces`` and
        confirmed by the reference implementation.
        """
        processor = self.context.models_processor
        control = self.context.control.copy()

        rgb = np.ascontiguousarray(image_bgr[..., ::-1])
        img = torch.from_numpy(rgb.astype("uint8")).to(processor.device)
        img = img.permute(2, 0, 1)
        if control["ManualRotationEnableToggle"]:
            img = v2.functional.rotate(
                img,
                angle=control["ManualRotationAngleSlider"],
                interpolation=v2.InterpolationMode.BILINEAR,
                expand=True,
            )

        _bboxes, kpss_5, _kpss = processor.run_detect(
            img,
            control["DetectorModelSelection"],
            max_num=control["MaxFacesToDetectSlider"],
            score=control["DetectorScoreSlider"] / 100.0,
            input_size=(512, 512),
            use_landmark_detection=control["LandmarkDetectToggle"],
            landmark_detect_mode=control["LandmarkDetectModelSelection"],
            landmark_score=control["LandmarkDetectScoreSlider"] / 100.0,
            from_points=control["DetectFromPointsToggle"],
            rotation_angles=[0]
            if not control["AutoRotationToggle"]
            else [0, 90, 180, 270],
        )
        if len(kpss_5) == 0:
            return []

        recogniser = control["RecognitionModelSelection"]
        similarity_type = control["SimilarityTypeSelection"]
        threshold = self._project_settings["SimilarityThresholdSlider"]

        cards: list[FaceCard] = []
        for face_kps in kpss_5:
            embedding, crop = processor.run_recognize_direct(
                img, face_kps, similarity_type, recogniser
            )
            if self._already_carded(cards, embedding, recogniser, threshold):
                continue
            store = {recogniser: embedding}
            for option in RECOGNITION_MODELS:
                if option == recogniser:
                    continue
                other, _crop = processor.run_recognize_direct(
                    img, face_kps, similarity_type, option
                )
                store[option] = other
            # RGB -> BGR, matching upstream's flip before it stores the crop.
            crop_bgr = np.ascontiguousarray(crop.cpu().numpy()[..., ::-1])
            cards.append(
                FaceCard(
                    embedding_store=store,
                    crop=crop_bgr,
                    recognition_model=recogniser,
                )
            )
        return cards

    def _already_carded(
        self,
        cards: Iterable[FaceCard],
        embedding: np.ndarray,
        recogniser: str,
        threshold: float,
    ) -> bool:
        """Cosine-distance dedupe, the same measure upstream deduplicates on.

        One person detected twice in a frame -- a reflection, a rotation pass --
        must collapse to one card, or the same face gets two ``face_id``s and
        two parameter entries and is swapped twice.
        """
        processor = self.context.models_processor
        for card in cards:
            similarity = processor.findCosineDistance(
                card.get_embedding(recogniser), embedding
            )
            if similarity >= threshold:
                return True
        return False

    def _source_embedding_store(self, source_path: str) -> dict[str, np.ndarray]:
        """The embedding store of the one face in the source image.

        Memoised per ``Engine`` behind ``(realpath, st_mtime_ns, st_size)``,
        so one unmodified file is embedded once per bind instead of once per
        frame. The stat is taken on *every* call and rides in the key on
        purpose: the user replaces a face image in place, and a path-only key
        would then serve a stale identity forever. A stat failure propagates
        rather than falling back to a path-only key -- refusing to cache is
        always safe; caching a renamed file's identity is not.
        """
        path = str(source_path)
        resolved = os.path.realpath(path)
        stat = os.stat(resolved)
        key = (resolved, stat.st_mtime_ns, stat.st_size)
        memoised = self._source_store_cache.get(key)
        if memoised is not None:
            return memoised
        image = cv2.imread(path)
        if image is None:
            raise RuntimeError("could not read source image {}".format(path))
        cards = self._detect_in_image(image)
        if not cards:
            raise RuntimeError("no face detected in source image {}".format(path))
        if len(cards) > 1:
            raise RuntimeError(
                "source image {} contains {} distinct faces; a source must "
                "contain exactly one so there is no question which face is "
                "being assigned".format(path, len(cards))
            )
        store = cards[0].embedding_store
        self._source_store_cache[key] = store
        return store
