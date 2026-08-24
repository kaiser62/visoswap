/** Typed fetch helpers against the backend, using a relative /api base.
 *
 * The Vite dev server proxies /api to the backend (see vite.config.ts), so the
 * browser and the tests hit the same relative paths.
 */

import type {
  FrameAtResponse,
  FramesIndexResponse,
  GenerationStatusResponse,
  PlaybackReportRequest,
  PlaybackReportResponse,
  Preset,
  PresetApplyResponse,
  Project,
  SchedulerStartRequest,
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

// --- Media endpoints (plan 05.1-05) -----------------------------------------

/** The frame index: metadata only, never image payloads (playback.list_frames). */
export function listFrames(projectId: string): Promise<FramesIndexResponse> {
  return request<FramesIndexResponse>(`/api/projects/${projectId}/frames`)
}

/**
 * Resolve which generated frame belongs at playback time `t`. Present for
 * completeness; the overlay deliberately uses the local index instead of this
 * endpoint — one HTTP request per displayed frame would stall the video.
 */
export function getFrameAt(projectId: string, t: number): Promise<FrameAtResponse> {
  return request<FrameAtResponse>(
    `/api/projects/${projectId}/frame-at?t=${encodeURIComponent(String(t))}`,
  )
}

/** Start the scheduler. Fails fast server-side on unusable configuration. */
export function startScheduler(
  projectId: string,
  body: SchedulerStartRequest,
): Promise<GenerationStatusResponse> {
  return request<GenerationStatusResponse>(
    `/api/projects/${projectId}/scheduler/start`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
}

/** Stop the scheduler; returns the resulting status. */
export function stopScheduler(projectId: string): Promise<GenerationStatusResponse> {
  return request<GenerationStatusResponse>(
    `/api/projects/${projectId}/scheduler/stop`,
    { method: 'POST' },
  )
}

/**
 * Report the playhead position so the scheduler can move its generation
 * window. Advisory — the backend documents that the video never waits for it.
 */
export function reportPlayback(
  projectId: string,
  body: PlaybackReportRequest,
): Promise<PlaybackReportResponse> {
  return request<PlaybackReportResponse>(`/api/projects/${projectId}/playback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Read the scheduler/generation status payload (generation._status). */
export function getGenerationStatus(
  projectId: string,
): Promise<GenerationStatusResponse> {
  return request<GenerationStatusResponse>(
    `/api/projects/${projectId}/generation/status`,
  )
}
