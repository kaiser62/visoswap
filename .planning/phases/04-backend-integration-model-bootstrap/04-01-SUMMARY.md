---
phase: 04-backend-integration-model-bootstrap
plan: 01
subsystem: backend
tags: [backend-port, engine-adapter, comfyui-removal, source-face, tracer]
status: complete

requires:
  - 02-02 (visoswap/engine.py -- Engine.load/detect_faces/swap, the CUDA provider lock, FaceCard)
  - 02-04 (ENGINE-01 clause-1 evidence: .venv-clean carries torch and no PySide6)
  - 03-01 (visoswap/schema/schema.json -- 201 typed entries)
  - 03-02 (visoswap/settings/store.py -- resolve_control / resolve_parameters, whole tiers present)
provides:
  - backend/ -- 24 Python files, ComfyUI absent (services/comfyui.py, inprocess.py, visomaster.py dropped)
  - backend/services/engine_backend.py -- EngineFrameGenerator behind the eight-member FrameGenerator seam
  - tests/_backend_runner.py -- the sealed backend tracer (real generation through the route)
  - tests/test_no_comfyui.py -- roadmap criterion 4 gate, proven in three directions
  - tests/test_backend_tracer.py -- static surface test + subprocess tracer assertions
  - source_face_path column (project row, server-assigned, stripped from the public API)
  - docs/backend-port.md
affects:
  - 04-02 owns the recorder cancel-hang fix (roadmap criterion 1)
  - 04-03 owns the model bootstrap / startup refusal on incomplete models
  - 04-04 severs the VisoMaster install dependency further
  - 05 (frontend) builds on the project CRUD + settings endpoints

tech-stack:
  added: []
  patterns:
    - "one interpreter carries both stacks (web + inference), so the backend tests run on .venv-clean, not plain python"
    - "the engine adapter resolves its settings from the Phase 3 store (resolve_control / resolve_parameters), never a hand-written dict"
    - "the source face is an absolute project-authored path on the project row, not a name resolved inside VisoMaster's folder"
    - "the ComfyUI gate is two scans (text + route), each reporting its examined count and proven not inert"

key-files:
  created:
    - backend/services/engine_backend.py
    - tests/_backend_runner.py
    - tests/test_no_comfyui.py
    - docs/backend-port.md
  modified:
    - backend/models/database.py
    - backend/api/schemas.py
    - backend/api/projects.py
    - backend/services/generator.py
    - tests/test_backend_tracer.py
    - tests/conftest.py

decisions:
  - "The combined runtime is .venv-clean (Py3.10). It already carried the pinned CUDA inference stack, so the web stack was installed there rather than duplicating a multi-GB torch in a second venv. Pillow pinned to the engine's 9.5.0; the web pin was relaxed (Image.open/convert/resize/split/merge/tobytes/save are stable across the range)."
  - "ComfyUI is removed structurally, not switched off: the backend column's three-value vocabulary collapses to 'engine', comfyui_url/comfyui_prompt_id columns are dropped, the schema Literal loses comfyui, config loses comfyui fields, and both ComfyUI routes in api/backends.py are removed."
  - "The provider default is CUDA. TensorRT is refused at settings-validation time (config.py validator rejects anything not in {CUDA, CPU}), matching the engine's own ValueError, because its relative tensorrt-engines cache path writes outside the project."
  - "The source face is a new server-assigned source_face_path column. The predecessor resolved faces by name from VisoMaster's folder; this repo drops that, so the engine adapter reads an absolute path on the project row instead."
  - "The engine adapter resolves its settings from the Phase 3 store at engine-build time: resolve_control (all 33 global keys) and resolve_parameters (all 168 project keys), so the engine's unconditional control/parameter reads never hit a sparse dict (this was a real bug surfaced by the tracer: bare Engine(device='cuda') raised KeyError on ManualRotationEnableToggle)."

metrics:
  duration: ~2h (assessed + Task 3 across two sessions; Tasks 1-2 pre-committed)
  completed: 2026-08-23
  tasks: 3
  commits: 5

actuals:
  tokens: 40000
  tasks: 3
  commits: 5
---

# Phase 04 Plan 01: Backend Port & ComfyUI Removal Summary

**The tracer's measured output line:**

```
CLEAN:tracer:frame=000000.000.jpg size=336835 provider=CUDA elapsed=10.7s reachable_before_seal=PyQt5=no,PyQt6=no,PySide2=no,PySide6=no,app=no,qtpy=no,shiboken2=no,shiboken6=no
```

One real generation request through `backend/api/generation.py` → the engine
adapter → a 336 KB frame file on disk, provider `CUDA`, PySide6 unreachable in
the process. This is roadmap criterion 5's first proof.

**The interpreter decision:** `.venv-clean/Scripts/python.exe` (Python 3.10.19)
is the combined runtime. It already carried the pinned CUDA inference stack
(torch, onnxruntime, cv2, kornia), so the web stack (fastapi, aiosqlite, httpx,
uvicorn, pydantic, python-multipart, yt-dlp, PIL, pytest-asyncio, asgi-lifespan)
was installed there. `pip show PySide6` reports it absent. The full suite runs
on this interpreter (293 passed).

