# VisoSwap — standalone face-swap video project

Status: approved 2026-08-21. Supersedes the ComfyUI-backed arrangement for now.

## Why

`visomaster_live` works, but only on a machine that already has VisoMaster
installed at `D:/Visomaster`. It drives that install by constructing VisoMaster's
real Qt `MainWindow` hidden, then reaching into `main_window.control`,
`main_window.target_faces`, and `main_window.models_processor`.

That costs two things. It drags ~299k LOC of Qt UI along to reach ~10.6k LOC of
inference. And it makes output depend on hidden state: the backend sets 10 of
the ~200 settings keys, and the rest come from whatever the GUI last saved to
disk. The same project can render differently on two machines and nothing in the
repo explains why.

The standalone project vendors the inference code, makes every setting explicit
and stored, and drops Qt entirely.

## Scope

**Carried over from `visomaster_live`:** `api/`, `models/database.py`,
`workers/`, `services/{scheduler,cache,ffmpeg,video,events,recorder,faceselect}.py`,
the React frontend, `launcher/`, `userscript/`, `tests/`.

**Rewritten:** `services/inprocess.py` and `services/visomaster.py` (922 LOC of
hidden-window driving) become a Qt-free engine package.

**Dropped:** `services/comfyui.py`, `api/comfyui.py`, and the `comfyui` branch of
the `backend` column. The `FrameGenerator` interface stays — that is the seam
ComfyUI returns through later, and removing it would have to be undone.

**Lifted from the VisoMaster web UI:** the schema-driven settings layer, its
three-tier global/project/face model, and both saved profiles.

## Layout

```
visoswap/                   vendored engine, GPLv3
  processors/               from app/processors, Qt stripped
  schema/                   generated schema.json + its generator
  models/                   downloader + models_data
backend/                    as today, minus comfyui
frontend/
launcher/  userscript/  tests/
```

`visoswap/` must import and run with no `backend` import and no PySide6
installed. That is the test that the boundary is real rather than nominal.

## Engine boundary

Vendor `app/processors/` (10,635 LOC). Measured by running the import probe
against unmodified source with Qt blocked: **8 of the 10 modules already import
clean**. Only two fail.

- `video_processor.py` (421 LOC) is a Qt playback loop. **Dropped whole** — the
  scheduler already owns playback.
- `models_processor.py` imports `QtCore`, subclasses `QtCore.QObject`, and
  declares two Signals — and beyond those, emits `model_loading_signal` /
  `model_loaded_signal` on the host window at 8 sites (lines 128, 142, 149, 163,
  170, 181, 280, 283) and touches `model_load_dialog` at 3 (215, 218, 219).
  Those are the engine's only model-load progress signalling; they are deleted,
  and the re-add path is recorded so a later phase finds a decision rather than
  an absence.
- `workers/frame_worker.py` (1306 LOC, the actual swap pipeline) imports no Qt
  directly, but imports two `app/ui` action modules (lines 16-17) that pull in
  PySide6 transitively. It calls exactly two functions from them:
  `get_pixmap_from_frame` (line 57, the display path) and
  `update_parameters_and_control_from_marker` (line 43, timeline markers). Both
  are on paths we drop, so both imports go. It also reads
  `swapfacesButton.isChecked()` / `editFacesButton.isChecked()` at five sites
  (48, 137, 163, 170, 183) purely as booleans, and signal emission plus
  frame-queue plumbing at lines 60-78 — again the display path.

`app/processors/` does **not** stand alone: it hard-imports
`app/helpers/{downloader,miscellaneous,integrity_checker}.py` at module scope.
`downloader` and `integrity_checker` are vendored into `visoswap/models/`; only
the five symbols actually imported from `miscellaneous` come across.

Blocking `PySide6` alone does not prove anything. `frame_worker.py` fails with
`QtBindingsNotFoundError` raised by **`qtpy`**, not by the block. The gate blocks
`PySide6`, `PySide2`, `PyQt5`, `PyQt6`, `qtpy`, `shiboken6`, and `shiboken2`.

