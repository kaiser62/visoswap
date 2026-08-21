# Roadmap: VisoSwap

## Overview

VisoSwap carries `visomaster_live`'s proven playback/generation backend forward while replacing
its dependency on a live VisoMaster + Qt install with a vendored, Qt-free inference engine and an
explicit, typed settings model. The path runs bottom-up: first prove the vendored engine can
import and swap a frame with no PySide6 present — the one step in this project that can actually
fail, and the one everything else depends on — then give the engine real settings, wire it into
the backend, expose it through the frontend, and finally verify the whole pipeline end to end,
including a fix for the recorder test that hangs on master today.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [ ] **Phase 1: Vendor the Engine & Strip Qt** - `visoswap/processors/` imports cleanly with no PySide6 installed
- [ ] **Phase 2: Engine API & First Swap** - The vendored engine swaps one real frame end to end, with no PySide6 and no VisoMaster install present
- [ ] **Phase 3: Settings Schema & Three-Tier Resolution** - Every setting is typed, generated from the layout dicts, and resolves global → project → face
- [ ] **Phase 4: Backend Integration & Model Bootstrap** - The backend runs on the new engine, refuses to start on incomplete models, and the full existing test suite passes
- [ ] **Phase 5: Frontend Schema Rendering** - Every settings control renders from schema.json, both migrated presets are selectable
- [ ] **Phase 6: End-to-End Verification** - Recorder, launcher, and userscript verified against the new engine, including a cancelled-recording regression check

## Phase Details

### Phase 1: Vendor the Engine & Strip Qt

**Goal**: `visoswap/processors/` exists as an installable package that imports cleanly with zero
Qt dependencies, proving the design's central bet — that the swap pipeline is separable from
VisoMaster's Qt shell — before anything else is built on top of it.
**Why here**: This is the half of Milestone 1 that is pure risk retirement: it isolates "does the
code even import without Qt" from "does it produce a correct swap" (Phase 2). If Qt coupling
exists beyond what the design's static inspection found, it surfaces here, first, before any
downstream phase has invested in an API around it. Nothing before this phase exists to depend on.
**Depends on**: Nothing (first phase)
**Requirements**: None directly (precursor to ENGINE-01, completed in Phase 2); LICENSE-01
**Success Criteria** (what must be TRUE):

  1. `python -c "import visoswap.processors"` exits 0 in a virtualenv where `pip list` / `pip show PySide6` confirms PySide6 is not installed
  2. `visoswap/processors/` contains no `video_processor.py` — it is dropped whole, not stubbed
  3. `models_processor.py` no longer subclasses `QtCore.QObject` and declares no `Signal`
  4. `frame_worker.py`'s two `app/ui` action-module imports that pulled in PySide6 transitively (`get_pixmap_from_frame`, `update_parameters_and_control_from_marker`) are removed, along with the Qt boolean reads (`swapfacesButton.isChecked()`, `editFacesButton.isChecked()`) and the display-path signal/frame-queue plumbing (former lines 60-78) — all replaced by reads on the new context object where still needed
  5. A `LICENSE` file (GPLv3 full text) exists at the repo root, vendored files carry a VisoMaster attribution header, and `grep -ril "pyside\|qtcore\|qtwidgets" visoswap/processors/` returns no matches outside of removal-note comments

**Plans**: 3/4 plans executed

- [x] 01-01-PLAN.md — Package skeleton, GPLv3 licensing, and the Qt-reachability gate proven on one tracer module
- [x] 01-02-PLAN.md — Vendor the Qt-free bulk of `app/processors/` plus `visoswap/models/`, rewriting `app.*` imports
- [x] 01-03-PLAN.md — Enumerate the `main_window` attribute surface, define `EngineContext`, de-Qt `models_processor.py`
- [ ] 01-04-PLAN.md — De-Qt `frame_worker.py`, close the gate on the whole tree, confirm in a Qt-free virtualenv

### Phase 2: Engine API & First Swap

