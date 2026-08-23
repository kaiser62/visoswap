"""Benchmark VisoMaster's own pipeline headlessly, for comparison against
VisoSwap's ``Engine`` numbers in ``docs/benchmark-baseline.md``.

Run on **VisoMaster's** interpreter -- not the project venvs::

    & 'D:\\Visomaster\\dependencies\\Python\\python.exe' tools/benchmark_visomaster.py

Working directory must contain a ``model_assets`` entry resolving to
``D:/Visomaster/model_assets`` (upstream resolves './model_assets' relative to
cwd). The tool reads nothing else from the install except imports; nothing is
written into ``D:/Visomaster``.

What it measures, on the SAME clip/source/settings as the VisoSwap benchmark:

* ``vm-live``   -- embed the source ONCE, then time N ``FrameWorker.process_frame``
                   loops. This is how VisoMaster's UI actually runs (a face button
                   is assigned once; the per-frame loop only swaps). It is the
                   number PROJECT.md's 21-27 fps claim refers to.
* ``reembed``   -- the same loop with the source embedding recomputed every frame
                   (full detect + all four recognisers), i.e. exactly what
                   ``Engine.swap`` does today. Expected to crater onto VisoSwap's
                   measured floor, which pins the cost on the seam, not the vendor.

Upstream's ``FrameWorker`` takes a Qt ``main_window``; this harness feeds a
stand-in exposing only what ``process_frame`` reads: ``control``, ``parameters``,
``target_faces``, ``models_processor``, ``video_processor`` (dummy) and the two
buttons as booleans (``swapfacesButton``/``editFacesButton`` with
``isChecked()``). Only ``process_frame`` is driven -- never ``run()`` -- so no
signals, queues or QApplication are needed.
"""

from __future__ import annotations

import os
import sys
import time

VISOMASTER_DIR = os.environ.get("VISOMASTER_DIR", "D:/Visomaster")
SETTINGS_FIXTURE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "engine_settings.json")
)
VIDEO = os.environ.get("VISOSWAP_TEST_VIDEO") or os.path.join(
    os.path.dirname(__file__), "..", "tests", "media", "17f0d620_rosh_generate_135bda4c9686.mp4"
)
SOURCE = os.environ.get("VISOSWAP_TEST_SOURCE") or os.path.join(
    os.path.dirname(__file__), "..", "tests", "media", "598004cb_tonima.JPG"
)
RECOGNITION_MODELS = (
    "Inswapper128ArcFace",
    "SimSwapArcFace",
    "GhostArcFace",
    "CSCSArcFace",
)
WARMUP = 3
MEASURED = 20


class _BoolButton:
    def __init__(self, value):
        self._value = bool(value)

    def isChecked(self):
        return self._value


class _Signal:
    """Qt-signal stand-in: upstream emits load progress nobody is listening to."""

    def emit(self, *args, **kwargs):
        pass


class _Dialog:
    def show(self):
        pass

    def close(self):
        pass

    def setValue(self, *args):
        pass


class FakeMainWindow:
    """Only what FrameWorker/ModelsProcessor read off main_window."""

    def __init__(self, control, parameters):
        self.control = dict(control)
        self.parameters = dict(parameters)
        self.target_faces = {}
        self.models_processor = None
        self.video_processor = None  # stored by the ctor; only run() touches it
        self.swapfacesButton = _BoolButton(True)
        self.editFacesButton = _BoolButton(False)
        self.model_loading_signal = _Signal()
        self.model_loaded_signal = _Signal()
        self.model_load_dialog = _Dialog()
        self.dfm_models_data = {}


def bench(label, detail):
    print("BENCH {} {}".format(label, " ".join(str(detail).split())), flush=True)


