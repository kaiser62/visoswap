/** Types mirroring visoswap/schema/schema.json — the single source the UI renders from. */

export type SettingsType = 'toggle' | 'int' | 'float' | 'selection' | 'text'

export interface SchemaGate {
  mechanism: 'toggle' | 'selection'
  parents: string[]
  required_value: boolean | string
  rule: 'single' | 'all' | 'last'
}

export interface SchemaEntry {
  type: SettingsType
  tier: 'global' | 'project'
  group: string
  label: string
  help?: string
  level?: number
  default?: unknown
  minimum?: number
  maximum?: number
  step?: number
  decimals?: number
  options?: string[] | null
  options_from?: string | null
  gate?: SchemaGate | null
  max_length?: number
  min_length?: number
}

export type SettingValue = boolean | number | string
export type Values = Record<string, SettingValue>

export interface SchemaHeader {
  generated?: string
  licence?: string
  source?: string
  version?: string
}

export interface SchemaDocument {
  schema: SchemaHeader
  widgets: Record<string, SchemaEntry>
}

export interface Project {
  id: string
  name: string
}

export interface Preset {
  id: string
  name: string
  project: Record<string, SettingValue>
  global: Record<string, SettingValue>
  created_at: string
  updated_at: string
}

export interface SettingsResponse {
  values: Values
}

export interface PresetApplyResponse {
  report: {
    project_written: number
    project_cleared: number
    global_written: number
    global_cleared: number
    unavailable: string[]
  }
  values: Values
}

export type LoadStatus = 'loading' | 'ready' | 'error'

// ---------------------------------------------------------------------------
// Media backend (plan 05.1-05) — mirrors backend/api/playback.py,
// backend/api/generation.py and the socket protocol in backend/api/ws.py.
// ---------------------------------------------------------------------------

/** One row of GET /{id}/frames (list_frames). `url` is null unless completed. */
export interface FrameEntry {
  timestamp: number
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled'
  priority: number
  duration: number | null
  attempts: number
  error: string | null
  url: string | null
}

/** The frames-index response body. */
export interface FramesIndexResponse {
  project_id: string
  interval: number
  duration: number | null
  frames: FrameEntry[]
}

/** GET /{id}/frame-at — nearest-previous resolve; never a future frame. */
export interface FrameAtResponse {
  timestamp: number
  available: boolean
  url: string | null
}

/** Per-status queue counts, as db.counts() reports them. */
export interface GenerationCounts {
  pending: number
  processing: number
  completed: number
  failed: number
  cancelled: number
}

/** Recording availability sub-object of the generation status payload. */
export interface RecordingInfo {
  available: boolean
  complete: boolean
  bytes: number
}

/** GET /{id}/generation/status (generation._status). */
export interface GenerationStatusResponse {
  project_id: string
  running: boolean
  full_video_mode: boolean
  current_time: number
  counts: GenerationCounts
  average_duration: number | null
  recording: RecordingInfo
}

/** POST /{id}/scheduler/start body (schemas.SchedulerStart). */
export interface SchedulerStartRequest {
  full_video?: boolean | null
  current_time?: number
  range_start?: number | null
  range_duration?: number | null
}

/** POST /{id}/playback body (schemas.PlaybackUpdate). Advisory only. */
export interface PlaybackReportRequest {
  current_time: number
  seeked?: boolean
}

/** POST /{id}/playback response — shape varies by scheduler state. */
export interface PlaybackReportResponse {
  running: boolean
  current_time: number
}

/**
 * The socket event union, keyed on `type` (backend/api/ws.py hello/ping plus
 * the published shapes in services/scheduler.py and workers/generation_worker.py).
 * The discriminated union is what lets consumers switch exhaustively instead of
 * guessing at payload shapes; anything unparseable or unknown is discarded.
 */
export type SocketEvent =
  | { type: 'hello'; project_id: string; running: boolean; counts: GenerationCounts }
  | { type: 'ping' }
  | { type: 'scheduler_started' }
  | { type: 'scheduler_stopped' }
  | {
      type: 'seek_reprioritized'
      current_time: number
      window_start: number
      window_end: number
    }
  | {
      type: 'queue_updated'
      pending: number
      processing: number
      completed: number
      failed: number
      cancelled: number
    }
  | { type: 'generation_started'; timestamp: number }
  | { type: 'generation_completed'; timestamp: number; path: string; duration: number }
  | { type: 'generation_failed'; timestamp: number; error: string }
