# Benchmark baseline — Phase 05.1 pre-implementation gate (CONTEXT D-11)

**Date:** 2026-08-23 · **Provider:** CUDA (TensorRT refused by design, T-02-09) · **Threads:** 1
**Settings:** exactly the sealed smoke run's pinned tiers (`tests/_engine_runner.py` overrides over `tests/fixtures/engine_settings.json`) — Inswapper128 @128, RetinaFace, LandmarkDetect off, FaceEditor off.
**Tool:** `tools/benchmark_engine.py` (sealed subprocess on `.venv-clean`, same boundary as every engine test).
Reproduce (on an **idle GPU** — see the variance note):

```
.venv-clean/Scripts/python.exe tools/benchmark_engine.py --micro
BENCH_PIPELINE_VIDEO=<long personal clip> .venv-clean/Scripts/python.exe tools/benchmark_engine.py --pipeline --frames 600
```

## Results — clean-GPU runs (authoritative)

| Tier | Subject | Resolution | Media fps | Result | Rate |
|------|---------|-----------|-----------|--------|------|
| 1 micro | repo test clip (`tests/media/…rosh_generate….mp4`, 2 faces) | 1920×1080 | 23.976 | mean **147.65 ms**/swap (p50 157, **min 93**, max 172, n=20 after 3 warmup) | **6.77 swap fps** |
| 2 pipeline | personal long clip, 600 sequential frames, swap→JPEG-encode | 1282×720 | 25.0 | window 64.11 s → **9.36 gen fps**, 561.5 frames/min, encode 49.1 MB total | headroom **0.374×** vs playback |
| — load / detect | both clips | — | — | model load ≤ 0.06 s¹; `detect_faces` 3.22–7.61 s once per bind | one-time |

## Variance note — measure only on an idle GPU

The first full run of this benchmark produced 201.55 ms/swap and 2.41 gen fps on the
identical commands; a rerun with nothing else on the machine reproduced immediately at
the numbers above (~1.4–3.9× faster). Something else held GPU time during run 1.
Two consequences recorded for the phase:

- Every performance gate in this milestone runs against an idle GPU, stated in the plan.
- The spread itself (min single-swap 93 ms inside the same window) shows how much a
  contended card distorts results; single-run numbers without an idle check are noise.

¹ Warm OS page cache; treat as best case.

## Root cause: `swap()` re-embeds the source image every frame

`Engine.swap` (`visoswap/engine.py`) begins each call with
`source_store = self._source_embedding_store(source_path)`, which re-runs the **full
four-call detection sequence on the source image**: `run_detect` plus
`run_recognize_direct` under *all four* recognition models (`RECOGNITION_MODELS`). That
is ~5 extra model inferences attached to every single frame, independent of resolution,
to compute an embedding store that never changes between frames for a given file.

The reference implementation reached 21.2 fps CUDA / 27.8 fps TensorRT at 1080p
(PROJECT.md) because VisoMaster's UI embedded the source **once** when a face button
was assigned; the per-frame loop ran only the swapper. The vendored frame worker itself
is not the regression — the seam shape around it is.

## Cross-check: VisoMaster itself, headless (`tools/benchmark_visomaster.py`)

Same clip, same fixture settings, same machine, driven through **upstream's own**
`ModelsProcessor` + `FrameWorker` on VisoMaster's interpreter (`dependencies/Python`,
PySide6 present), cwd on a scratch junction so the read-only install is untouched:

| Configuration | mean ms/frame | fps |
|---------------|--------------|-----|
| VisoMaster pipeline, **source embedded once** (how its UI assigns a face) | **121.1** (p50 125, min 94) | **8.26** |
| VisoMaster pipeline, source re-detected every frame (= `Engine.swap`'s shape) | 187.5 (p50 172) | 5.33 |
| VisoSwap `Engine.swap` today (re-embeds per frame) | 147.7 | 6.77 |
| 1080p@23.976 playback requires | ≤ 41.7 | 23.98 |

Readings:

1. **The re-embed tax is confirmed on both stacks**: embedding once instead of per
   frame is worth ~66 ms/frame on upstream (~35%). The planned `Engine` cache fix
   recovers roughly that much — worth taking, nowhere near sufficient alone.
2. **PROJECT.md's 21.2/27.8 fps figures do not reproduce today, even in pristine
   VisoMaster form** (8.26 fps embed-once). Those numbers predate this benchmark;
   until re-measured under controlled conditions they should be treated as unverified
   legend, not a target.
3. Our `Engine` sits *between* upstream's two variants while carrying the worse seam
   shape — consistent with the same vendor floor plus the re-embed tax, partially
   masked by run-to-run variance.

## Verdict for D-06 (live overlay, priority 1)

**No configuration on this machine today reaches 1080p playback rate — including
authentic VisoMaster.** At 8.3–9.4 gen fps against 23.976 playback, a Stream-Live run
generates roughly one swapped frame per three played frames regardless of seam shape.
Therefore, for Phase 05.1:

- The **nearest-previous swapped frame behind the playhead** behavior is not a degraded
  fallback — it *is* the design, and the core value (playback never pauses/stutters)
  holds trivially because generation can never be waited on.
- The **cache fix stays sequenced first** (cheap, honest win, re-benchmarked as gate).
- The **Jobs tab must surface the live generation deficit** (generated-vs-played ratio)
  so the user sees reality instead of a lie of omission.
- Real-time parity levers, if ever wanted, are separate work with separate gates:
  `processing_scale`/`processing_width` downsizing (fields already exist), interval
  modes, or revisiting TensorRT behind an absolute cache path. None are in 05.1 scope.

## Decision recorded

Plan 05.1 must sequence: **(1)** idle-GPU discipline + source-embedding cache +
re-benchmark gate, **(2)** everything else. No overlay implementation may land against
today's numbers.