`main_window` is replaced by an explicit context object. The raw attribute
surface across the coupled files is 15, but 8 of those die with
`video_processor.py`. `EngineContext` carries **7**: `control`, `parameters`,
`target_faces`, `models_processor`, `dfm_models_data`, `swap_faces_enabled`,
`edit_faces_enabled` — pinned by a test so it cannot quietly grow back into a
god object.

Public API:

```
Engine.load(video)
Engine.detect_faces() -> [FaceCard]
Engine.swap(frame_number, source, settings) -> ndarray
```

`FaceCard` is a dataclass holding the embedding store and the crop. No Qt
widget, no `face_id` tied to a UI element.

## Settings

The web UI already derives its schema rather than hand-listing it: `web_ui.py`
imports four `*_layout_data` dicts and flattens them to JSON, and the frontend
generates every control from that. Keep the approach; fix what it gets wrong.

Counted by importing the dicts: swapper 103, face_editor 42, common 23, settings
33. The first three are the project tier (168 keys), `settings` is the global
tier (33 keys), and the two sets do not overlap.

A checked-in generator dumps `schema.json` once, offline. This is the only step
that needs Qt — `settings_layout_data.py` transitively imports PySide6 through
`control_actions.py` — and it runs during development, never on a user's
machine. The generator adds two things the current schema lacks:

- **An explicit `type` per key.** Today both the JS renderer and the value
  coercion infer type from substrings in the key name (`Toggle`, `Selection`,
  `Decimal`). A key that does not follow the convention silently renders as the
  wrong control.
- **Typed defaults.** Today values round-trip as strings (`"0.9"`, `"50"`) and
  are re-coerced against `window.default_parameters` — meaning type information
  lives in the Qt window rather than in the data.

Dynamic `options` (model lists read from disk) resolve at startup against the
models directory, so a frozen schema does not stale out.

Three tiers, all persisted:

| Tier | Keys | Storage |
|---|---|---|
| global | 33 control keys, from `settings_layout_data` | app config |
| project | 168 parameter keys, from `common` + `swapper` + `face_editor` | new `project_settings` table |
| face | sparse overrides | new `face_settings` table |

Resolution is global, then project, then face; each tier stores only overrides.

Per-face settings are **keyed by recognition embedding**, not by index. The web
UI keys them by ephemeral target index and holds them in browser memory, so they
reset on project load (`webui2/app.js:254-258`) — there is no per-face
persistence to lift, only a model to borrow. Embedding keying is what makes them
survive a reload, and it reuses the embedding matching already committed for
face selection.

Both entries in `profiles.json` migrate into a seeded `presets` table with
values coerced to real types. Drop the hardcoded `SwapModelSelection` and
`SwapperResSelection` overrides re-applied on every load at `web_ui.py:372-373`
and `:1199-1200`.

## Models

Vendor `app/helpers/downloader.py` and `models_data.py`, which already carry
URLs and hashes for all 54 files (12GB). On first run, check the models
directory, fetch what is missing with hash verification, and refuse to start if
incomplete rather than failing later inside inference.

`MODELS_DIR` is env-driven and defaults to `./model_assets`, so an existing
install can be pointed at instead of re-downloaded.

## Licensing

VisoMaster is GPLv3, so vendoring makes GPLv3 the only option — it is forced,
not chosen. Full licence text, and headers on vendored files attributing
VisoMaster.

Separately, insightface's weights — including the `genderage.onnx` the female
face selection depends on — are licensed for non-commercial research use. That
is independent of the GPL and blocks nothing today, but it means the project
cannot ship commercially without replacing that model. It belongs in the README
now, not when someone discovers it later.

## Milestones

1. `visoswap/` imports and swaps one frame with no PySide6 installed. Everything
   else depends on this, so it goes first and it is the one that can fail.
2. Schema generated; settings resolve across the three tiers.
3. Backend runs on the new engine; the existing test suite passes.
4. Frontend renders from the schema; both profiles migrated.
5. Recorder, launcher, and userscript verified end to end.

## Known risks

- The DFM, liveportrait, and clipseg paths are vendored but exercised only if
  used. They may carry coupling not found by inspection.
- `test_recorder.py::test_a_cancelled_run_leaves_a_playable_partial` hangs
  indefinitely on master today. It is carried over broken and should be fixed in
  the new repo, not silently deselected forever.
