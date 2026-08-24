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
  type ReactNode,
} from 'react'
import {
  getGenerationStatus,
  getProject,
  listFrames,
  startScheduler as postSchedulerStart,
  stopScheduler as postSchedulerStop,
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

export const initialMediaState: MediaState = {
  status: 'loading',
  projectId: null,
  project: null,
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
      return { ...state, project: action.project }
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
    default:
      return state
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

  const startRun = useCallback(async () => {
    if (!projectId) return
    let body: SchedulerStartRequest
    if (state.mode === 'export') {
      if (state.range.end !== null) {
        const duration = state.range.end - (state.range.start ?? 0)
        if (duration <= 0) return // inverted ranges are refused at the UI layer
        body = {
          full_video: false,
          current_time: 0,
          range_start: state.range.start ?? 0,
          range_duration: duration,
        }
      } else {
        body = { full_video: true, current_time: 0 }
      }
    } else {
      body = { full_video: false, current_time: 0 }
    }
    try {
      await postSchedulerStart(projectId, body)
    } catch (err) {
      dispatch({ type: 'MEDIA_ERROR', message: `Starting the run failed: ${describeError(err)}` })
      return
    }
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

  const setStartMark = useCallback((t: number) => {
    dispatch({ type: 'RANGE_MARK', which: 'start', t })
  }, [])

  const setEndMark = useCallback((t: number) => {
    dispatch({ type: 'RANGE_MARK', which: 'end', t })
  }, [])

  const clearRange = useCallback(() => {
    dispatch({ type: 'RANGE_CLEARED' })
  }, [])

  const selectMode = useCallback((mode: MediaMode) => {
    dispatch({ type: 'MODE_SELECTED', mode })
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
    }),
    [state, startRun, stopRun, refreshStatus, setStartMark, setEndMark, clearRange, selectMode],
  )

  return <MediaContext.Provider value={value}>{children}</MediaContext.Provider>
}

export function useMedia(): MediaContextValue {
  const ctx = useContext(MediaContext)
  if (!ctx) throw new Error('useMedia must be used within MediaProvider')
  return ctx
}
