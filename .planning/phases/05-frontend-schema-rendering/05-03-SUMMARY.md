---
phase: 05-frontend-schema-rendering
plan: 03
subsystem: ui
tags: [vite, react, typescript, tailwind, vitest, jsdom]

# Dependency graph
requires:
  - phase: 05-frontend-schema-rendering
    plan: 01
    provides: settings read API (GET /api/schema, /api/projects/{id}/settings, /api/presets) + documented response shapes
provides:
  - Greenfield Vite + React + TS frontend under frontend/ (Tailwind v4 + vitest)
  - Generic schema-driven renderer SchemaControl dispatching on entry.type only (D-03)
  - SettingsContext + useReducer state container (D-06)
  - Sidebar + GroupSection tier->group hierarchy render (D-01)
  - UI states (loading/error/empty/populated) per UI-SPEC copy
  - Render-count + type-fidelity + ui-states test suites (FRONTEND-01 criterion 1/4)
affects: [05-frontend-schema-rendering plans 04-05, save + preset apply UI]

# Actuals
actuals:
  tokens: 8495
  tasks: 3
  commits: 1

# Tech tracking
tech-stack:
  added: [vite, react, react-dom, typescript, tailwindcss, vitest, jsdom, @testing-library/react, @testing-library/jest-dom, @testing-library/user-event, oxlint]
  patterns: [schema-driven renderer, useReducer state container, React Context + custom hook, fetch helpers with ApiError]

key-files:
  created:
    - frontend/package.json
    - frontend/vite.config.ts
    - frontend/tsconfig.app.json
    - frontend/index.html
    - frontend/src/main.tsx
    - frontend/src/index.css
    - frontend/src/App.tsx
    - frontend/src/types.ts
    - frontend/src/lib/api.ts
    - frontend/src/state/SettingsContext.tsx
    - frontend/src/components/ui.tsx
    - frontend/src/components/SchemaControl.tsx
    - frontend/src/components/Sidebar.tsx
    - frontend/src/components/GroupSection.tsx
    - frontend/src/test/setup.ts
    - frontend/src/test/render-count.test.tsx
    - frontend/src/test/ui-states.test.tsx
  modified: []

key-decisions:
  - "SchemaControl dispatches exclusively on entry.type; no key-string inspection, no per-key mapping (enforced by a test reading the component source for key literals)"
  - "App renders ALL 201 controls simultaneously with the sidebar scrolling to the active section (a filter would break the count test and hide controls)"
  - "Each control carries exactly one data-key/data-control-type so the count test (201) and type tests stay consistent"
  - "Vitest runs with explicit imports (no globals); @testing-library cleanup registered explicitly in setup.ts"

patterns-established:
  - "Pattern: relative /api fetch helpers throwing ApiError{status, detail} — the single data-access layer"
  - "Pattern: React Context + useReducer (SettingsContext) with LOAD_START/LOAD_OK/LOAD_ERROR/SET_PROJECTS/SET_PROJECT/SET_VALUE/RESET_DIRTY actions"
  - "Pattern: small Tailwind primitives (Button/Card/Badge/Spinner/Skeleton/Modal) honoring UI-SPEC tokens"
  - "Pattern: data-key + data-control-type attributes on every control as the test seam"

requirements-completed: ["FRONTEND-01"]

coverage:
  - id: D1
    description: "Greenfield Vite + React + TS app builds clean (npm run build, no type errors) — FRONTEND-01 criterion 4 baseline"
    requirement: FRONTEND-01
    verification:
      - kind: automated_ui
        ref: "cd frontend && npm run build"
        status: pass
    human_judgment: false
  - id: D2
    description: "App renders one control per schema key (201), grouped tier->group with a category sidebar (D-01)"
    requirement: FRONTEND-01
    verification:
      - kind: automated_ui
        ref: frontend/src/test/render-count.test.tsx#renders exactly as many controls as schema keys
        status: pass
    human_judgment: false
  - id: D3
    description: "Control type comes from the schema entry's type field only; no per-key mapping in source (D-03, FRONTEND-01 criterion 1)"
    requirement: FRONTEND-01
    verification:
      - kind: automated_ui
        ref: frontend/src/test/render-count.test.tsx#SchemaControl source contains none of the 201 key strings
        status: pass
    human_judgment: false
  - id: D4
    description: "Five pathological keys render the correct primitive (ClipText text, int/float sliders with bounds/step, string-option select, disabled DFM select)"
    verification:
      - kind: automated_ui
        ref: frontend/src/test/render-count.test.tsx#pathological keys render the right primitive
        status: pass
    human_judgment: false
  - id: D5
    description: "UI states (loading skeletons / error banner + retry / empty copy / populated) per the UI-SPEC contract"
    verification:
      - kind: automated_ui
        ref: frontend/src/test/ui-states.test.tsx#UI states
        status: pass
    human_judgment: false

# Metrics
duration: 55min
completed: 2025-01-15
status: complete
---

# Plan 05-03: Frontend Scaffold + Schema-Driven Renderer

**Greenfield Vite + React + TypeScript frontend rendering all 201 controls from schema.json via one generic type-dispatching renderer, with a tier->group sidebar, React Context state, UI states per the approved UI-SPEC, and a test suite pinning the count/type-fidelity gates.**

## Performance

- **Duration:** 55 min
- **Completed:** 2025-01-15
- **Tasks:** 3
- **Files modified:** 18 created

