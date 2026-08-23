---
phase: 05-frontend-schema-rendering
plan: 01
subsystem: api
tags: [fastapi, settings, sqlite, schema, presets, asyncio, httpx]

# Dependency graph
requires:
  - phase: 04-backend-integration-model-bootstrap
    provides: FastAPI app with model bootstrap startup gate, async aiosqlite Database, projects/deps router patterns
provides:
  - GET /api/schema read endpoint (201-entry typed settings schema, DFM options resolved)
  - GET /api/projects/{id}/settings read endpoint (201 typed values via Phase 3 store)
  - GET /api/presets read endpoint (2 migrated presets: A, with AUD)
  - Sync sqlite3 bridge over async aiosqlite backend via asyncio.to_thread
  - Self-sufficient settings bootstrap (DDL + preset seed, path-aware readiness helper)
  - Settings read-API contract tests
affects: [05-frontend-schema-rendering plans 02-05, frontend plans 03-04]

# Actuals
actuals:
  tokens: 2689
  tasks: 2
  commits: 1

# Tech tracking
tech-stack:
  added: [httpx]
  patterns: [sync-connection bridge, path-aware readiness helper, error-mapping helper]

key-files:
  created:
    - backend/api/settings.py
    - tests/test_api_settings.py
  modified:
    - backend/main.py
    - requirements-backend-dev.txt

key-decisions:
  - "Aliased the settings module import as settings_api in backend/main.py to avoid the name collision with the local get_settings() binding inside create_app()"
  - "Settings router opens a synchronous sqlite3 connection to get_settings().db_path (not the async Database singleton) run in asyncio.to_thread — the Phase 3 store is synchronous and the Phase 4 backend is async"
  - "Readiness helper applies apply_settings_schema + seed_presets once per resolved db path, keyed in a set, so the API works on a bare database regardless of Phase 4 state"

patterns-established:
  - "Pattern: sync sqlite3 bridge — open sqlite3.connect(db_path) in asyncio.to_thread for Phase 3 store calls; never reuse the aiosqlite singleton"
  - "Pattern: path-aware readiness — track initialized db paths in a set, re-run DDL+seed when the path differs (tests repoint the database)"
  - "Pattern: _settings_error helper — map store/preset errors to 400, sqlite3.Error to 500, reused by plan 02 write path"

requirements-completed: ["FRONTEND-01"]

coverage:
  - id: D1
    description: "GET /api/schema returns the resolved 201-entry typed settings schema with DFM options"
    requirement: FRONTEND-01
    verification:
      - kind: integration
        ref: tests/test_api_settings.py#test_schema_returns_201_typed_entries
        status: pass
      - kind: integration
        ref: tests/test_api_settings.py#test_schema_carries_dfm_options_key
        status: pass
    human_judgment: false
  - id: D2
    description: "GET /api/projects/{id}/settings resolves 201 typed values through the Phase 3 store; unknown project 404, malformed id 400"
    requirement: FRONTEND-01
    verification:
      - kind: integration
        ref: tests/test_api_settings.py#test_project_settings_resolve_201_typed_values
        status: pass
      - kind: integration
        ref: tests/test_api_settings.py#test_project_settings_unknown_project_returns_404
        status: pass
      - kind: integration
        ref: tests/test_api_settings.py#test_project_settings_malformed_id_returns_400
        status: pass
      - kind: integration
        ref: tests/test_api_settings.py#test_every_settings_value_type_matches_its_entry
        status: pass
    human_judgment: false
  - id: D3
    description: "GET /api/presets lists the 2 migrated presets (A, with AUD) with payload keys within the schema key set"
    requirement: FRONTEND-01
    verification:
      - kind: integration
        ref: tests/test_api_settings.py#test_presets_lists_the_two_migrated_presets
        status: pass
      - kind: integration
        ref: tests/test_api_settings.py#test_preset_payload_keys_are_schema_keys
        status: pass
    human_judgment: false
  - id: D4
    description: "Settings API self-bootstraps DDL and presets on a bare database, path-aware across repointed databases"
    verification:
      - kind: integration
        ref: tests/test_api_settings.py#test_readiness_helper_is_path_aware
        status: pass
    human_judgment: false

# Metrics
duration: 20min
completed: 2025-01-15
status: complete
---

# Plan 05-01: Backend Read API for Schema, Settings, Presets

