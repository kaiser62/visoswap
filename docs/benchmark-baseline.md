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

## Verdict for D-06 (live overlay, priority 1)

**Even on an idle GPU the overlay cannot keep up: 0.374× of playback fps (micro 6.77 vs
23.976).** The remediation is contained and project-authored (`engine.py` carries no
vendor header):

- Cache `_source_embedding_store` inside `Engine`, keyed by resolved path + mtime +
  size, invalidated on `load()`; optionally pre-warm at bind/detect time.
- After the fix, re-run this exact benchmark as a gate. If the swap-only floor clears
  media fps with margin for scheduler/db/ws layers, Stream-Live ships as true real-time
  overlay; otherwise it ships as nearest-previous swapped frame behind the playhead —
  the core-value behavior, which never blocks or stutters — with the generation gap
  surfaced honestly in the Jobs tab.

Tier 2's scope note stands: these numbers exclude the scheduler queue, DB writes and ws
fan-out by design.

## Decision recorded

Plan 05.1 must sequence: **(1)** idle-GPU discipline + source-embedding cache +
re-benchmark gate, **(2)** everything else. No overlay implementation may land against
today's numbers.