**Goal**: A caller with no PySide6 installed and no VisoMaster install present can load a video,
detect a face, and get back a swapped frame through a stable `Engine` API — the concrete
condition the design names as "the one that can fail."
**Why here**: Depends on Phase 1's import-clean vendoring. This is where Milestone 1's actual
success condition is proven, not merely approximated — after this phase, every later phase has a
real, working engine to build settings, backend, and frontend on top of. It is deliberately kept
separate from settings/backend/frontend work so a failure here is cheap to isolate.
**Depends on**: Phase 1
**Requirements**: ENGINE-01
**Success Criteria** (what must be TRUE):

  1. A smoke test, run in a PySide6-free / no-VisoMaster-install / `backend`-import-free
     environment, loads one video, calls `Engine.detect_faces()`, and gets back at least one
     `FaceCard` for a frame with a visible face

  2. The same smoke test calls `Engine.swap(frame_number, source, settings)` and writes a result
     `ndarray` to disk that differs from the source frame (non-zero pixel diff)

  3. `grep -rn "from backend\|import backend" visoswap/` returns no matches — the engine has no
     dependency on the backend package

  4. The smoke test additionally exercises, at least once each: the DFM swap-model path, the
     LivePortrait editor path, and the CLIPseg-based masking path — each completes without a Qt
     import error or `AttributeError` on a missing Qt attribute (this directly retires the design's
     named risk that these paths carry coupling static inspection did not find)

  5. `FaceCard` holds only the embedding store and crop — no Qt widget reference, no `face_id`
     tied to a UI element
**Plans**: TBD

### Phase 3: Settings Schema & Three-Tier Resolution

**Goal**: Every settings key has a typed, generated representation, and a value set at any of the
three tiers (global, project, face) resolves deterministically — replacing the current system
where type lives in the Qt window and values silently round-trip as strings.
**Why here**: Needs a working `Engine` to validate settings against (Phase 2) but is otherwise
independent of the backend/frontend wiring that follows, so it is proven on its own before
anything downstream (Phase 4's persistence, Phase 5's rendering) depends on its shape being
correct.
**Depends on**: Phase 2
**Requirements**: SCHEMA-01, SCHEMA-02, SCHEMA-03, SCHEMA-04
**Success Criteria** (what must be TRUE):

  1. A checked-in, offline generator produces `visoswap/schema/schema.json` from the four
     `*_layout_data` dicts; the file is valid JSON and every key has both an explicit `type` field
     and a typed (non-string) default where the widget has one

  2. Dynamic `options` lists (model lists read from disk) resolve against the models directory at
     startup, not baked into the frozen `schema.json`

  3. `project_settings` and `face_settings` tables exist and a resolution function returns:
     face-tier value when set → else project-tier value when set → else global default — verified
     by setting a value at each tier in turn and confirming the correct one wins, keyed by
     recognition embedding (not index) for the face tier

  4. A seeded `presets` table contains exactly 2 rows sourced from `profiles.json`, with values
     coerced to real types (not strings)

  5. No hardcoded `SwapModelSelection` / `SwapperResSelection` reassignment remains anywhere in
     the codebase (the two overrides `web_ui.py` re-applied on every load are gone)
**Plans**: TBD

### Phase 4: Backend Integration & Model Bootstrap

