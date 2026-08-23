# Phase 05.1 — External API Coverage Declaration

**Detector:** `api-coverage`, run over the Phase 05.1 plan scope on 2026-08-24.
**Result:** **No external API integration.**

## Reasoned declaration

Phase 05.1 adds no client for any third-party API, SDK, or hosted service. No API
key, OAuth flow, webhook, rate limit, or vendor error taxonomy enters the codebase.
A coverage matrix would therefore have zero rows, and fabricating one would assert
coverage of endpoints that do not exist.

Every boundary the phase crosses is local to the user's machine:

| Boundary the phase touches | Kind | Why it is not an external API |
|---|---|---|
| `visoswap/engine.py` `Engine.load` / `detect_faces` / `swap` | in-process Python call | Vendored engine, three published methods pinned by `tests/test_engine_surface.py`. |
| `backend/api/faces.py` (new), `projects.py`, `playback.py`, `generation.py`, `gallery.py` (new) | first-party HTTP | This repo's own FastAPI routers, consumed by this repo's own frontend. |
| `/ws/projects/{project_id}` | first-party WebSocket | `backend/api/ws.py`; informational-only, never authoritative. |
| `ffmpeg` / `ffprobe` | local subprocess | Binaries on `PATH`, invoked by `backend/services/ffmpeg.py` and `recorder.py`. |
| SQLite via `aiosqlite` | local file | `backend/models/database.py`, one file under `data/`. |
| ONNX Runtime / torch model sessions | in-process | Weights already on disk under `model_assets`; Phase 4 bootstrap owns acquisition. |
| `POST /api/projects/{id}/url` remote fetch | pre-existing | Already shipped in Phase 4 (`backend/api/projects.py`, `enable_ytdlp`). Phase 05.1 adds no new remote source type and no new vendor client. |

## Consequence for the plans

No plan in this phase carries an API-contract task, a credential-handling task, or a
vendor-error-mapping task. Error mapping stays on the existing first-party pattern
(`backend/api/settings.py::_settings_error`, referenced by plans 02, 03 and 08).

Re-run the detector if a later phase introduces a hosted model provider, a cloud
storage target for takes, or a telemetry sink. None of those is in Phase 05.1 scope.
