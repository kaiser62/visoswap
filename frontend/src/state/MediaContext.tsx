/** React Context + useReducer media state container (plan 05.1-05 Task 2).
 *
 * Modeled structurally on SettingsContext.tsx: a state interface, a
 * discriminated action union, a pure exported reducer, a provider holding
 * `useReducer` with `useCallback` thunks, a memoized context value, and a hook
 * that throws when used outside the provider. The reducer stays pure — the
 * socket subscription and every network call live in the provider's effects
 * and callbacks.
 *
 * One container owns the run (scheduler state), the frame index, and the
 * socket. The socket is informational only: its refetch callback dispatches an
 * INDEX_LOADED, which REPLACES the index wholesale — on reconnect the refetched
 * index is authoritative over any replayed events (backend/api/ws.py contract;
 * the hub drops the oldest event for a saturated subscriber).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from 'react'
import {
  activateFace,
  getGenerationStatus,
  getProject,
  listFrames,
  startScheduler as postSchedulerStart,
  stopScheduler as postSchedulerStop,
  updateProject,
} from '../lib/api'
import { buildIndex, type FrameIndexEntry } from '../lib/frameindex'
import { openProjectSocket } from '../lib/ws'
import type {
  GenerationCounts,
  GenerationStatusResponse,
  LoadStatus,
  Project,
  RecordingInfo,
  SchedulerStartRequest,
  SocketEvent,
} from '../types'

/** User-facing generation mode (D-07). `export` uses the range marks when an
 * end mark exists, otherwise the full video. */
export type MediaMode = 'live' | 'interval' | 'export'

export interface RangeMarks {
  start: number | null
  end: number | null
}

export interface MediaState {
  status: LoadStatus
  projectId: string | null
  project: Project | null
  sourceFaceId: string | null
  index: FrameIndexEntry[]
  running: boolean
  fullVideoMode: boolean
  counts: GenerationCounts
  averageDuration: number | null
  recording: RecordingInfo | null
  socketState: 'closed' | 'open'
  failedCount: number
  range: RangeMarks
  mode: MediaMode
  error: string | null
  /** The last rendered preview frame's url (D-09); null when none is shown. */
  previewUrl: string | null
  /** Automatic preview toggle — off by default (D-09). */
  automaticPreview: boolean
  /** The video element's paused state, reported by the player card. Auto-
   *  preview gates on this at fire time, not only at subscribe time. */
  videoPaused: boolean
}

export type MediaAction =
  | { type: 'PROJECT_SET'; projectId: string }
  | { type: 'PROJECT_LOADED'; project: Project }
  | { type: 'INDEX_LOADED'; entries: FrameIndexEntry[] }
  | { type: 'INDEX_UPSERTED'; entry: FrameIndexEntry }
  | {
      type: 'STATUS_LOADED'
      status: GenerationStatusResponse
    }
  | { type: 'RUNNING_SET'; running: boolean }
  | { type: 'COUNTS_UPDATED'; counts: GenerationCounts }
  | { type: 'GENERATION_FAILED' }
  | { type: 'SOCKET_STATE'; open: boolean }
  | { type: 'RANGE_MARK'; which: 'start' | 'end'; t: number }
  | { type: 'RANGE_CLEARED' }
  | { type: 'MODE_SELECTED'; mode: MediaMode }
  | { type: 'MEDIA_ERROR'; message: string }
  | { type: 'SOURCE_FACE_SET'; sourceFaceId: string }
  | { type: 'PREVIEW_SET'; url: string }
  | { type: 'PREVIEW_CLEAR' }
  | { type: 'AUTO_PREVIEW_TOGGLE' }
  | { type: 'VIDEO_PAUSED'; paused: boolean }

export const initialMediaState: MediaState = {
  status: 'loading',
  projectId: null,
  project: null,
  sourceFaceId: null,
  index: [],
  running: false,
  fullVideoMode: false,
  counts: { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 },
  averageDuration: null,
  recording: null,
  socketState: 'closed',
  failedCount: 0,
  range: { start: null, end: null },
  mode: 'live',
  error: null,
  previewUrl: null,
  automaticPreview: false,
  videoPaused: true,
}

function normalizeCounts(counts: Partial<GenerationCounts> | undefined): GenerationCounts {
  return {
    pending: counts?.pending ?? 0,
    processing: counts?.processing ?? 0,
    completed: counts?.completed ?? 0,
    failed: counts?.failed ?? 0,
    cancelled: counts?.cancelled ?? 0,
  }
}

