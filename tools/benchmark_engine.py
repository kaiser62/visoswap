"""The two-tier performance baseline Phase 05.1's plan must cite (CONTEXT D-11).

Run on the engine interpreter (the one with torch/onnxruntime), never imported by
pytest -- the same process boundary as ``tests/_engine_runner.py``, whose seal,
settings fixture, pinned smoke settings and asset resolution this file reuses so
the benchmark measures exactly what production measures::

    .venv-clean/Scripts/python.exe tools/benchmark_engine.py --micro
    .venv-clean/Scripts/python.exe tools/benchmark_engine.py --pipeline
        [--frames N]
    .venv-clean/Scripts/python.exe tools/benchmark_engine.py --gate --idle-gpu
        [--frames N]

Tier 1 (``--micro``) answers "how fast is one swap": bind the test clip, detect
once, warm up, then time N sequential ``Engine.swap`` calls at the media's native
resolution. The committed default clip is already 1920x1080@24, which is why one
subject covers both "source resolution" and "1080p".

Tier 2 (``--pipeline``) answers "can live overlay keep up": the production shape
of a stream-mode frame is swap -> JPEG-encode, so this loops the first N frames
doing exactly that and reports sustained generated frames/min against the clip's
own fps. It excludes the scheduler's queue/db/ws layers on purpose -- if the
engine+encode floor sits below playback fps no amount of queueing saves the
overlay; if it sits above, the headroom tells us how much those layers may cost.
Default window is 600 frames; point ``BENCH_PIPELINE_VIDEO`` at a longer personal
clip for a steadier measurement (the committed clip is ~24 frames).

Gate (``--gate --idle-gpu``) certifies the Phase 05.1 source-embedding cache:
the real cache is measured twice, then the ``embed_once`` monkey-patch runs as
the control, and the verdict passes only when both real means sit within
``GATE_TOLERANCE`` of the control and agree with each other within
``GATE_SPREAD_LIMIT``. Without ``--idle-gpu`` it prints reproduce instructions
and refuses to print a verdict: docs/benchmark-baseline.md records a contended
card reading 201.55 ms/swap where an idle one read 147.65 ms.

No image is written and no pixel data is logged: report lines carry timings,
shapes and counts only (T-02-10). Output mixes human lines (``BENCH ...``) with
one machine-readable ``BENCH_JSON:{...}`` per mode so
``docs/benchmark-baseline.md`` can be assembled without parsing prose.
"""

from __future__ import annotations

import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TOOLS_DIR)
_TESTS_DIR = os.path.join(_REPO_ROOT, "tests")

