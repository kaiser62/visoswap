# VisoSwap

## What This Is

A self-contained video face-swap application: you load a video, pick a source
face, and the swapped result plays back in real time while frames are generated
asynchronously behind the playhead. It is the working `visomaster_live` project
cut free of its dependency on a local VisoMaster install, with the inference
code vendored and the Qt UI dropped.

For a single technical user running it on their own GPU machine.

## Core Value

Playback never blocks on generation. If a generated frame is missing at its
timestamp, the original video frame is shown — the video does not pause, wait,
or stutter. Everything else is negotiable; this is not.

## Requirements

### Validated

<!-- Shipped and confirmed valuable in visomaster_live, carried over. -->

- ✓ Real-time playback with asynchronous predictive frame generation — existing
- ✓ Deterministic target timestamps `T_k = k * interval`, nearest-previous frame selection — existing
- ✓ One job per `(project_id, timestamp)` with bounded retry — existing
- ✓ Project cache on disk plus sqlite job/frame state — existing
- ✓ Off-playback-path recorder composing an output mp4 — existing
- ✓ Single-face targeting: confident female first, most prominent face otherwise — existing
- ✓ Env-driven ports for parallel workspaces — existing

### Active

- [ ] Swap a frame with no PySide6 installed and no VisoMaster install present
- [ ] Generated settings schema with explicit types and typed defaults
- [ ] Three-tier settings resolution: global, project, per-face
- [ ] Per-face settings persisted, keyed by recognition embedding
- [ ] Both existing profiles migrated as seeded presets
- [ ] Model bootstrap: verify, download missing by hash, refuse to start if incomplete
- [ ] Frontend renders all controls from the schema
- [ ] GPLv3 licensing with VisoMaster attribution, non-commercial weights documented

### Out of Scope

- ComfyUI backend — deferred, not abandoned. The `FrameGenerator` interface stays
  as the seam it returns through, so removing it would only have to be undone.
- VisoMaster's Qt UI — the whole point of the project is not needing it.
- `video_processor.py`, VisoMaster's Qt playback loop — the scheduler already
  owns playback; keeping both would mean two schedulers.
- Multi-face swapping — replaced by single-face targeting, deliberately, and the
  old behaviour is not coming back as a toggle.
- Multi-user, auth, hosting — single user on their own machine.

## Context

The existing project works, but only on a machine with VisoMaster installed at
`D:/Visomaster`. It drives that install by constructing VisoMaster's real Qt
`MainWindow` hidden, then reaching into `main_window.control`,
`main_window.target_faces`, and `main_window.models_processor`.

Two costs. It drags ~299k LOC of Qt UI along to reach ~10.6k LOC of inference.
And output depends on hidden state: the backend sets 10 of ~200 settings keys,
and the rest come from whatever the GUI last saved to disk — so the same project
can render differently on two machines with nothing in the repo explaining why.

Prior work being drawn on:

- `visomaster_live` — the working application. Backend ~4.4k LOC, React
  frontend, launcher, userscript, test suite.
- VisoMaster's `app/processors/` — 10,635 LOC of inference to vendor. Measured
  Qt coupling is small: `frame_worker.py` (1306 LOC, the actual swap pipeline)
  has zero Qt imports and touches Qt only via two buttons read as booleans.
- A web UI previously built on VisoMaster — already derives its settings schema
  from four `*_layout_data` dicts rather than hand-listing them, and already has
  a three-tier global/project/face model. Its per-face tier lives in browser
  memory and resets on load, so that tier is a model to borrow, not code to lift.

Full design: `docs/specs/2026-08-21-visoswap-standalone-design.md`.

## Constraints

- **Licensing**: GPLv3, forced not chosen — VisoMaster is GPLv3 and its code is
  vendored.
- **Licensing**: insightface weights, including the `genderage.onnx` the face
  selection depends on, are non-commercial research use. Independent of the GPL.
  Blocks nothing today; blocks any commercial release.
- **Dependencies**: `visoswap/` must import and run with no PySide6 and no
  `backend` import. That is the test that the boundary is real.
- **Assets**: 12GB of model weights across 54 files. `MODELS_DIR` is env-driven
  so an existing install can be pointed at rather than re-downloaded.
- **Tech stack**: FastAPI + aiosqlite + ffmpeg + onnxruntime; React + TypeScript
  + Vite. Inherited and not up for revisiting.
- **Performance**: TensorRT provider measured faster than CUDA on this hardware
  (27.8 vs 21.2 fps at 1080p, 1 thread) and is the default.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Vendor `app/processors/`, drop Qt entirely | Qt buys nothing on a server; the parameter boundary is what turns a poked-at GUI into a library | — Pending |
| Drop `video_processor.py` whole | It is a Qt playback loop and the scheduler already owns playback | — Pending |
| Generate `schema.json` offline from the layout dicts | Only step needing Qt, and it runs in development, never on a user machine | — Pending |
| Add explicit types to the schema | Today type is inferred from substrings in key names, so a non-conforming key silently renders wrong | — Pending |
| Key per-face settings by recognition embedding | Index is ephemeral and cannot survive a reload; embedding matching already exists for face selection | — Pending |
| GPLv3 | Forced by vendoring VisoMaster | ✓ Good |
| Defer ComfyUI, keep the `FrameGenerator` seam | It returns later; removing the interface would only have to be undone | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-21 after initialization*