/** Pure reducer — exported for direct-call purity tests; contains no I/O. */
export function mediaReducer(state: MediaState, action: MediaAction): MediaState {
  switch (action.type) {
    case 'PROJECT_SET':
      // A new project starts from a clean slate except user preferences that
      // are project-independent (mode choice survives; marks do not).
      return {
        ...initialMediaState,
        mode: state.mode,
        status: 'loading',
        projectId: action.projectId,
      }
    case 'PROJECT_LOADED':
      return { ...state, project: action.project, sourceFaceId: action.project.source_face_id ?? null }
    case 'INDEX_LOADED':
      return { ...state, index: action.entries, status: 'ready' }
    case 'INDEX_UPSERTED': {
      const next = [...state.index]
      const at = next.findIndex((e) => e.timestamp === action.entry.timestamp)
      if (at >= 0) next[at] = action.entry
      else {
        next.push(action.entry)
        next.sort((a, b) => a.timestamp - b.timestamp)
      }
      return { ...state, index: next }
    }
    case 'STATUS_LOADED':
      return {
        ...state,
        running: action.status.running,
        fullVideoMode: action.status.full_video_mode,
        counts: normalizeCounts(action.status.counts),
        averageDuration: action.status.average_duration,
        recording: action.status.recording,
      }
    case 'RUNNING_SET':
      return { ...state, running: action.running }
    case 'COUNTS_UPDATED':
      return { ...state, counts: normalizeCounts(action.counts) }
    case 'GENERATION_FAILED':
      return { ...state, failedCount: state.failedCount + 1 }
    case 'SOCKET_STATE':
      return { ...state, socketState: action.open ? 'open' : 'closed' }
    case 'RANGE_MARK':
      return action.which === 'start'
        ? { ...state, range: { ...state.range, start: action.t } }
        : { ...state, range: { ...state.range, end: action.t } }
    case 'RANGE_CLEARED':
      return { ...state, range: { start: null, end: null } }
    case 'MODE_SELECTED':
      return { ...state, mode: action.mode }
    case 'MEDIA_ERROR':
      return { ...state, status: 'error', error: action.message }
    case 'SOURCE_FACE_SET':
      return { ...state, sourceFaceId: action.sourceFaceId }
    case 'PREVIEW_SET':
      return { ...state, previewUrl: action.url }
    case 'PREVIEW_CLEAR':
      return { ...state, previewUrl: null }
    case 'AUTO_PREVIEW_TOGGLE':
      return { ...state, automaticPreview: !state.automaticPreview }
    case 'VIDEO_PAUSED':
      return { ...state, videoPaused: action.paused }
  }
}

interface MediaContextValue extends MediaState {
  startRun: () => Promise<void>
  stopRun: () => Promise<void>
  refreshStatus: () => Promise<void>
  setStartMark: (t: number) => void
  setEndMark: (t: number) => void
  clearRange: () => void
  selectMode: (mode: MediaMode) => void
  selectFace: (faceId: string) => Promise<void>
  updateInterval: (seconds: number) => Promise<void>
  /** Refetch the project payload and adopt it — the one refresh path every
   *  media mutation shares, so player, card and library read one truth. */
  refreshProject: () => Promise<void>
  /** Record the playhead so start/preview bodies carry the real position
   *  without re-rendering on every timeupdate. */
  reportPlayhead: (t: number) => void
  /** Read the last reported playhead (0 before the first report). */
  getPlayhead: () => number
  setPreviewUrl: (url: string) => void
  clearPreview: () => void
  toggleAutoPreview: () => void
  setVideoPaused: (paused: boolean) => void
}

const MediaContext = createContext<MediaContextValue | null>(null)

function describeError(err: unknown): string {
  if (err instanceof Error && err.message) return err.message
  return String(err)
}

