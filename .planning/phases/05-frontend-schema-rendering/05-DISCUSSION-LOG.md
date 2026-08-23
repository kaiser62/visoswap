# Phase 5: Frontend Schema Rendering - Discussion Log

**Gathered:** 2026-08-23
**Mode:** default (interactive)

## Domain
Vite + React + TS frontend rendering all 201 settings controls from `schema.json`, applying the 2 presets, and round-tripping changes through a persisted project tier. This phase also adds the settings/schema/preset API the frontend depends on.

## Questions & Decisions

### Area: Layout & Navigation
- **Q:** How should the 201 settings controls be organized?
- **Options:** Grouped + sidebar / Single long page / Search-first list
- **Selected:** Grouped + sidebar (Recommended) → **D-01**

### Area: Save Model
- **Q:** How should settings changes be persisted?
- **Options:** Dirty-tracking, save only changes / Auto-save on change / Single Save button (all)
- **Selected:** Dirty-tracking, save only changes (Recommended) → **D-02**

### Area: Control Mapping
- **Q:** How should schema types map to rendered controls?
- **Options:** Generic schema-driven renderer / Bespoke per-control components
- **Selected:** Generic schema-driven renderer (Recommended) → **D-03**

### Area: Preset UX
- **Q:** How should preset selection/application be presented?
- **Options:** Header selector + apply / Settings-panel dropdown
- **Selected:** Header selector + apply (Recommended) → **D-04**

### Area: API Contract
- **Q:** What API surface should this phase add for settings/schema/presets?
- **Options:** REST + schema.json served / Static schema, minimal new endpoints
- **Selected:** REST + schema.json served (Recommended) → **D-05**

### Area: State Management
- **Q:** How should frontend state be managed?
- **Options:** React Context + useReducer / Zustand / Redux Toolkit
- **Selected:** React Context + useReducer (Recommended) → **D-06**

### Area: Styling
- **Q:** What styling approach should the frontend use?
- **Options:** Tailwind CSS / CSS Modules / shadcn/ui
- **Selected:** Tailwind CSS (Recommended) → **D-07**

## Deferred Ideas
- None.
