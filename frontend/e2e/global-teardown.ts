import { request } from '@playwright/test'

/** Delete the projects an E2E run created.
 *
 * Runs after the whole suite rather than after each test: a failing test that
 * leaves its project behind is exactly the case worth cleaning up, and a
 * per-test hook does not fire when the run is interrupted. 77 abandoned
 * projects had accumulated before this existed.
 *
 * Only names beginning with `E2E_PREFIX` are touched. Anything a human made is
 * off limits, so the match is a prefix and not a heuristic — including
 * `Untitled project`, which the New-project button creates and which a person
 * is just as likely to be sitting on.
 */

export const E2E_PREFIX = 'e2e-'

export default async function globalTeardown() {
  const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000'
  const api = await request.newContext({ baseURL })
  try {
    const resp = await api.get('/api/projects')
    if (!resp.ok()) return
    const projects = (await resp.json()) as { id: string; name: string }[]
    for (const p of projects) {
      if (!p.name?.startsWith(E2E_PREFIX)) continue
      // One failure must not strand the rest — the server may already have
      // dropped the row, and the point of this pass is the other twenty.
      await api.delete(`/api/projects/${p.id}`).catch(() => {})
    }
  } catch {
    // The backend being gone by teardown time is not a test failure.
  } finally {
    await api.dispose()
  }
}
