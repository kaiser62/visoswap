---
phase: 05-frontend-schema-rendering
plan: 04
subsystem: ui
tags: [react, vite, save, presets, dirty-tracking, modal]

# Dependency graph
requires:
  - phase: 05-frontend-schema-rendering
    plan: 02
    provides: PUT settings + POST preset-apply backend endpoints, ApiError-mapped 400s
  - phase: 05-frontend-schema-rendering
    plan: 03
    provides: SettingsContext (dirty map), lib/api.ts, ui.tsx primitives
provides:
  - Dirty-tracked Save Changes (N) flow (D-02): change -> dirty -> PUT changed-only -> clear on success
  - Save failure banner with dirty retained + retry; Discard requires confirmation and reverts to baseline
  - Preset selector + diff confirmation modal (D-04): apply only after explicit confirm; failure persists nothing
  - Pure diff module (lib/diff.ts) and reusable ConfirmModal
  - save-flow + preset-flow integration test suites
affects: [05-frontend-schema-rendering plan 05, final gates + browser E2E]

# Actuals
actuals:
  tokens: 6986
  tasks: 2
  commits: 1

# Tech tracking
tech-stack:
  added: []
  patterns: [dirty-tracking in reducer, baseline values for discard, confirm-modal gating, pure diff function]

key-files:
  created:
    - frontend/src/components/Header.tsx
    - frontend/src/components/ConfirmModal.tsx
    - frontend/src/components/PresetSelector.tsx
    - frontend/src/components/PresetDiffModal.tsx
    - frontend/src/lib/diff.ts
    - frontend/src/test/save-flow.test.tsx
    - frontend/src/test/preset-flow.test.tsx
  modified:
    - frontend/src/state/SettingsContext.tsx
    - frontend/src/App.tsx

key-decisions:
  - "Save dispatches SAVE_OK with the server's returned values (source of truth) and clears dirty; SAVE_ERROR retains dirty + sets the documented message"
  - "baselineValues tracks the last-persisted values so Discard can revert exactly"
  - "Preset apply dispatches APPLY_PRESET replacing values from the response and clearing dirty; the POST only fires from the confirmed modal"
  - "PresetDiffModal uses diffPreset(preset, values, dirty) to compute changed keys + unsaved-edit intersection"

patterns-established:
  - "Pattern: reducer actions SAVE_START/SAVE_OK/SAVE_ERROR/DISCARD/APPLY_PRESET for the write lifecycle"
  - "Pattern: pure diff function (diffPreset) kept in lib/diff.ts for testability"
  - "Pattern: destructive/preset actions gated behind a confirmed modal — never a direct trigger"

requirements-completed: ["FRONTEND-01"]

coverage:
  - id: D1
    description: "Change a control -> Save Changes (N) persists exactly the changed keys; dirty clears on success; reload-restore round-trip (FRONTEND-01 criterion 3 frontend half)"
    requirement: FRONTEND-01
    verification:
      - kind: automated_ui
        ref: frontend/src/test/save-flow.test.tsx#changes a control, shows Save Changes (1), PUT body has exactly that key, dirty clears
        status: pass
    human_judgment: false
  - id: D2
    description: "Save failure keeps edits, shows the documented error banner, and leaves the button re-enabled (retry path)"
    requirement: FRONTEND-01
    verification:
      - kind: automated_ui
        ref: frontend/src/test/save-flow.test.tsx#save failure keeps edits and shows the documented error banner
        status: pass
    human_judgment: false
  - id: D3
    description: "Discard requires confirmation with the destructive copy; Cancel changes nothing, Discard reverts to baseline and clears dirty"
    verification:
      - kind: automated_ui
        ref: frontend/src/test/save-flow.test.tsx#Discard requires confirmation, Cancel changes nothing
        status: pass
      - kind: automated_ui
        ref: frontend/src/test/save-flow.test.tsx#Discard confirm reverts values and clears dirty
        status: pass
    human_judgment: false
  - id: D4
    description: "Preset selector lists migrated presets; selecting opens a diff confirmation modal; Apply posts the preset id and replaces values; failure keeps modal open and persists nothing (FRONTEND-01 criterion 2)"
    requirement: FRONTEND-01
    verification:
      - kind: automated_ui
        ref: frontend/src/test/preset-flow.test.tsx#selecting a preset opens the diff modal with the changed-key list
        status: pass
      - kind: automated_ui
        ref: frontend/src/test/preset-flow.test.tsx#Apply posts the preset id and replaces values
        status: pass
      - kind: automated_ui
        ref: frontend/src/test/preset-flow.test.tsx#apply failure keeps the modal open with an error and no value change
        status: pass
      - kind: automated_ui
        ref: frontend/src/test/preset-flow.test.tsx#zero presets renders the disabled selector
        status: pass
    human_judgment: false

# Metrics
duration: 45min
completed: 2025-01-15
status: complete
---