export function MediaProvider({
  projectId,
  children,
}: {
  projectId: string | null
  children: ReactNode
}) {
  const [state, dispatch] = useReducer(mediaReducer, initialMediaState)

  // The playhead lives in a ref: it changes sixty times a second while
  // playing, and no render should depend on it — only the start/preview body
  // assembly reads it, at click time.
  const playheadRef = useRef(0)

  // Bootstrap: one pass per project — frame index, generation status, and the
  // project payload (video_src/fps feed the player card).
  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    dispatch({ type: 'PROJECT_SET', projectId })
    void (async () => {
      try {
        const [framesResp, status, project] = await Promise.all([
          listFrames(projectId),
          getGenerationStatus(projectId),
          getProject(projectId),
        ])
        if (cancelled) return
        dispatch({ type: 'PROJECT_LOADED', project })
        dispatch({ type: 'STATUS_LOADED', status })
        dispatch({ type: 'INDEX_LOADED', entries: buildIndex(framesResp) })
      } catch (err) {
        if (!cancelled) {
          dispatch({
            type: 'MEDIA_ERROR',
            message: `Media bootstrap failed: ${describeError(err)}`,
          })
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [projectId])

  // The socket: opened once per project, closed by the effect cleanup. Its
  // refetch callback replaces the index wholesale — reconnect heals by
  // refetching, never by trusting replayed events.
  useEffect(() => {
    if (!projectId) return
    const reloadIndex = () => {
      void listFrames(projectId)
        .then((resp) => dispatch({ type: 'INDEX_LOADED', entries: buildIndex(resp) }))
        .catch(() => {
          /* keep the last known index; the next event or reconnect retries */
        })
    }
    const onEvent = (event: SocketEvent) => {
      switch (event.type) {
        case 'hello':
          dispatch({ type: 'SOCKET_STATE', open: true })
          dispatch({ type: 'RUNNING_SET', running: event.running })
          dispatch({ type: 'COUNTS_UPDATED', counts: event.counts })
          break
        case 'ping':
          break
        case 'scheduler_started':
          dispatch({ type: 'RUNNING_SET', running: true })
          break
        case 'scheduler_stopped':
          dispatch({ type: 'RUNNING_SET', running: false })
          break
        case 'seek_reprioritized':
          break
        case 'queue_updated':
          dispatch({
            type: 'COUNTS_UPDATED',
            counts: normalizeCounts(event as Partial<GenerationCounts>),
          })
          break
        case 'generation_started':
          break
        case 'generation_completed':
          dispatch({
            type: 'INDEX_UPSERTED',
            entry: { timestamp: event.timestamp, url: event.path },
          })
          break
        case 'generation_failed':
          dispatch({ type: 'GENERATION_FAILED' })
          break
      }
    }
    const handle = openProjectSocket(projectId, onEvent, reloadIndex)
    return () => handle.close()
  }, [projectId])

  const refreshStatus = useCallback(async () => {
    if (!projectId) return
    try {
      const status = await getGenerationStatus(projectId)
      dispatch({ type: 'STATUS_LOADED', status })
    } catch {
      /* advisory read; the next event or manual refresh corrects it */
    }
  }, [projectId])

  const refreshProject = useCallback(async () => {
    if (!projectId) return
    try {
      const project = await getProject(projectId)
      dispatch({ type: 'PROJECT_LOADED', project })
    } catch (err) {
      dispatch({
        type: 'MEDIA_ERROR',
        message: `Refreshing the project failed: ${describeError(err)}`,
      })
    }
  }, [projectId])

  const startRun = useCallback(async () => {
    if (!projectId) return
    // The mode→flags mapping lives here, in one place (D-07/D-08): streaming
    // and interval follow the playhead; export covers the marked span, or the
    // whole video when no marks exist. One mark alone is refused before any
    // request — a half-marked range is a user mistake, not a run shape.
    let body: SchedulerStartRequest
    if (state.mode === 'export') {
      const { start, end } = state.range
      if (start === null && end === null) {
        body = { full_video: true, current_time: playheadRef.current }
      } else if (start !== null && end !== null) {
        const duration = end - start
        if (duration <= 0) {
          dispatch({ type: 'MEDIA_ERROR', message: 'End mark must come after the start mark.' })
          return
        }
        // The marks are sent alongside the same explicit flags every start body
        // carries, so the shape plan 05 pinned holds for every mode.
        body = {
          full_video: false,
          current_time: playheadRef.current,
          range_start: start,
          range_duration: duration,
        }
      } else {
        dispatch({
          type: 'MEDIA_ERROR',
          message:
            start === null
              ? 'Export needs a start mark — set one from the transport.'
              : 'Export needs an end mark — set one from the transport.',
        })
        return
      }
    } else {
      body = { current_time: playheadRef.current }
    }
    try {
      await postSchedulerStart(projectId, body)
    } catch (err) {
      dispatch({ type: 'MEDIA_ERROR', message: `Starting the run failed: ${describeError(err)}` })
      return
    }
    dispatch({ type: 'PREVIEW_CLEAR' })
    await refreshStatus()
  }, [projectId, state.mode, state.range, refreshStatus])

  const stopRun = useCallback(async () => {
    if (!projectId) return
    try {
      await postSchedulerStop(projectId)
    } catch (err) {
      dispatch({ type: 'MEDIA_ERROR', message: `Stopping the run failed: ${describeError(err)}` })
      return
    }
    await refreshStatus()
  }, [projectId, refreshStatus])

  /**
   * Selecting a mode is a UI choice that also persists the row's generation
   * grid: live maps to `stream`, interval to `interval`. Export changes no
   * row field — it is a span choice resolved at start time from the marks.
   */
  const selectMode = useCallback(
    (mode: MediaMode) => {
      dispatch({ type: 'MODE_SELECTED', mode })
      if (!projectId || mode === 'export') return
      const generation_mode = mode === 'live' ? 'stream' : 'interval'
      void updateProject(projectId, { generation_mode })
        .then((project) => dispatch({ type: 'PROJECT_LOADED', project }))
        .catch(() => {
          /* persistence is best-effort; the mode still governs this session */
        })
    },
    [projectId],
  )

  /** The interval N persists through the project update endpoint because the
   *  scheduler reads it from the row, never from the start body. */
  const updateInterval = useCallback(
    async (seconds: number) => {
      if (!projectId || !Number.isFinite(seconds) || seconds <= 0) return
      try {
        const project = await updateProject(projectId, { interval: seconds })
        dispatch({ type: 'PROJECT_LOADED', project })
      } catch (err) {
        dispatch({
          type: 'MEDIA_ERROR',
          message: `Saving the interval failed: ${describeError(err)}`,
        })
      }
    },
    [projectId],
  )

  /** Activate a library face for the open project, then adopt the refreshed
   *  payload so the strip's active mark follows the row's truth (D-03). */
  const selectFace = useCallback(
    async (faceId: string) => {
      if (!projectId) return
      try {
        await activateFace(projectId, faceId)
        dispatch({ type: 'SOURCE_FACE_SET', sourceFaceId: faceId })
        await refreshProject()
      } catch (err) {
        dispatch({
          type: 'MEDIA_ERROR',
          message: `Activating the face failed: ${describeError(err)}`,
        })
      }
    },
    [projectId, refreshProject],
  )

  const setStartMark = useCallback((t: number) => {
    dispatch({ type: 'RANGE_MARK', which: 'start', t })
  }, [])

  const setEndMark = useCallback((t: number) => {
    dispatch({ type: 'RANGE_MARK', which: 'end', t })
  }, [])

  const clearRange = useCallback(() => {
    dispatch({ type: 'RANGE_CLEARED' })
  }, [])

  const reportPlayhead = useCallback((t: number) => {
    playheadRef.current = t
  }, [])

  const getPlayhead = useCallback(() => playheadRef.current, [])

  const setPreviewUrl = useCallback((url: string) => {
    dispatch({ type: 'PREVIEW_SET', url })
  }, [])

  const clearPreview = useCallback(() => {
    dispatch({ type: 'PREVIEW_CLEAR' })
  }, [])

  const toggleAutoPreview = useCallback(() => {
    dispatch({ type: 'AUTO_PREVIEW_TOGGLE' })
  }, [])

  const setVideoPaused = useCallback((paused: boolean) => {
    dispatch({ type: 'VIDEO_PAUSED', paused })
  }, [])

  const value = useMemo<MediaContextValue>(
    () => ({
      ...state,
      startRun,
      stopRun,
      refreshStatus,
      setStartMark,
      setEndMark,
      clearRange,
      selectMode,
      selectFace,
      updateInterval,
      refreshProject,
      reportPlayhead,
      getPlayhead,
      setPreviewUrl,
      clearPreview,
      toggleAutoPreview,
      setVideoPaused,
    }),
    [
      state,
      startRun,
      stopRun,
      refreshStatus,
      setStartMark,
      setEndMark,
      clearRange,
      selectMode,
      selectFace,
      updateInterval,
      refreshProject,
      reportPlayhead,
      getPlayhead,
      setPreviewUrl,
      clearPreview,
      toggleAutoPreview,
      setVideoPaused,
    ],
  )

  return <MediaContext.Provider value={value}>{children}</MediaContext.Provider>
}

export function useMedia(): MediaContextValue {
  const ctx = useContext(MediaContext)
  if (!ctx) throw new Error('useMedia must be used within MediaProvider')
  return ctx
}
