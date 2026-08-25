/** Typed fetch helpers against the backend, using a relative /api base.
 *
 * The Vite dev server proxies /api to the backend (see vite.config.ts), so the
 * browser and the tests hit the same relative paths.
 */

import type {
  ActivateFaceResponse,
  Face,
  FaceUsageProject,
  FaceUsageResponse,
  FrameAtResponse,
  FramesIndexResponse,
  GenerationStatusResponse,
  PlaybackReportRequest,
  PlaybackReportResponse,
  Preset,
  PresetApplyResponse,
  Project,
  RenderPreviewResponse,
  SchedulerStartRequest,
  SchemaDocument,
  SettingsResponse,
  Take,
  Values,
} from '../types'

/** Re-exported so face UI can import the usage row alongside the calls that
 *  produce it. */
export type { FaceUsageProject } from '../types'

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
  if (resp.status === 204) return undefined as T
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      // A structured 409 (FaceUsageConflict) carries its message inside the
      // detail object; surface that message rather than "[object Object]".
      if (body && typeof body.detail === 'string') detail = body.detail
      else if (body && typeof body.detail?.message === 'string') detail = body.detail.message
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

/** Delete a project: the row, its cached frames and its source file all go.
 *  204, so `request` resolves undefined — there is nothing to read back. */
export function deleteProject(projectId: string): Promise<void> {
  return request<void>(`/api/projects/${projectId}`, { method: 'DELETE' })
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

/** One project's public payload (projects._public): video_src/has_video/fps. */
export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`)
}

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

/** The composed recording's URL — a builder, not a fetch: both the download
 *  link and the inline player hand this to the browser, which streams the file
 *  (and its Range requests) on its own path instead of through this module. */
export function outputUrl(projectId: string): string {
  return `/api/projects/${projectId}/output`
}

/** Requeue every failed frame job. The response carries the resulting status
 *  plus how many jobs were actually reset — the count the UI reports back. */
export function retryFailed(
  projectId: string,
): Promise<GenerationStatusResponse & { requeued: number }> {
  return request<GenerationStatusResponse & { requeued: number }>(
    `/api/projects/${projectId}/generation/retry-failed`,
    { method: 'POST' },
  )
}

// --- Media endpoints (plan 05.1-06) -----------------------------------------
//
// Upload helpers send a FormData body and deliberately set NO content-type
// header: the browser writes the multipart boundary itself, and a hand-set
// header silently breaks the server's parse.

/** The upload ceiling the server enforces (backend.config max_upload_bytes).
 *  Mirrored here so an oversize pick is refused instantly instead of after a
 *  long upload the server will only reject with a 413. */
export const MAX_UPLOAD_BYTES = 8 * 1024 ** 3

/**
 * Upload a video file as the project's source. Both source endpoints answer
 * with the full refreshed `_public` project payload, so callers can adopt it
 * directly instead of refetching.
 */
export function uploadSource(projectId: string, file: File): Promise<Project> {
  const form = new FormData()
  form.append('file', file)
  return request<Project>(`/api/projects/${projectId}/source`, {
    method: 'POST',
    body: form,
  })
}

/** Point the project's video at a remote URL; returns the refreshed payload. */
export function setSourceUrl(projectId: string, url: string): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}/url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
}

/** Persist editable project fields (interval, generation mode, ...). */
export function updateProject(
  projectId: string,
  patch: {
    name?: string
    interval?: number | null
    generation_mode?: 'stream' | 'interval' | null
    full_video_mode?: boolean | null
    processing_scale?: number | null
  },
): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

/** The whole global library, newest-first — no project id in the request,
 *  which is exactly what makes the store machine-global (D-03). */
export function listFaces(): Promise<Face[]> {
  return request<Face[]>('/api/faces')
}

/** Upload an image into the global store under its content digest. */
export function uploadFace(file: File): Promise<Face> {
  const form = new FormData()
  form.append('file', file)
  return request<Face>('/api/faces', { method: 'POST', body: form })
}

/** Which projects point at this face — what the delete dialog reads first. */
export async function getFaceUsage(faceId: string): Promise<FaceUsageProject[]> {
  const resp = await request<FaceUsageResponse>(`/api/faces/${faceId}/usage`)
  return resp.projects
}

/**
 * Delete a face. Without `force` the server answers 409 with the affected
 * project list when anything still points at it; `force` performs the cascade
 * (stop runs → release rows → unlink). A plain delete carries NO query string.
 */
export function deleteFace(faceId: string, force = false): Promise<undefined> {
  const path = force ? `/api/faces/${faceId}?force=true` : `/api/faces/${faceId}`
  return request<undefined>(path, { method: 'DELETE' })
}

// --- Takes / gallery (plan 05.1-07) -----------------------------------------

/** Every exported take, newest-first as the server sorts them (D-12). */
export function listTakes(): Promise<Take[]> {
  return request<Take[]>('/api/takes')
}

/** Remove one take. The name must be one the list response supplied — the
 *  server re-validates it against its derived-name rule regardless. */
export function deleteTake(name: string): Promise<undefined> {
  return request<undefined>(`/api/takes/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

/** A take's read URL — a builder, not a fetch, so the video element's own
 *  Range requests reach the endpoint directly. */
export function takeUrl(name: string): string {
  return `/api/takes/${encodeURIComponent(name)}`
}

/** Bind a library face to the open project (D-03); assignment list per D-04. */
export function activateFace(
  projectId: string,
  faceId: string,
): Promise<ActivateFaceResponse> {
  return request<ActivateFaceResponse>(`/api/projects/${projectId}/face`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ face_id: faceId }),
  })
}

/** One-shot swapped frame at playhead `t` (D-09); never touches the scheduler. */
export function renderPreview(projectId: string, t: number): Promise<RenderPreviewResponse> {
  return request<RenderPreviewResponse>(`/api/projects/${projectId}/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ t }),
  })
}