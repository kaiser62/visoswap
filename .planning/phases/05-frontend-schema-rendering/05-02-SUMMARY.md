---
phase: 05-frontend-schema-rendering
plan: 02
subsystem: api
tags: [fastapi, settings, presets, sqlite, pydantic, asyncio]

# Dependency graph
requires:
  - phase: 05-frontend-schema-rendering
    plan: 01
    provides: settings read API router, sync sqlite3 bridge, path-aware readiness helper, _settings_error helper
provides:
  - PUT /api/projects/{id}/settings write endpoint (persist overrides, round-trip)
  - POST /api/projects/{id}/presets/{preset_id} preset apply endpoint (atomic)
  - pydantic models SettingsUpdate, SettingsResponse, PresetApplyResponse in backend/api/schemas.py
  - write-API contract tests (round-trip, rejection matrix, preset apply)
affects: [05-frontend-schema-rendering plans 04-05, frontend save + preset apply flows]

# Actuals
actuals:
  tokens: 2400
  tasks: 2
  commits: 1

# Tech tracking
tech-stack:
  added: []
  patterns: [caller-owned transaction, per-key store.write loop, atomic preset apply]

key-files:
  created:
    - tests/test_api_settings_write.py
  modified:
    - backend/api/settings.py
    - backend/api/schemas.py

key-decisions:
  - "PUT iterates overrides calling store.set_project per key inside one transaction; rollback on the first error guarantees zero partial writes for a mixed body"
  - "Preset apply runs presets.get_preset (404) then presets.apply_preset in a caller-owned transaction committed only on success"
  - "Fixed the _settings_error helper to reference validate.InvalidSettingValue (store does not re-export it) — a latent bug the read path never exercised"

patterns-established:
  - "Pattern: write endpoint = to_thread(sync block: connect, ensure ready, per-key store.write, commit, resolve_all, close)"
  - "Pattern: atomicity — commit/rollback owned by the handler, store functions never commit"

requirements-completed: ["FRONTEND-01"]

coverage:
  - id: D1
    description: "PUT /api/projects/{id}/settings persists exactly the provided overrides and a fresh GET returns them (round-trip)"
    requirement: FRONTEND-01
    verification:
      - kind: integration
        ref: tests/test_api_settings_write.py#test_put_round_trip_persists_typed_values
        status: pass
    human_judgment: false
  - id: D2
    description: "PUT rejection matrix: wrong-tier key, unknown key, out-of-bounds int, string-for-int, mixed body -> 400 with zero partial writes; empty overrides no-op; unknown project 404"
    requirement: FRONTEND-01
    verification:
      - kind: integration
        ref: tests/test_api_settings_write.py#test_put_global_tier_key_returns_400_and_stores_nothing
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_put_unknown_key_returns_400
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_put_out_of_bounds_int_returns_400
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_put_string_for_int_key_returns_400
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_put_empty_overrides_noop_returns_current_values
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_put_mixed_body_stores_nothing
        status: pass
    human_judgment: false
  - id: D3
    description: "POST /api/projects/{id}/presets/{preset_id} applies both migrated presets atomically with a stable report; 404 on unknown preset; idempotent"
    requirement: FRONTEND-01
    verification:
      - kind: integration
        ref: tests/test_api_settings_write.py#test_apply_preset_A_reports_and_changes_values
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_apply_preset_with_aud_reports_and_changes_values
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_apply_preset_is_idempotent
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_apply_unknown_preset_returns_404
        status: pass
      - kind: integration
        ref: tests/test_api_settings_write.py#test_apply_preset_unavailable_is_a_list
        status: pass
    human_judgment: false

# Metrics
duration: 18min
completed: 2025-01-15
status: complete
---

# Plan 05-02: Backend Write API for Settings and Preset Apply

**Write endpoints PUT /api/projects/{id}/settings (persist-only-overrides, atomic rejection) and POST /api/projects/{id}/presets/{preset_id} (atomic preset apply with report), with pydantic request models and full write-API contract tests.**

## Performance

- **Duration:** 18 min
- **Completed:** 2025-01-15
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- `PUT /api/projects/{id}/settings`: persists exactly the provided overrides to the project tier, round-trips through a fresh connection, rejects the full invalid-input matrix with 400 and zero partial writes, answers 404 for an unknown project, and no-ops on empty overrides.
- `POST /api/projects/{id}/presets/{preset_id}`: applies both migrated presets atomically via `presets.apply_preset` in a caller-owned transaction, returns a stable report plus freshly resolved values, answers 404 for an unknown preset, and is idempotent.
- Added pydantic models `SettingsUpdate`, `SettingsResponse`, `PresetApplyResponse` to `backend/api/schemas.py`; the `overrides` field deliberately accepts arbitrary keys so per-key validation is the store's job.
- 13 write-API contract tests green on `.venv-clean`; full suite 369 passed (was 356).

## Task Commits

1. **Task 1: Tracer - PUT settings round-trip, changed values only** - (in `0d5e4a6`)
2. **Task 2: Preset apply endpoint with atomic transaction + report** - (in `0d5e4a6`)

**Plan metadata:** `0d5e4a6` (feat(05-02): settings write API + preset apply + contract tests)

## Files Created/Modified
- `backend/api/settings.py` - PUT settings and POST preset-apply endpoints; fixed `_settings_error` to use `validate.InvalidSettingValue`
- `backend/api/schemas.py` - Added SettingsUpdate, SettingsResponse, PresetApplyResponse models
- `tests/test_api_settings_write.py` - Write-API contract tests

## Decisions Made
- Wrapped the per-key store writes in one caller-owned transaction committed only on success, guaranteeing a mixed valid+invalid body stores nothing.
- Resolved preset id via `presets.get_preset` before `apply_preset` so an unknown preset answers 404 before any write.
- Did not coerce types in the API layer; the Phase 3 store's validator rejects wrong types (e.g. a string for an int key) with 400.

## Deviations from Plan

### Auto-fixed Issues

**1. [Blocking] `_settings_error` referenced a nonexistent store attribute**
- **Found during:** Task 1 (PUT endpoint, rejection test)
- **Issue:** `backend/api/settings.py` mapped errors via `store.InvalidSettingValue`, but the Phase 3 store does not re-export `InvalidSettingValue` — it lives in `visoswap.settings.validate`. The read-only path never hit this branch, so plan 01's tests passed; the write path surfaced `AttributeError` on the first rejection.
- **Fix:** Imported `validate` and changed the tuple to `validate.InvalidSettingValue`.
- **Files modified:** backend/api/settings.py
- **Verification:** test_put_global_tier_key_returns_400_and_stores_nothing passes; full suite 369 green.
- **Committed in:** 0d5e4a6

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary for the write path's error mapping to function. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviation above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Write API is complete and green; the frontend save (PUT) and preset-apply (POST) flows in plans 04-05 have their backend path ready.
- Response shapes documented in the plan's interfaces (`{"values": {...}}` for PUT; `{"report": {...}, "values": {...}}` for preset apply) are the contract the frontend will consume.

---
*Phase: 05-frontend-schema-rendering*
*Completed: 2025-01-15*
