# Phase 5: Frontend Schema Rendering - Context

**Gathered:** 2026-08-23
**Status:** Ready for planning

<domain>
## Phase Boundary

A Vite + React + TypeScript web frontend that renders **all 201 settings controls** from `schema.json` (never hand-listed in frontend source), applies the **2 migrated presets** (`A`, `with AUD`), and round-trips changed values through the project/face tier persisted by the backend. Because no settings API exists today, **this phase also adds the backend endpoints** the frontend calls (schema, settings per project, presets). The result must satisfy the four FRONTEND-01 success criteria: rendered-control count == schema key count, preset selection updates visible values, reload restores a changed value, and `npm run build` passes with no type errors.

**Depends on:** Phase 3 (schema + seeded presets), Phase 4 (backend + project-tier persistence). Phase 4 is unverified as of planning — the plan must not assume its internals; it should build the settings API on the Phase 3 `visoswap.settings` store (the authoritative three-tier source).

</domain>

<decisions>
## Implementation Decisions

### Layout & Navigation
- **D-01:** Organize the 201 controls into **groups with a sidebar navigation**. Controls are grouped by schema tier and logical group, with a category sidebar; this scales to 201 controls. — **Reversibility:** reversible

### Save Model
- **D-02:** Use **dirty-tracking, saving only changed values**. Only controls the user actually changed POST/PUT to the project tier, matching the backend's override-only design (writes only diffs). — **Reversibility:** reversible

### Control Mapping
- **D-03:** Use a **generic schema-driven renderer**: one component reads the schema entry's `type` (toggle→switch, int→slider/number, float→slider, selection→dropdown, text→input) and renders the matching control. **It must never infer type from key-name substrings** (`Toggle`/`Selection`/`Decimal`) — this is a hard FRONTEND-01 criterion. — **Reversibility:** reversible

### Preset UX
- **D-04:** Present presets as a **header selector**; applying one updates visible control values and shows a diff/confirmation before persisting. — **Reversibility:** reversible

### API Contract (built by this phase)
- **D-05:** Add REST endpoints the frontend calls: `GET /api/schema`, `GET/PUT /api/projects/{id}/settings`, `GET /api/presets`, `POST /api/projects/{id}/presets/{preset}`. These follow the existing FastAPI router patterns (`backend/api/*.py`). — **Reversibility:** costly — new public API surface other callers may depend on

### State Management
- **D-06:** Use **React Context + useReducer** for frontend state — lightweight, fits a settings-heavy single view with dirty tracking. — **Reversibility:** reversible

### Styling
- **D-07:** Use **Tailwind CSS** with a small set of primitives; matches a fresh Vite app with no heavy component-library dependency. — **Reversibility:** reversible

### the agent's Discretion
- Component/file structure inside `frontend/` (Vite scaffold layout), exact group labels for the sidebar, the diff/confirmation modal styling, and data-fetching layer (e.g. `fetch` vs a thin client wrapper). These are implementation details the planner may decide.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Schema & settings (Phase 3 — the authoritative source)
- `visoswap/schema/__init__.py` — the frozen schema loader; `WIDGETS` (201 typed entries), `entry()`, `type_of()`, `default_of()`, `keys_in_tier()`, `resolved_entry()`. Types: `toggle/int/float/selection/text`.
- `visoswap/schema/schema.json` — the 201 typed entries the UI renders from.
- `visoswap/settings/store.py` — three-tier resolution (face→project→global→default); `resolve`, `resolve_parameters`, `resolve_control`, `set_global/set_project/set_face`, `clear_*`. The API in D-05 should wrap this.
- `visoswap/settings/db.py` — `SETTINGS_SCHEMA` DDL and `apply_settings_schema(connection)`.
- `visoswap/settings/presets.py` — `seed_presets`, `list_presets`, `get_preset`, `apply_preset`; the two migrated presets.
- `visoswap/settings/data/presets_seed.json` — the 2 committed presets (`A`, `with AUD`).

### Backend patterns (Phase 4 code — exists, unverified)
- `backend/api/projects.py` — existing FastAPI router patterns (APIRouter, Depends(get_db)/get_project, pydantic schemas).
- `backend/api/schemas.py` — request/response pydantic models; new settings/preset schemas should follow this style.
- `backend/api/deps.py` — `get_db`, `get_project` dependencies.
- `backend/models/database.py` — the Database wrapper.
- `backend/main.py` — how routers are wired (`include_router`), and how the frontend dist is served (`FRONTEND_DIST`, Vite dev server on :5173).

### Requirements
- `.planning/REQUIREMENTS.md` § FRONTEND-01 — the phase requirement and its 4 success criteria.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `visoswap/schema/__init__.py` — `WIDGETS`, `type_of`, `default_of`, `keys_in_tier`, `resolved_entry` give the frontend everything needed to render and validate controls with zero manual mapping.
- `visoswap/settings/presets.py` — `list_presets`/`get_preset`/`apply_preset` provide the preset backend behavior.
- `backend/api/*` FastAPI router + dependency pattern — reuse for the new settings/preset endpoints.

### Established Patterns
- Backend routers use `APIRouter(prefix=...)` + `Depends(get_db)`/`Depends(get_project)` + pydantic `schemas.py`. The new API in D-05 must match.
- The override-only settings design means the API only persists diffs — the frontend dirty-tracking (D-02) aligns with it.
- The engine/settings are deliberately Qt-free and stdlib-only; the frontend is a separate web layer and may use React freely.

### Integration Points
- New API routers mount in `backend/main.py` via `app.include_router(...)`.
- The frontend builds to `frontend/dist`, served by `backend/main.py` at `/` (with Vite dev server on :5173 during development).
- The frontend's data layer calls the D-05 REST endpoints; project id comes from the project context (see `backend/api/projects.py`).

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond the decisions above — open to standard approaches for the exact component layout, group labels, and styling primitives (planner discretion).

</specifics>

<deferred>
## Deferred Ideas

- None — discussion stayed within phase scope.

</deferred>

---

*Phase: 5-Frontend Schema Rendering*
*Context gathered: 2026-08-23*
