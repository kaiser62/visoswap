/** Typed fetch helpers against the backend, using a relative /api base.
 *
 * The Vite dev server proxies /api to the backend (see vite.config.ts), so the
 * browser and the tests hit the same relative paths.
 */

import type {
  Preset,
  PresetApplyResponse,
  Project,
  SchemaDocument,
  SettingsResponse,
  Values,
} from '../types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init)
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(resp.status, detail)
  }
  return (await resp.json()) as T
}

export function getSchema(): Promise<SchemaDocument> {
  return request<SchemaDocument>('/api/schema')
}

export function getProjectSettings(projectId: string): Promise<SettingsResponse> {
  return request<SettingsResponse>(`/api/projects/${projectId}/settings`)
}

export function listProjects(): Promise<Project[]> {
  return request<Project[]>('/api/projects')
}

export function createProject(name?: string): Promise<Project> {
  return request<Project>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name ?? 'Untitled project' }),
  })
}

export function getPresets(): Promise<{ presets: Preset[] }> {
  return request<{ presets: Preset[] }>('/api/presets')
}

export function saveProjectSettings(
  projectId: string,
  overrides: Values,
): Promise<SettingsResponse> {
  return request<SettingsResponse>(`/api/projects/${projectId}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ overrides }),
  })
}

export function applyPreset(
  projectId: string,
  presetId: string,
): Promise<PresetApplyResponse> {
  return request<PresetApplyResponse>(
    `/api/projects/${projectId}/presets/${presetId}`,
    { method: 'POST' },
  )
}
