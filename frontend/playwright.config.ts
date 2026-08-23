import { defineConfig } from '@playwright/test'

/**
 * E2E against the real backend serving the built frontend (single-container).
 * Reuses an already-running server on :8000; starts one otherwise.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: 'line',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:8000',
    headless: true,
  },
  webServer: {
    command: '..\\.venv-clean\\Scripts\\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000',
    url: 'http://127.0.0.1:8000/api/health',
    reuseExistingServer: true,
    cwd: '..',
    timeout: 120_000,
  },
})