**Settings read API router (GET /api/schema, /api/projects/{id}/settings, /api/presets) wrapping the Phase 3 store over a sync sqlite3 bridge, with a path-aware readiness helper that self-bootstraps the settings DDL and preset seed on a bare database.**

## Performance

- **Duration:** 20 min (including resume from prior-session checkpoint)
- **Started:** (resumed from handoff at task 1/2)
- **Completed:** 2025-01-15
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Three read endpoints over the FastAPI app: `GET /api/schema` (201 typed entries, DFM options resolved), `GET /api/projects/{id}/settings` (201 typed values through the Phase 3 store), `GET /api/presets` (2 migrated presets).
- Sync/async bridge proven end-to-end: synchronous sqlite3 connection to `get_settings().db_path` run in `asyncio.to_thread`, never reusing the aiosqlite singleton.
- Path-aware readiness helper applies `apply_settings_schema` + `seed_presets` once per resolved db path, making the API self-sufficient on a bare database.
- Error-mapping helper translates store/preset errors to 400 and `sqlite3.Error` to 500, ready for the plan 02 write path.
- 9 contract tests green on `.venv-clean`; full suite 356 passed (was 347).

## Task Commits

1. **Task 1: Tracer - GET /api/schema and /api/projects/{id}/settings end-to-end** - (in `2e4f92d`)
2. **Task 2: Read-API contract hardening** - (in `2e4f92d`)

**Plan metadata:** `2e4f92d` (feat(05-01): settings read API + contract tests)

## Files Created/Modified
- `backend/api/settings.py` - Settings read API router: GET /api/schema, GET /api/projects/{id}/settings, GET /api/presets; sync sqlite3 bridge, path-aware readiness helper, error-mapping helper
- `backend/main.py` - Wire settings router; aliased module import as `settings_api` to avoid the `get_settings()` local name collision
- `requirements-backend-dev.txt` - Added httpx (FastAPI TestClient requirement)
- `tests/test_api_settings.py` - Contract tests for all three read endpoints + hardening (value typing, error mapping, preset payload, path-aware readiness)

## Decisions Made
- Aliased the settings module import in `backend/main.py` to avoid the collision with the local `settings = get_settings()` binding inside `create_app()`.
- Opened a synchronous sqlite3 connection (not the async Database singleton) run in `asyncio.to_thread` for Phase 3 store calls — the two drivers cannot be mixed.
- Made readiness path-aware (set of resolved db paths) so tests that repoint the database still work.

## Deviations from Plan

### Auto-fixed Issues

**1. [Blocking] Name collision in backend/main.py**
- **Found during:** Task 1 (router wiring)
- **Issue:** Adding `settings` to the `from backend.api import ...` line collided with the existing `settings = get_settings()` local inside `create_app()`, causing `AttributeError: 'Settings' object has no attribute 'router'` at import time and failing test collection.
- **Fix:** Aliased the module import as `settings_api` and used `app.include_router(settings_api.router)`.
- **Files modified:** backend/main.py
- **Verification:** `.venv-clean` full suite 356 passed.
- **Committed in:** 2e4f92d

**2. [Blocking] Malformed-id test hit FastAPI 422 before validate_project_id**
- **Found during:** Task 2 (error mapping)
- **Issue:** The test used `"nothex"` (6 chars) which FastAPI's `Path(min_length=32)` rejected as 422, never reaching the 400 error-mapping path. The 400 case is a 32-char id that fails hex validation.
- **Fix:** Changed the test to a 32-char `"z"*32` id that passes length validation but fails the hex regex → 400.
- **Files modified:** tests/test_api_settings.py
- **Verification:** test_project_settings_malformed_id_returns_400 passes.
- **Committed in:** 2e4f92d

---

**Total deviations:** 2 auto-fixed (1 blocking name collision, 1 blocking test correctness)
**Impact on plan:** Both auto-fixes necessary for the API to build and for the 400 error-mapping contract to be exercised. No scope creep.

## Issues Encountered
None beyond the two auto-fixed deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Read API is complete and green; ready for plan 05-02 (write API: PUT settings + preset apply), which reuses the `_settings_error` helper and the sync-bridge/readiness patterns.
- The `_settings_error` helper already maps `store.UnknownSettingsKey/InvalidSettingValue/WrongTier` and `presets.UnknownPreset` to 400, ready for the write path.
- Frontend plans 03-04 can rely on the exact response shapes documented in the plan's interfaces.

---
*Phase: 05-frontend-schema-rendering*
*Completed: 2025-01-15*
