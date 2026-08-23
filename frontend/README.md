# Frontend

Vite + React + TypeScript settings UI for VisoSwap. Renders all 201 controls from
`visoswap/schema/schema.json` through one generic schema-driven renderer
(`src/components/SchemaControl.tsx`) that dispatches exclusively on each entry's
`type` field — never on key names.

## Prerequisites

- Node.js ≥ 20 with npm
- The Python backend (see below) — the Phase 4 **model bootstrap gate** applies:
  the backend refuses to start unless the complete owned model set is reachable
  (default `model_assets_owned/`, or `MODELS_DIR`). A server that starts has
  passed the gate.

## Run

**1. Backend first** (repo root):

```
.venv-clean\Scripts\python.exe -m uvicorn backend.main:app
```

Port defaults to 8000; override with `BACKEND_PORT`. The `/api` proxy in
`vite.config.ts` targets `http://127.0.0.1:${BACKEND_PORT || 8000}`.

**2a. Vite dev server** (this directory):

```
npm install   # once
npm run dev   # http://localhost:5173, FRONTEND_PORT overrides
```

**2b. Single-container mode:** `npm run build`, then just run the backend — it
serves `frontend/dist` at `/` when the directory exists.

**Open a project:** the app reads `?project=<id>` from the URL. Create one via
`POST /api/projects`, or paste a video URL as `http://127.0.0.1:8000/url=<URL>`
— the backend redirects to `/?project=<id>` after binding.

## Test

```
npm test            # vitest run (render-count / ui-states / save-flow / preset-flow / gates)
npm run build       # tsc -b && vite build — the FRONTEND-01 type-error gate
npm run test:e2e    # Playwright: real Chromium against the backend on :8000
```

Playwright reuses an already-running backend on :8000 (serving `frontend/dist`)
or starts one itself; point `E2E_BASE_URL` elsewhere to target another instance.
First run needs `npx playwright install chromium`.

Backend settings API suite (repo root):

```
.venv-clean\Scripts\python.exe -m pytest tests/test_api_settings.py tests/test_api_settings_write.py
```

Backend tests require `MODELS_VERIFY_MODE=fast` (or a complete model set — the
default `model_assets_owned` copy qualifies).

## Offline notes

- System font stack only (`ui-sans-serif, system-ui, …`) — no webfonts are
  fetched at runtime.
- Dependencies install once via `npm install`; nothing else is fetched at
  runtime. All API calls are relative `/api` paths against the local backend.