**Goal**: The backend serves generation requests through the vendored engine instead of driving a
hidden Qt window, refuses to start when models are incomplete rather than failing mid-inference,
and the full existing test suite passes — including the recorder test that hangs on master today.
**Why here**: Needs both the working `Engine` (Phase 2) and the settings tables the backend will
persist project/face overrides into (Phase 3). This is where the `comfyui` branch is actually
removed and where `services/inprocess.py` / `services/visomaster.py` (922 LOC of hidden-window
driving) are replaced, so it must follow the engine and settings work it depends on and precede
the frontend/E2E phases that assume a working backend.
**Depends on**: Phase 2, Phase 3
**Requirements**: BACKEND-01
**Success Criteria** (what must be TRUE):

  1. `pytest` exits 0 for the full existing suite, and
     `test_recorder.py::test_a_cancelled_run_leaves_a_playable_partial` completes (does not hang)
     and passes — fixed, not skipped or deselected

  2. Starting the backend with `MODELS_DIR` pointed at a directory missing one or more of the 54
     tracked files exits non-zero with a clear error before any generation request is served

  3. Starting the backend with `MODELS_DIR` pointed at a complete set (verified by hash) starts
     normally and `GET /api/health` returns 200

  4. `grep -ril comfyui backend/` returns no matches outside comments explicitly noting removal,
     and no comfyui routes are registered on the FastAPI app

  5. A real generation request through `backend/api/generation.py` produces a frame file on disk
     via the new engine, with no PySide6 process spawned and no reference to a VisoMaster install
     path anywhere in the call path
**Plans**: TBD

### Phase 5: Frontend Schema Rendering

**Goal**: Every settings control a user can touch is rendered from `schema.json` rather than
hand-listed in frontend source, and both migrated presets are selectable and apply correctly.
**Why here**: Needs the schema and seeded presets (Phase 3) and the backend endpoints that
persist tier overrides (Phase 4) to have something real to render against and save to. It is the
last phase where new user-facing surface is built — Phase 6 only verifies what already exists.
**Depends on**: Phase 3, Phase 4
**Requirements**: FRONTEND-01
**Success Criteria** (what must be TRUE):

  1. The count of rendered settings controls in the UI equals the count of keys in `schema.json`
     — no manual per-key mapping remains in frontend source, and no control infers its type from
     substrings in the key name (`Toggle`/`Selection`/`Decimal`)

  2. Selecting either of the 2 migrated presets updates the visible control values to match that
     preset's stored values

  3. Changing a control value and reloading the project restores the changed value (round-trips
     through the project/face tier persisted in Phase 4)

  4. `npm run build` succeeds with no type errors

**Plans**: TBD
**UI hint**: yes

### Phase 6: End-to-End Verification

**Goal**: A user can run the full pipeline — launcher to browser to recorded mp4 — on the new
engine with no VisoMaster or PySide6 dependency anywhere in the path, including a clean cancel
mid-recording.
**Why here**: This is Milestone 5, deliberately last: it has no new user-facing surface of its
own, only verification that everything built in Phases 2-5 holds together as a system, plus final
confirmation that the Phase 4 recorder-hang fix actually holds under a live cancel. Depends on the
backend (Phase 4) and frontend (Phase 5) both being in place.
**Depends on**: Phase 4, Phase 5
**Requirements**: None (v1 Active) — this phase re-verifies capabilities already listed as
Validated in PROJECT.md (recorder, launcher, userscript, deterministic playback, env-driven
ports) against the new engine rather than adding new scope.
**Success Criteria** (what must be TRUE):

  1. A full project run produces a playable output mp4 via the recorder, on a machine with no
     PySide6 and no VisoMaster install present

  2. Cancelling a recording mid-run still leaves a playable partial mp4 (live regression check for
     the Phase 4 fix, not just the unit test)

  3. `launcher/` starts backend and frontend using only `BACKEND_PORT` / `FRONTEND_PORT` / `PORT`
     env vars — no hardcoded 8000 or 5173 anywhere in the launch path

  4. The userscript, loaded against the running frontend, performs its documented function with no
     errors in the browser console

  5. `README` states the GPLv3 license, attributes VisoMaster, and discloses the non-commercial
     licensing of the insightface weights (including `genderage.onnx`)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Vendor the Engine & Strip Qt | 3/4 | In Progress|  |
| 2. Engine API & First Swap | 0/TBD | Not started | - |
| 3. Settings Schema & Three-Tier Resolution | 0/TBD | Not started | - |
| 4. Backend Integration & Model Bootstrap | 0/TBD | Not started | - |
| 5. Frontend Schema Rendering | 0/TBD | Not started | - |
| 6. End-to-End Verification | 0/TBD | Not started | - |