## Accomplishments
- Greenfield Vite + React 19 + TS + Tailwind v4 app under `frontend/`, buildable with zero type errors (FRONTEND-01 criterion 4 baseline).
- Generic `SchemaControl` dispatches exclusively on `entry.type` (toggle/int/float/selection/text) — no key-name inspection, no per-key mapping. A test reads the component source and asserts none of the 201 key strings appears as a literal.
- `SettingsContext` centralizes state via React Context + useReducer (D-06); `lib/api.ts` is the single typed fetch layer with `ApiError`.
- Sidebar (tier -> group with counts, independent scroll) + `GroupSection` render all 201 controls grouped per D-01; clicking a sidebar item scrolls to the section.
- UI states (loading skeletons / error banner + retry / empty copy / populated) match the UI-SPEC copywriting contract.
- 10 vitest tests green (render-count 201 control count + type fidelity for the 5 pathological keys; ui-states 3 cases).

## Task Commits

1. **Task 1: Tracer - render every control from schema.json** - (in `2a1c0e6`)
2. **Task 2: Centralize state in SettingsContext; extract Tailwind primitives** - (in `2a1c0e6`)
3. **Task 3: UI states + sidebar + group sections + fidelity tests** - (in `2a1c0e6`)

**Plan metadata:** `2a1c0e6` (feat(05-03): frontend scaffold + schema-driven renderer)

## Files Created/Modified
- `frontend/package.json` - scripts (dev/build/preview/test), Vite+React+TS+Tailwind+vitest deps
- `frontend/vite.config.ts` - react + tailwindcss plugins; /api dev proxy honoring BACKEND_PORT; vitest jsdom + setup
- `frontend/tsconfig.app.json` - resolveJsonModule + node types for test fs access
- `frontend/index.html` - app title
- `frontend/src/main.tsx` - React root mount
- `frontend/src/index.css` - Tailwind v4 import + theme
- `frontend/src/App.tsx` - read path: load -> grouped render + sidebar with scroll-sync
- `frontend/src/types.ts` - SchemaEntry/SchemaGate/SettingsType/SettingValue/Values/SchemaDocument/Preset models
- `frontend/src/lib/api.ts` - typed fetch helpers + ApiError
- `frontend/src/state/SettingsContext.tsx` - Context + useReducer (D-06)
- `frontend/src/components/ui.tsx` - Button/Card/Badge/Spinner/Skeleton/Modal primitives
- `frontend/src/components/SchemaControl.tsx` - generic type-dispatching renderer (D-03)
- `frontend/src/components/Sidebar.tsx` - tier -> group nav + buildHierarchy
- `frontend/src/components/GroupSection.tsx` - one group as a Card of controls
- `frontend/src/test/setup.ts` - jest-dom + explicit cleanup
- `frontend/src/test/render-count.test.tsx` - 201 count, type fidelity, no-key-literals gate, 5 pathological keys
- `frontend/src/test/ui-states.test.tsx` - loading/error/empty/populated

## Decisions Made
- App renders all controls simultaneously (sidebar scrolls to active section) rather than filtering to one group, so the 201 count is always verifiable in the DOM.
- One `data-key`/`data-control-type` per control (the primary input) keeps the count == key count while still letting type tests read the control.
- Vitest keeps explicit imports (per plan, no `globals`), so `@testing-library` cleanup is registered explicitly in `setup.ts`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Blocking] Only the active group rendered — count test found 0 controls**
- **Found during:** Task 1 (render-count test)
- **Issue:** The main panel filtered `GroupSection`s to the active sidebar group, so most controls were absent from the DOM and the 201-count test failed.
- **Fix:** Render all groups; the sidebar scrolls the selected section into view and stays in sync via scroll position.
- **Files modified:** frontend/src/App.tsx, GroupSection.tsx (added `id`)
- **Verification:** render-count test passes (201 controls).
- **Committed in:** 2a1c0e6

**2. [Blocking] Missing node types for test fs access**
- **Found during:** Task 1 (build)
- **Issue:** `tsconfig.app.json` restricted `types` to `["vite/client"]`, so `node:fs`/`node:path`/`node:process` imports in the schema-source test failed type-checking.
- **Fix:** Added `"node"` to the `types` array.
- **Files modified:** frontend/tsconfig.app.json
- **Verification:** `npm run build` clean.
- **Committed in:** 2a1c0e6

**3. [Blocking] Pathological-key tests over-counted slider inputs**
- **Found during:** Task 3 (fidelity tests)
- **Issue:** The int/float tests expected 2 `[data-key]` elements (range + number) but each control intentionally carries one `data-key`; also an unreachable `status === 'loading'` comparison failed type-checking.
- **Fix:** Asserted 1 element per slider; removed the dead comparison (Sidebar `loading` prop passed `false`).
- **Files modified:** render-count.test.tsx, App.tsx
- **Verification:** build + all 10 tests green.
- **Committed in:** 2a1c0e6

**4. [Blocking] @testing-library DOM leaked between tests**
- **Found during:** Task 3 (ui-states)
- **Issue:** Auto-cleanup only registers when vitest `globals` is enabled; with explicit imports the DOM persisted across tests, corrupting counts.
- **Fix:** Registered `afterEach(cleanup)` explicitly in `src/test/setup.ts`.
- **Files modified:** frontend/src/test/setup.ts
- **Verification:** ui-states tests pass.
- **Committed in:** 2a1c0e6

---

**Total deviations:** 4 auto-fixed (all blocking)
**Impact on plan:** All four necessary for a correct, buildable, testable read path. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Read path complete and green; the save (PUT) and preset-apply (POST) UI in plans 04-05 can consume `saveProjectSettings`/`applyPreset` (already stubbed in `lib/api.ts`) and the `dirty` map in `SettingsContext`.
- The `_settings_error`-mapped 400s from plan 02 surface via `ApiError.status` for save-failure UI.

---
*Phase: 05-frontend-schema-rendering*
*Completed: 2025-01-15*