# Plan 05-04: Save + Preset Apply Flows

**Dirty-tracked Save Changes (N) that persists only changed values with the documented save-failure/discard behaviour, and a preset selector with a diff-confirmation modal that applies only on explicit confirm — both pinned by integration tests.**

## Performance

- **Duration:** 45 min
- **Completed:** 2025-01-15
- **Tasks:** 2
- **Files modified:** 9 (7 created, 2 modified)

## Accomplishments
- Header with Save Changes (N) / Discard / preset selector / project picker; Save button enabled only when ≥1 control is dirty, shows "Saving…" while saving.
- Save dispatches SAVE_OK with the server's returned values (source of truth) and clears dirty; a 500 keeps edits and shows the documented error banner with the button re-enabled.
- Discard requires a confirmation modal with the documented destructive copy; Cancel changes nothing, Discard reverts to `baselineValues` and clears dirty.
- Preset selector (zero-state "No presets" disabled) opens a diff modal listing changed settings; Apply POSTs the preset id and replaces values from the response; failure keeps the modal open and persists nothing.
- 9 new integration tests; full frontend suite 19 passed; `npm run build` clean.

## Task Commits

1. **Task 1: Tracer - save flow** - (in `b9f5d2c`)
2. **Task 2: Preset selector + diff confirmation modal** - (in `b9f5d2c`)

**Plan metadata:** `b9f5d2c` (feat(05-04): save + preset apply flows)

## Files Created/Modified
- `frontend/src/components/Header.tsx` - title, project picker, preset selector, Save/Discard, transient Saved indicator, save-error banner
- `frontend/src/components/ConfirmModal.tsx` - destructive discard confirmation (UI-SPEC copy)
- `frontend/src/components/PresetSelector.tsx` - header select + zero-state; opens diff modal
- `frontend/src/components/PresetDiffModal.tsx` - "Apply preset {name}" diff + Cancel/Apply, failure error
- `frontend/src/lib/diff.ts` - pure diffPreset(preset, values, dirty) -> {changedKeys, differsFromUnsaved}
- `frontend/src/state/SettingsContext.tsx` - added saving/saveError/baselineValues + SAVE_*/DISCARD/APPLY_PRESET actions
- `frontend/src/App.tsx` - mount Header
- `frontend/src/test/save-flow.test.tsx` - save/discard flow integration tests
- `frontend/src/test/preset-flow.test.tsx` - preset apply flow integration tests

## Decisions Made
- Server's returned values become the source of truth after a successful save (SAVE_OK) so the UI reflects what the backend accepted.
- `baselineValues` (last-persisted) enables exact Discard revert.
- Preset apply replaces values from the POST response and clears dirty; the POST only fires from the confirmed modal.
- diffPreset computed in a pure module for deterministic testability.

## Deviations from Plan

### Auto-fixed Issues

**1. [Blocking] Header preset fetch swallowed the ui-states error test rejection**
- **Found during:** Task 1 (regression)
- **Issue:** The ui-states error test used `mockRejectedValueOnce`, but the Header's preset fetch now fires before the schema load, consuming the rejection so the error state never triggered.
- **Fix:** Made the error test reject on the schema call specifically (first attempt only), and added a `count` guard so retry succeeds.
- **Files modified:** ui-states.test.tsx
- **Verification:** all 19 tests green.
- **Committed in:** b9f5d2c

**2. [Blocking] Header + modal both expose a "Discard" button**
- **Found during:** Task 2 (Discard-confirm test)
- **Issue:** `getByRole('button', { name: 'Discard' })` matched both the header Discard and the modal confirm Discard.
- **Fix:** Scoped the confirm click with `within(screen.getByRole('dialog'))`.
- **Files modified:** save-flow.test.tsx
- **Verification:** Discard-confirm test passes.
- **Committed in:** b9f5d2c

---

**Total deviations:** 2 auto-fixed (both blocking test robustness)
**Impact on plan:** Necessary to keep the existing ui-states suite and the new save-flow suite green. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

### Post-close addendum (human UAT)

**3. [Blocking] Save failed for hand-edited numeric values** — found during the browser walkthrough after phase close. Two client paths produced the documented save-failure banner: a cleared number input parsed to NaN and shipped JSON `null` (API 422), and hand-typed values bypassed the inputs' min/max so out-of-bounds numbers reached store.validate (400). Fix: `SchemaControl.emitNumber` ignores non-finite parses and clamps into schema bounds before marking dirty; regression test `typed out-of-bounds numbers are clamped and never ship null` pins both. Frontend suite 28 green; rebuilt dist served by the running backend.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Save + preset flows complete and green; FRONTEND-01 criteria 2 and 3 frontend halves are proven at the component level.
- Plan 05 (final gates + browser E2E) can now run the app end-to-end against a live backend.

---
*Phase: 05-frontend-schema-rendering*
*Completed: 2025-01-15*