## What Was Built

**Tasks 1 & 2 (pre-committed `152e05f`, `306e3c2`):** the backend tree was ported
(24 files), `comfyui.py`/`inprocess.py`/`visomaster.py` dropped, ComfyUI removed
at all structural sites, provider default flipped to `CUDA`, and
`EngineFrameGenerator` written behind the unchanged eight-member `FrameGenerator`
seam.

**Task 3 (this session):**
- `tests/_backend_runner.py` — the sealed backend tracer, modelled on
  `_engine_runner.py` (same exit vocabulary), driving one real generation through
  the route on the combined interpreter with the Qt seal armed and `backend` left
  off the block list.
- `tests/test_no_comfyui.py` — roadmap criterion 4 gate: a tokenize-based text
  scan (comments stripped) and a route scan over the built app, both reporting
  examined counts and proven non-inert in three directions (planted source
  flagged, removal comment not flagged, decoy route flagged).
- `tests/test_backend_tracer.py` — static surface test (adapter lazy-imports
  `visoswap.engine` and touches only the public surface) plus a subprocess
  assertion that the tracer produced a non-empty frame at provider CUDA with
  PySide6 unreachable.
- `source_face_path` column + adapter wiring.
- Finished `docs/backend-port.md`.

## Deviations from Plan

**1. [Rule 2 - Missing critical functionality] The engine adapter had no settings source.**
- **Found during:** Task 3 (tracer).
- **Issue:** `_get_engine()` built `Engine(device="cuda")` with no settings, so
  the engine's unconditional control/parameter reads raised `KeyError`
  (`ManualRotationEnableToggle`, then `SimilarityThresholdSlider`) on every bind.
  The plan's Task 2 said settings must come from the Phase 3 store; the committed
  adapter never did.
- **Fix:** the adapter now resolves the global tier (`store.resolve_control`) and
  project tier (`store.resolve_parameters`) from the backend's app DB at
  engine-build time, and `bind()` sets the project before building the engine so
  the project tier is present when `detect_faces` runs.
- **Files modified:** `backend/services/engine_backend.py`
- **Commit:** `70af280`

**2. [Rule 2 - Missing critical functionality] No source-face path existed.**
- **Found during:** Task 3 (tracer).
- **Issue:** the adapter read `project["source_face_path"]`, but the ported
  project schema had no such column — the reference backend sourced faces by name
  from VisoMaster's folder, which this repo drops. Nothing could tell the engine
  which face to swap.
- **Fix:** added `source_face_path` to the projects schema, migration, create and
  update paths, and the `ProjectUpdate` schema; stripped it from the public API
  response like `video_path` so absolute paths never reach the browser. Plan 04-04
  ("sever VisoMaster dependency") now builds on this rather than inventing a
  second mechanism.
- **Files modified:** `backend/models/database.py`, `backend/api/schemas.py`,
  `backend/api/projects.py`
- **Commit:** `70af280`

## Verification, verbatim

```
$ .venv-clean/Scripts/python.exe -B tests/_backend_runner.py --tracer
CLEAN:tracer:frame=000000.000.jpg size=336835 provider=CUDA elapsed=10.7s reachable_before_seal=...

$ .venv-clean/Scripts/python.exe -m pytest tests/test_no_comfyui.py tests/test_backend_tracer.py \
      tests/test_engine_surface.py tests/test_vendor_headers.py -q
15 passed in 15.97s

$ .venv-clean/Scripts/python.exe -m pytest tests/ -q
293 passed in 65.47s

$ grep -ril comfyui backend/ --include="*.py" | grep -v __pycache__
(no matches)
```

Test count: **286 → 293**, +7 (5 ComfyUI gate, 1 static surface, 1 tracer
subprocess). Zero skips.

## Requirements marked

BACKEND-01 is progressed (criterion 4 and the criterion-5 first proof are now
met); the full BACKEND-01 criterion set still requires plans 04-02/03/04
(recorder hang, model bootstrap, VisoMaster severing). ROADMAP/STATE not touched
by the executor.

## Known Stubs

None. Nothing here is placeholder, no test is skipped.

## Threat Flags

- `source_face_path` is a new project column carrying an absolute filesystem
  path. It is server-assigned only (not in `ProjectCreate`, and stripped from the
  public response via `_public`), so no path crosses a client trust boundary. The
  engine adapter reads it straight off the DB row.

## What Phase 4 inherits

- `tests/_backend_runner.py --tracer` is the template plans 04-03/04-04 extend
  (model-bootstrap refusal and the no-VisoMaster proof run through the same
  runner).
- The `source_face_path` column is the seam 04-04's face-sourcing work builds on.
- The ComfyUI gate begins scanning new `backend/` files automatically; nothing
  needs an edit to start.

## Self-Check: PASSED

All committed files present. 5 commits found in `git log`: `152e05f`, `306e3c2`,
`aeb145e`, `70af280`, `3ddae24`.