def stats(values):
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    return {
        "n": n,
        "mean_ms": round(mean * 1000.0, 2),
        "p50_ms": round(ordered[n // 2] * 1000.0, 2),
        "min_ms": round(ordered[0] * 1000.0, 2),
        "max_ms": round(ordered[-1] * 1000.0, 2),
        "fps": round(1.0 / mean, 2) if mean > 0 else 0.0,
    }


def main():
    import json

    import cv2
    import numpy as np
    import torch
    from torchvision.transforms import v2

    sys.path.insert(0, VISOMASTER_DIR)
    from app.processors.models_processor import ModelsProcessor
    from app.processors.workers.frame_worker import FrameWorker

    if not os.path.isfile(SETTINGS_FIXTURE):
        raise FileNotFoundError("settings fixture missing: " + SETTINGS_FIXTURE)
    with open(SETTINGS_FIXTURE, "r", encoding="utf-8") as handle:
        settings = json.load(handle)
    control = dict(settings["global"])
    project = dict(settings["project"])

    control["ProvidersPrioritySelection"] = "CUDA"
    control["DetectorModelSelection"] = "RetinaFace"
    control["RecognitionModelSelection"] = "Inswapper128ArcFace"
    control["SimilarityTypeSelection"] = "Opal"
    control["EmbMergeMethodSelection"] = "Mean"
    control["LandmarkDetectToggle"] = False
    control["nThreadsSlider"] = 1
    project["ClipEnableToggle"] = False
    project["FaceEditorEnableToggle"] = False

    main_window = FakeMainWindow(control, project)
    processor = ModelsProcessor(main_window, device="cuda")
    processor.switch_providers_priority("CUDA")
    processor.set_number_of_threads(int(control.get("nThreadsSlider", 1)))
    main_window.models_processor = processor

    capture = cv2.VideoCapture(VIDEO)
    if not capture.isOpened():
        raise RuntimeError("could not open " + VIDEO)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def read_frame(number):
        capture.set(cv2.CAP_PROP_POS_FRAMES, number)
        ok, frame_bgr = capture.read()
        if not ok or frame_bgr is None:
            raise RuntimeError("could not read frame %d" % number)
        return frame_bgr

    def detect_in_image(image_bgr):
        rgb = np.ascontiguousarray(image_bgr[..., ::-1])
        img = torch.from_numpy(rgb.astype("uint8")).to(processor.device)
        img = img.permute(2, 0, 1)
        kpss_5, _kpss = None, None
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
            rotation_angles=[0],
        )
        cards = []
        recogniser = control["RecognitionModelSelection"]
        similarity_type = control["SimilarityTypeSelection"]
        threshold = float(project["SimilarityThresholdSlider"])
        for face_kps in kpss_5:
            embedding, crop = processor.run_recognize_direct(
                img, face_kps, similarity_type, recogniser
            )
            store = {recogniser: embedding}
            for option in RECOGNITION_MODELS:
                if option == recogniser:
                    continue
                other, _crop = processor.run_recognize_direct(
                    img, face_kps, similarity_type, option
                )
                store[option] = other
            cards.append({"store": store, "crop_bgr": np.ascontiguousarray(crop.cpu().numpy()[..., ::-1])})
        return cards

    # Target faces: detect once on frame 0, register like the UI does.
    targets = detect_in_image(read_frame(0))
    if not targets:
        raise RuntimeError("no faces detected in " + VIDEO)
    for index, card in enumerate(targets):
        main_window.target_faces["face-%d" % index] = card

    # THE VISOMASTER WAY: source embedded once, at assignment time.
    t0 = time.monotonic()
    sources = detect_in_image(cv2.imread(SOURCE))
    source_embed_s = time.monotonic() - t0
    if len(sources) != 1:
        raise RuntimeError("source must contain exactly one face, got %d" % len(sources))
    source_store = sources[0]["store"]
    for face_id, card in main_window.target_faces.items():
        card["assigned"] = {
            m: source_store[m] for m in RECOGNITION_MODELS if m in source_store
        }

    def build_worker(frame_number):
        frame_bgr = read_frame(frame_number)
        frame_rgb = np.ascontiguousarray(frame_bgr[..., ::-1])
        worker = FrameWorker(frame_rgb, main_window, frame_number, None, True)
        worker.parameters = {fid: dict(project) for fid in main_window.target_faces}
        worker.target_faces = main_window.target_faces
        return worker

    # How do the assigned embeddings reach frame_worker? Mirror Engine.swap's
    # shape: it sets FaceCard.assigned_input_embedding; upstream cards carry
    # assigned_input_embeddings dicts. Attach whatever attribute name exists.
    probe_card = next(iter(main_window.target_faces.values()))
    if isinstance(probe_card, dict):
        # Wrap plain dicts into attribute objects carrying every spelling the
        # vendored/upstream workers have been seen to read.
        class Card:
            def __init__(self, face_id, store, crop, recognition_model, assigned):
                self.face_id = face_id
                self.embedding_store = store
                self.crop = crop
                self.recognition_model = recognition_model
                self.assigned_input_embedding = assigned
                self.assigned_input_embeddings = assigned

            def get_embedding(self, embedding_swap_model):
                return self.embedding_store.get(embedding_swap_model, np.array([]))

        wrapped = {}
        for face_id, card in main_window.target_faces.items():
            c = Card(
                face_id,
                card["store"],
                card["crop_bgr"],
                control["RecognitionModelSelection"],
                card["assigned"],
            )
            wrapped[face_id] = c
        main_window.target_faces.clear()
        main_window.target_faces.update(wrapped)

    provider = getattr(processor, "provider_name", "?")

    # --- variant A: vm-live (source embedded once above) -------------------
    for i in range(WARMUP):
        build_worker(i % max(frame_count, 1)).process_frame()
    latencies_a = []
    for i in range(MEASURED):
        started = time.monotonic()
        build_worker(i % max(frame_count, 1)).process_frame()
        latencies_a.append(time.monotonic() - started)

    # --- variant B: re-embed the source every frame (Engine.swap today) ----
    latencies_b = []
    for i in range(3):
        started = time.monotonic()
        detect_in_image(cv2.imread(SOURCE))
        build_worker(i % max(frame_count, 1)).process_frame()
        latencies_b.append(time.monotonic() - started)
    for i in range(min(10, MEASURED)):
        started = time.monotonic()
        detect_in_image(cv2.imread(SOURCE))
        build_worker(i % max(frame_count, 1)).process_frame()
        latencies_b.append(time.monotonic() - started)

    a = stats(latencies_a)
    b = stats(latencies_b)
    result = {
        "mode": "visomaster",
        "provider": str(provider),
        "resolution": "1920x1080",
        "media_fps": round(fps, 3),
        "faces": len(main_window.target_faces),
        "source_embed_once_s": round(source_embed_s, 2),
        "vm_live": a,
        "reembed_per_frame": b,
    }
    bench(
        "VM_LIVE",
        "provider={} res=1920x1080 media_fps={} faces={} swaps={} mean_ms={} "
        "p50_ms={} min_ms={} max_ms={} fps={} (source embedded ONCE)".format(
            provider, result["media_fps"], result["faces"], a["n"], a["mean_ms"],
            a["p50_ms"], a["min_ms"], a["max_ms"], a["fps"],
        ),
    )
    bench(
        "REEMBED",
        "swaps={} mean_ms={} p50_ms={} fps={} (source re-detected every frame "
        "= Engine.swap today)".format(b["n"], b["mean_ms"], b["p50_ms"], b["fps"]),
    )
    print("BENCH_JSON:{}".format(result), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - diagnostic tool reports, never tracebacks
        bench("ENGINE_ERROR", "{}: {}".format(type(exc).__name__, exc))
        sys.exit(4)
