import { defineConfig } from '@playwright/test'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * E2E against the real backend serving the built frontend (single-container).
 * Reuses an already-running server on :8000; starts one otherwise.
 */

/** The repository root, resolved from this file rather than from the process's
 *  working directory: `cwd` and the interpreter path have to agree, and a
 *  relative pair silently disagreed. `cwd: '..'` put the child in the repo root
 *  while the command still said `..\.venv-clean\...`, so the second `..` climbed
 *  one level too far and the spawn failed with a path that exists nowhere. */
const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const PYTHON = resolve(REPO_ROOT, '.venv-clean', 'Scripts', 'python.exe')

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
    command: `"${PYTHON}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`,
    url: 'http://127.0.0.1:8000/api/health',
    reuseExistingServer: true,
    cwd: REPO_ROOT,
    timeout: 120_000,
  },
})
