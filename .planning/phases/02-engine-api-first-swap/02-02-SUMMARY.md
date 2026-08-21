---
phase: 02-engine-api-first-swap
plan: 02
subsystem: engine
tags: [engine-api, face-card, first-swap, provider-lock, seal]
status: complete

requires:
  - 01-04 (the Qt-free vendored tree and the self.frame seam)
  - 02-01 (model_assets link, sealed runner, typed settings fixture, exit vocabulary)
provides:
  - visoswap/engine.py (Engine.load / detect_faces / swap, and FaceCard)
  - tests/_engine_runner.py --smoke (the end-to-end swap inside the seal)
  - tests/test_engine_smoke.py (nine assertions over one shared run)
  - tests/test_engine_surface.py (the three-method API pinned by ast)
  - CUDA provider lock; TensorRT refused outright
affects:
  - 02-03 exercises LivePortrait against this Engine and reuses the smoke frame
  - Phase 3's three tiers resolve into the settings dict Engine.swap consumes
  - Phase 4's backend replaces services/inprocess.py with this API

tech-stack:
  added: []
  patterns:
    - "refuse the unsafe execution provider with a raise rather than deprioritising it, so a relative cache path cannot be reached by configuration"
    - "derive the face identifier by digesting the recognition embedding, so no identifier originates in a widget and the same face keys the same way across runs"
    - "pin a public surface with ast rather than import, because the pytest interpreter has neither torch nor cv2 and a skipped surface check is a false green"

key-files:
  created:
    - visoswap/engine.py
    - tests/test_engine_smoke.py
    - tests/test_engine_surface.py
  modified:
    - tests/_engine_runner.py
    - tests/test_vendor_headers.py
    - docs/engine-test-assets.md
    - .gitignore
---

# Phase 02 Plan 02: `Engine`, `FaceCard`, and the First Swap Summary

The project has produced its first pixel. Two faces detected in a 1080p clip, both swapped with
a source face belonging to a different person, **110,932 pixels changed, 6.3s including both
model loads**, on an interpreter with no Qt installed.

## What Was Built

**Task 1 — the API** (commit `40dfb90`). `visoswap/engine.py` publishes exactly three methods:
`load`, `detect_faces`, `swap`. Project-authored, no vendored body. The constructor switches the
execution provider off TensorRT, and `TensorRT`/`TensorRT-Engine` are **refused outright with a
`ValueError`** rather than merely deprioritised — their provider options carry a relative
`tensorrt-engines` cache path that lands wherever the process happens to be. That is threat
T-02-09, and 02-01 found the hazard already realised upstream: a nested
`D:/Visomaster/tensorrt-engines/tensorrt-engines/`.

`FaceCard` carries four members — `embedding_store`, `crop`, `recognition_model`,
`assigned_input_embedding` — and derives `face_id` by digesting its own recognition embedding.
No identifier comes from a widget, and the same face keys the same way across runs. That is
roadmap criterion 5.

**Task 2 — the swap** (commit `e1fd91b`). `--smoke` resolves both media fixtures, the settings
fixture and the `model_assets` link before anything expensive runs, and exits `ASSET_MISSING (3)`
on any of them. It never skips: a skipped swap test is a phase that looks finished and has
swapped nothing. All four exit-3 paths were provoked by hand.

## Corrections the Executor Made to the Plan

Three, all measured:

- **The plan's proposed surface grep could not have passed.** It matched every method at
  four-space indent in the file, including the two `FaceCard` methods the same plan requires.
  A class-scoped check is what was meant. (This is the check I had already hardened during the
  plan revision — the grep was still too blunt.)
- **The plan's override list named two keys that do not exist.** `ThreadsSlider` is
  `nThreadsSlider`; `TextMaskingEnableToggle` is `ClipEnableToggle`. The static
  override-names-a-real-key check was run against the wrong names first, to confirm it says so.
- The "not a copy of the source" guard was split into its own assertion with its own message,
  because a no-op swap producing a perfect copy is the specific failure this phase exists to
  catch.

## Verification Results (verbatim)

```
$ .venv-clean/Scripts/python -B tests/_engine_runner.py --smoke
CLEAN:smoke:faces=2 frames=24 fps=23.976 provider=CUDA input_shape=1080x1920x3
output_shape=1080x1920x3 diff_pixels=110932 elapsed=6.3s
artifacts=smoke_source_frame.png+smoke_swapped_frame.png
reachable_before_seal=PyQt5=no,PyQt6=no,PySide2=no,PySide6=no,app=yes,backend=no,
qtpy=no,shiboken2=no,shiboken6=no
exit=0

$ python -m pytest tests/ -q
61 passed

$ grep -rn "from backend\|import backend" visoswap/
(no output)

$ grep -nE "^    def [a-zA-Z]" visoswap/engine.py
118:    def face_id      148:    def get_embedding      159:    def assign      (FaceCard)
293:    def load         325:    def detect_faces       362:    def swap        (Engine)
```

The seal is not passing vacuously: `app=yes` records that the VisoMaster package genuinely
resolved before the block armed. Run on `D:/Visomaster`'s interpreter it reports
`PySide6=yes app=yes qtpy=yes shiboken6=yes` reachable before the seal — the swap completed with
four packages that genuinely resolve made unimportable. That is roadmap criterion 3 as a runtime
fact rather than a grep.

## Roadmap Criteria

Criteria **1, 2, 3 and 5 are met**. Criterion 4 (LivePortrait exercised; DFM and CLIPseg
import-proven only, by decision) belongs to plan 02-03.

`ENGINE-01` stays **Pending**. It means "swap a frame with no PySide6 installed and no
VisoMaster install present" — the second clause is not yet true, since `model_assets` is a
junction into `D:/Visomaster`. It closes in 02-04, not here.

## Note on Execution

This plan was executed by an agent that was interrupted after both task commits landed but
before it ran the closing verification or wrote this summary. Both were completed directly and
independently: the numbers above are from fresh runs, not recovered from the interrupted
agent's output.