for _path in (_REPO_ROOT, _TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Single source of truth for the seal, the pinned settings and asset resolution.
from _engine_runner import (  # noqa: E402 - follows the sys.path setup above
    DEFAULT_TEST_SOURCE,
    DEFAULT_TEST_VIDEO,
    SMOKE_GLOBAL_OVERRIDES,
    SMOKE_PROJECT_OVERRIDES,
    SealBroken,
    apply_overrides,
    arm_seal,
    leaked_roots,
    load_settings,
    resolve_media,
    resolve_models_dir,
    resolve_settings_fixture,
)

EXIT_CLEAN = 0
EXIT_SEAL_BREACHED = 1
EXIT_DEPS_MISSING = 2
EXIT_ASSET_MISSING = 3
EXIT_ENGINE_ERROR = 4

PIPELINE_VIDEO_ENV_VAR = "BENCH_PIPELINE_VIDEO"
MICRO_WARMUP = 3
MICRO_MEASURED = 20
#: Steady-state window for tier 2: long enough to average out per-frame jitter,
#: short enough that the whole benchmark stays a minutes-scale errand.
DEFAULT_PIPELINE_FRAMES = 600
#: Gate tolerance (Phase 05.1 plan 01): the real cache and the ``embed_once``
#: monkey-patch now do the same work, so their mean latencies must agree. A
#: material gap means the memo missed on some frames and per-frame re-embed
#: cost leaked back in.
GATE_TOLERANCE = 0.15
#: Two real-cache runs taken in the same process must agree with each other
#: before either can certify anything: a card that disagrees with itself by
#: more than this is contended, and docs/benchmark-baseline.md records how
#: badly a contended card distorts these numbers (201.55 ms vs 147.65 ms).
GATE_SPREAD_LIMIT = 0.25


def bench(label, detail):
    print("BENCH {} {}".format(label, " ".join(str(detail).split())), flush=True)


def stats(values):
    """mean/p50/min/max over a list of second-duration floats."""
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    return {
        "n": n,
        "mean_ms": round(mean * 1000.0, 2),
        "p50_ms": round(ordered[n // 2] * 1000.0, 2),
        "min_ms": round(ordered[0] * 1000.0, 2),
        "max_ms": round(ordered[-1] * 1000.0, 2),
        "fps_from_mean": round(1.0 / mean, 2) if mean > 0 else 0.0,
    }


def load_settings_pair():
    fixture_path = resolve_settings_fixture()
    settings = load_settings(fixture_path)
    if settings is None:
        raise FileNotFoundError(
            "settings fixture not found at {} -- regenerate with "
            "tools/dump_engine_settings.py".format(fixture_path)
        )
    return settings


def check_assets(video_path, source_path):
    for label, path in (("target video", video_path), ("source face", source_path)):
        if not os.path.isfile(path):
            raise FileNotFoundError(
                "{} not found at {} -- see docs/engine-test-assets.md".format(
                    label, path
                )
            )
    models_dir = str(resolve_models_dir())
    if not os.path.isdir(models_dir):
        raise FileNotFoundError(
            "model_assets not reachable at {} -- run "
            "tools/link_model_assets.py".format(models_dir)
        )


def build_engine(settings):
    """The engine, pinned to exactly the smoke run's settings."""
    control = apply_overrides(
        dict(settings.get("global", {})), SMOKE_GLOBAL_OVERRIDES, "global"
    )
    parameters = apply_overrides(
        dict(settings.get("project", {})), SMOKE_PROJECT_OVERRIDES, "project"
    )
    from visoswap.engine import Engine

    return Engine(device="cuda", global_settings=control, project_settings=parameters)


def resolve_media_pair(pipeline):
    """Tier subjects. Only the pipeline mode honours BENCH_PIPELINE_VIDEO: micro
    must always measure the committed 1080p clip unless VISOSWAP_TEST_VIDEO says
    otherwise, so the two tiers cannot silently swap subjects again."""
    if pipeline:
        video_path = os.environ.get(PIPELINE_VIDEO_ENV_VAR) or resolve_media(
            "VISOSWAP_TEST_VIDEO", DEFAULT_TEST_VIDEO
        )
    else:
        video_path = resolve_media("VISOSWAP_TEST_VIDEO", DEFAULT_TEST_VIDEO)
    source_path = resolve_media("VISOSWAP_TEST_SOURCE", DEFAULT_TEST_SOURCE)
    check_assets(video_path, source_path)
    return video_path, source_path


def mode_micro(threads=None, embed_once=False):
    """Tier 1: sequential ``Engine.swap`` latency distribution, native resolution.

    ``threads`` overrides the global tier's ``nThreadsSlider`` for the run.
    ``embed_once`` monkey-patches ``_source_embedding_store`` to compute the
    store a single time -- the exact projection of the planned source-embedding
    cache, measured through the full ``swap`` path.
    """
    import time

    settings = load_settings_pair()
    if threads is not None:
        settings["global"]["nThreadsSlider"] = int(threads)
    video_path, source_path = resolve_media_pair(pipeline=False)

    arm_seal()
    import cv2

    engine = build_engine(settings)
    try:
        load_started = time.monotonic()
        media = engine.load(video_path)
        load_s = time.monotonic() - load_started

        detect_started = time.monotonic()
        cards = engine.detect_faces(0)
        detect_s = time.monotonic() - detect_started
        if not cards:
            raise RuntimeError(
                "no faces detected in {}".format(os.path.basename(video_path))
            )

        # The decoder that fed detection also sizes the report; opening a second
        # capture risks disagreeing about frame indices.
        probe = engine._read_frame(0)  # noqa: SLF001 - sealed-runner convention
        height, width = probe.shape[:2]

        if embed_once:
            cached = engine._source_embedding_store(source_path)  # noqa: SLF001
            engine._source_embedding_store = lambda path: cached  # noqa: SLF001

        for _ in range(MICRO_WARMUP):
            engine.swap(0, source_path)

        latencies = []
        for i in range(MICRO_MEASURED):
            started = time.monotonic()
            engine.swap(i % int(media["frame_count"]), source_path)
            latencies.append(time.monotonic() - started)

        provider = engine.context.models_processor.provider_name
    finally:
        engine._release()  # noqa: SLF001 - the decoder holds an OS handle

    timing = stats(latencies)
    label = "MICRO{}{}".format(
        "-t{}".format(threads) if threads is not None else "",
        "-embedonce" if embed_once else "",
    )
    result = {
        "mode": label.lower(),
        "provider": provider,
        "resolution": "{}x{}".format(width, height),
        "media_fps": round(float(media["fps"]), 3),
        "load_s": round(load_s, 2),
        "detect_s": round(detect_s, 2),
        "faces": len(cards),
        "warmup": MICRO_WARMUP,
        "threads": threads,
        "embed_once": bool(embed_once),
        **timing,
    }
    bench(
        label,
        "provider={} res={} media_fps={} threads={} embed_once={} "
        "load_s={:.2f} detect_s={:.2f} faces={} swaps={} mean_ms={} p50_ms={} "
        "min_ms={} max_ms={} swap_fps={}".format(
            provider, result["resolution"], result["media_fps"], threads,
            bool(embed_once), load_s, detect_s, len(cards), timing["n"],
            timing["mean_ms"], timing["p50_ms"], timing["min_ms"],
            timing["max_ms"], timing["fps_from_mean"],
        ),
    )
    print("BENCH_JSON:{}".format(result), flush=True)
    return EXIT_CLEAN


def mode_pipeline(frames):
    """Tier 2: sustained swap->encode rate vs the clip's own fps."""
    import time

    settings = load_settings_pair()
    video_path, source_path = resolve_media_pair(pipeline=True)

    arm_seal()
    import cv2

    engine = build_engine(settings)
    try:
        media = engine.load(video_path)
        cards = engine.detect_faces(0)
        if not cards:
            raise RuntimeError(
                "no faces detected in {}".format(os.path.basename(video_path))
            )

        frame_count = int(media["frame_count"])
        media_fps = float(media["fps"])
        count = min(frames, frame_count)

        # Warm one swap so first-frame model/JIT cost stays out of the window.
        swapped = engine.swap(0, source_path)

        encoded_bytes = 0
        latencies = []
        pipeline_started = time.monotonic()
        for i in range(count):
            started = time.monotonic()
            swapped = engine.swap(i % frame_count, source_path)
            ok, buffer = cv2.imencode(".jpg", swapped)
            latencies.append(time.monotonic() - started)
            if not ok:
                raise RuntimeError("JPEG encode failed on frame {}".format(i))
            encoded_bytes += len(buffer)
        pipeline_s = time.monotonic() - pipeline_started

        provider = engine.context.models_processor.provider_name
    finally:
        engine._release()  # noqa: SLF001 - the decoder holds an OS handle

    per_frame = stats(latencies)
    gen_fps = count / pipeline_s if pipeline_s > 0 else 0.0
    result = {
        "mode": "pipeline",
        "provider": provider,
        "video": os.path.basename(video_path),
        "resolution": "{}x{}".format(swapped.shape[1], swapped.shape[0]),
        "media_fps": round(media_fps, 3),
        "frames": count,
        "window_s": round(pipeline_s, 2),
        "gen_fps": round(gen_fps, 2),
        "generated_frames_per_min": round(gen_fps * 60.0, 1),
        "headroom_vs_playback": round(gen_fps / media_fps, 3) if media_fps else 0.0,
        "per_frame_mean_ms": per_frame["mean_ms"],
        "per_frame_p50_ms": per_frame["p50_ms"],
        "encoded_mb": round(encoded_bytes / (1000.0 * 1000.0), 1),
    }
    bench(
        "PIPELINE",
        "video={} res={} media_fps={} frames={} window_s={:.2f} gen_fps={:.2f} "
        "gen_per_min={:.1f} headroom_vs_playback={} mean_ms={} p50_ms={} "
        "encoded_mb={}".format(
            result["video"], result["resolution"], result["media_fps"], count,
            pipeline_s, gen_fps, result["generated_frames_per_min"],
            result["headroom_vs_playback"], per_frame["mean_ms"],
            per_frame["p50_ms"], result["encoded_mb"],
        ),
    )
    print("BENCH_JSON:{}".format(result), flush=True)
    return EXIT_CLEAN


def _micro_window(engine, source_path, window, total_frames):
    """One ``mode_micro``-shaped measurement window on an already-warm engine.

    Sequential ``Engine.swap`` calls over rotating frame indices, returning the
    per-swap latency list in seconds. The gate takes three of these windows
    (real cache twice, then the ``embed_once`` control) on one engine so all
    three see the same load, the same decoder and the same models.
    """
    import time

    total = max(1, int(total_frames or 1))
    latencies = []
    for i in range(window):
        started = time.monotonic()
        engine.swap(i % total, source_path)
        latencies.append(time.monotonic() - started)
    return latencies


def mode_gate(frames=None, idle_gpu=False):
    """Certify the real source-embedding cache against the embed-once control.

    Answers one question (CONTEXT D-15a): did the real cache land within noise
    of the measured ``--embed-once`` projection? Three windows run in one
    process on one engine -- the real cache measured **twice** (their spread
    certifies the card), then the ``embed_once`` monkey-patch as the control --
    and the verdict is machine-readable from the exit code: ``EXIT_CLEAN`` on
    pass, ``EXIT_ENGINE_ERROR`` on fail.

    Idle-GPU discipline is enforced, not assumed (D-11): without an explicit
    ``--idle-gpu`` the gate refuses to print a verdict at all.
    """
    if not idle_gpu:
        bench(
            "GATE REFUSED",
            "no --idle-gpu acknowledgement. Performance numbers from a "
            "contended card are noise: the same commands measured 201.55 "
            "ms/swap contended and 147.65 ms idle "
            "(docs/benchmark-baseline.md). Reproduce on an idle GPU with: "
            ".venv-clean/Scripts/python.exe tools/benchmark_engine.py "
            "--gate --idle-gpu [--frames N]",
        )
        return EXIT_ENGINE_ERROR

    window = max(1, int(frames or MICRO_MEASURED))
    settings = load_settings_pair()
    video_path, source_path = resolve_media_pair(pipeline=False)

    arm_seal()

    engine = build_engine(settings)
    try:
        media = engine.load(video_path)
        cards = engine.detect_faces(0)
        if not cards:
            raise RuntimeError(
                "no faces detected in {}".format(os.path.basename(video_path))
            )
        probe = engine._read_frame(0)  # noqa: SLF001 - sealed-runner convention
        height, width = probe.shape[:2]

        for _ in range(MICRO_WARMUP):
            engine.swap(0, source_path)

        real_first = stats(
            _micro_window(engine, source_path, window, media["frame_count"])
        )
        real_second = stats(
            _micro_window(engine, source_path, window, media["frame_count"])
        )

        # The projection's monkey-patch, exactly as mode_micro applies it: the
        # store computed once, every later call served the same object.
        cached_store = engine._source_embedding_store(source_path)  # noqa: SLF001
        engine._source_embedding_store = lambda path: cached_store  # noqa: SLF001
        for _ in range(MICRO_WARMUP):
            engine.swap(0, source_path)
        control = stats(
            _micro_window(engine, source_path, window, media["frame_count"])
        )

        provider = engine.context.models_processor.provider_name
    finally:
        engine._release()  # noqa: SLF001 - the decoder holds an OS handle

    fastest_real = min(real_first["mean_ms"], real_second["mean_ms"])
    spread = (
        abs(real_first["mean_ms"] - real_second["mean_ms"]) / fastest_real
        if fastest_real > 0
        else float("inf")
    )
    ratio_first = real_first["mean_ms"] / control["mean_ms"]
    ratio_second = real_second["mean_ms"] / control["mean_ms"]

    reasons = []
    if spread > GATE_SPREAD_LIMIT:
        reasons.append("contended")
    for ratio in (ratio_first, ratio_second):
        if not (1.0 - GATE_TOLERANCE) <= ratio <= (1.0 + GATE_TOLERANCE):
            reasons.append("cache_gap")
            break
    verdict = "pass" if not reasons else "fail"

    result = {
        "mode": "gate",
        "provider": provider,
        "resolution": "{}x{}".format(width, height),
        "media_fps": round(float(media["fps"]), 3),
        "frames_per_window": window,
        "faces": len(cards),
        "real_mean_ms": [real_first["mean_ms"], real_second["mean_ms"]],
        "real_p50_ms": [real_first["p50_ms"], real_second["p50_ms"]],
        "real_min_ms": [real_first["min_ms"], real_second["min_ms"]],
        "real_max_ms": [real_first["max_ms"], real_second["max_ms"]],
        "control_mean_ms": control["mean_ms"],
        "control_p50_ms": control["p50_ms"],
        "control_fps": control["fps_from_mean"],
        "ratio": [round(ratio_first, 4), round(ratio_second, 4)],
        "spread": round(spread, 4),
        "tolerance": GATE_TOLERANCE,
        "spread_limit": GATE_SPREAD_LIMIT,
        "verdict": verdict,
        "reasons": reasons,
        "idle_gpu": True,
    }
    bench(
        "GATE",
        "provider={} res={} frames={} real_mean_ms={}/{} real_p50_ms={}/{} "
        "control_mean_ms={} control_p50_ms={} ratio={}/{} spread={} "
        "verdict={} reason={}".format(
            provider,
            result["resolution"],
            window,
            real_first["mean_ms"],
            real_second["mean_ms"],
            real_first["p50_ms"],
            real_second["p50_ms"],
            control["mean_ms"],
            control["p50_ms"],
            result["ratio"][0],
            result["ratio"][1],
            result["spread"],
            verdict,
            "+".join(reasons) if reasons else "none",
        ),
    )
    print("BENCH_JSON:{}".format(result), flush=True)
    return EXIT_CLEAN if verdict == "pass" else EXIT_ENGINE_ERROR


USAGE = (
    "usage: benchmark_engine.py (--micro | --pipeline [--frames N] "
    "| --gate --idle-gpu [--frames N])"
    "  # run on the engine interpreter"
)


def main(argv):
    try:
        preloaded = leaked_roots()
        if preloaded:
            bench("SEAL_BREACHED", "sealed roots preloaded: " + ", ".join(preloaded))
            return EXIT_SEAL_BREACHED
        if argv == ["--micro"]:
            return mode_micro()
        if len(argv) >= 2 and argv[0] == "--micro":
            threads = None
            embed_once = False
            rest = argv[1:]
            while rest:
                if rest[0] == "--threads" and len(rest) >= 2:
                    threads = int(rest[1])
                    rest = rest[2:]
                elif rest[0] == "--embed-once":
                    embed_once = True
                    rest = rest[1:]
                else:
                    bench("ENGINE_ERROR", USAGE)
                    return EXIT_ENGINE_ERROR
            return mode_micro(threads=threads, embed_once=embed_once)
        if argv == ["--pipeline"]:
            return mode_pipeline(DEFAULT_PIPELINE_FRAMES)
        if len(argv) == 3 and argv[0] == "--pipeline" and argv[1] == "--frames":
            return mode_pipeline(max(1, int(argv[2])))
        if argv and argv[0] == "--gate":
            frames = None
            idle_gpu = False
            rest = argv[1:]
            while rest:
                if rest[0] == "--frames" and len(rest) >= 2:
                    frames = max(1, int(rest[1]))
                    rest = rest[2:]
                elif rest[0] == "--idle-gpu":
                    idle_gpu = True
                    rest = rest[1:]
                else:
                    bench("ENGINE_ERROR", USAGE)
                    return EXIT_ENGINE_ERROR
            return mode_gate(frames=frames, idle_gpu=idle_gpu)
    except SealBroken as exc:
        bench("SEAL_BREACHED", exc)
        return EXIT_SEAL_BREACHED
    except ImportError as exc:
        bench("DEPS_MISSING", exc)
        return EXIT_DEPS_MISSING
    except FileNotFoundError as exc:
        bench("ASSET_MISSING", exc)
        return EXIT_ASSET_MISSING
    except Exception as exc:  # noqa: BLE001 - a tool reports, never tracebacks
        bench("ENGINE_ERROR", "{}: {}".format(type(exc).__name__, exc))
        return EXIT_ENGINE_ERROR
    bench("ENGINE_ERROR", USAGE)
    return EXIT_ENGINE_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
