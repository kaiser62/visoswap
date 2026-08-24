/** Media state container tests (plan 05.1-05 Task 2).
 *
 * One reducer owns the run, the index and the socket; the socket is
 * informational only — a reconnect heals by refetching the index wholesale,
 * never by trusting replayed events (backend/api/ws.py contract).
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  initialMediaState,
  mediaReducer,
  MediaProvider,
  useMedia,
} from '../state/MediaContext'
import { RECONNECT_BASE_MS } from '../lib/ws'
import type { FrameEntry, GenerationStatusResponse, Project } from '../types'

// --- harness -----------------------------------------------------------------

class FakeSocket {
  static instances: FakeSocket[] = []
  url: string
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }
  serverOpen() {
    this.readyState = 1
    this.onopen?.()
  }
  serverMessage(data: unknown) {
    this.onmessage?.({ data })
  }
  serverClose() {
    this.readyState = 3
    this.onclose?.()
  }
  close() {
    this.readyState = 3
  }
  send() {}
}

function frame(timestamp: number, url: string | null, status = 'completed'): FrameEntry {
  return {
    timestamp,
    status: status as FrameEntry['status'],
    priority: 1,
    duration: null,
    attempts: 0,
    error: null,
    url,
  }
}

function statusPayload(over: Partial<GenerationStatusResponse> = {}): GenerationStatusResponse {
  return {
    project_id: 't',
    running: false,
    full_video_mode: false,
    current_time: 0,
    counts: { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 },
    average_duration: null,
    recording: { available: false, complete: false, bytes: 0 },
    ...over,
  }
}

const projectPayload: Project & Record<string, unknown> = {
  id: 't',
  name: 'Test',
  has_video: true,
  video_src: '/api/projects/t/video',
  effective_interval: 1 / 24,
  fps: 24,
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'ok',
    json: async () => body,
  }
}

/** Mutable server-side state the stub serves from. */
const server = {
  frames: [frame(1, '/api/projects/t/frame/000001.000'), frame(2, '/api/projects/t/frame/000002.000')],
  framesStatus: 200,
  startBodies: [] as unknown[],
  stopCalls: 0,
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/projects/t/frames') {
      if (server.framesStatus !== 200) {
        return jsonResponse({ detail: 'backend exploded' }, server.framesStatus)
      }
      return jsonResponse({
        project_id: 't',
        interval: 1 / 24,
        duration: 60,
        frames: server.frames,
      })
    }
    if (url === '/api/projects/t/generation/status') {
      return jsonResponse(statusPayload())
    }
    if (url === '/api/projects/t') {
      return jsonResponse(projectPayload)
    }
    if (init?.method === 'POST' && url === '/api/projects/t/scheduler/start') {
      server.startBodies.push(JSON.parse(String(init.body)))
      return jsonResponse(statusPayload({ running: true }))
    }
    if (init?.method === 'POST' && url === '/api/projects/t/scheduler/stop') {
      server.stopCalls += 1
      return jsonResponse(statusPayload({ running: false }))
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const callsTo = (mock: ReturnType<typeof installFetch>, suffix: string) =>
  mock.mock.calls.filter((c) => String(c[0]).endsWith(suffix)).length

function Probe({ onReady }: { onReady?: () => void }) {
  const media = useMedia()
  onReady?.()
  return (
    <div>
      <div data-testid="status">{media.status}</div>
      <div data-testid="project-id">{media.projectId ?? 'none'}</div>
      <div data-testid="index-length">{media.index.length}</div>
      <div data-testid="last-url">{media.index[media.index.length - 1]?.url ?? '-'}</div>
      <div data-testid="running">{String(media.running)}</div>
      <div data-testid="counts">{JSON.stringify(media.counts)}</div>
      <div data-testid="failed">{String(media.failedCount)}</div>
      <div data-testid="socket">{media.socketState}</div>
      <div data-testid="range">{JSON.stringify(media.range)}</div>
      <div data-testid="mode">{media.mode}</div>
      <div data-testid="error">{media.error ?? '-'}</div>
      <button data-testid="btn-start" onClick={() => void media.startRun()} />
      <button data-testid="btn-stop" onClick={() => void media.stopRun()} />
      <button data-testid="btn-mode-export" onClick={() => media.selectMode('export')} />
      <button data-testid="btn-set-start" onClick={() => media.setStartMark(10)} />
      <button data-testid="btn-set-end" onClick={() => media.setEndMark(20)} />
      <button data-testid="btn-clear-range" onClick={() => media.clearRange()} />
    </div>
  )
}

async function bootstrapped(fetchMock: ReturnType<typeof installFetch>) {
  render(
    <MediaProvider projectId="t">
      <Probe />
    </MediaProvider>,
  )
  // Flush the bootstrap fetches, then the socket-open refetch chain.
  await act(async () => {})
  await act(async () => {})
  await act(async () => {})
  expect(screen.getByTestId('project-id').textContent).toBe('t')
  expect(callsTo(fetchMock, '/frames')).toBeGreaterThan(0)
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  server.frames = [frame(1, '/api/projects/t/frame/000001.000'), frame(2, '/api/projects/t/frame/000002.000')]
  server.framesStatus = 200
  server.startBodies = []
  server.stopCalls = 0
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('media context', () => {
  it('mounts with no project and does nothing until a project id is supplied', async () => {
    const fetchMock = installFetch()
    const { rerender } = render(
      <MediaProvider projectId={null}>
        <Probe />
      </MediaProvider>,
    )
    await act(async () => {})
    expect(fetchMock).not.toHaveBeenCalled()
    expect(FakeSocket.instances.length).toBe(0)
    expect(screen.getByTestId('project-id').textContent).toBe('none')

    rerender(
      <MediaProvider projectId="t">
        <Probe />
      </MediaProvider>,
    )
    await act(async () => {})
    await act(async () => {})
    await waitFor(() => {
      expect(screen.getByTestId('project-id').textContent).toBe('t')
    })
    expect(fetchMock).toHaveBeenCalled()
  })

  it('given a project id it loads once: one bootstrap pass per effect, one socket', async () => {
    const fetchMock = installFetch()
    await bootstrapped(fetchMock)
    // Bootstrap effect ran exactly once: status + project each fetched once.
    expect(callsTo(fetchMock, '/generation/status')).toBe(1)
    expect(callsTo(fetchMock, '/api/projects/t')).toBe(1)
    expect(FakeSocket.instances.length).toBe(1)
    // The index was fetched on bootstrap AND on the socket-open refetch —
    // refetch-on-open is mandated by backend/api/ws.py's informational contract.
    expect(callsTo(fetchMock, '/frames')).toBe(2)
    expect(screen.getByTestId('index-length').textContent).toBe('2')
  })

  it('a generation-completed event inserts that frame into the index carrying the event path', async () => {
    const fetchMock = installFetch()
    await bootstrapped(fetchMock)
    expect(screen.getByTestId('index-length').textContent).toBe('2')
    act(() => {
      FakeSocket.instances[0].serverMessage(
        JSON.stringify({
          type: 'generation_completed',
          timestamp: 5,
          path: '/api/projects/t/frame/000005.000',
          duration: 0.1,
        }),
      )
    })
    expect(screen.getByTestId('index-length').textContent).toBe('3')
    expect(screen.getByTestId('last-url').textContent).toBe('/api/projects/t/frame/000005.000')
  })

  it('scheduler-started sets running true and scheduler-stopped sets it false', async () => {
    const fetchMock = installFetch()
    await bootstrapped(fetchMock)
    expect(screen.getByTestId('running').textContent).toBe('false')
    act(() => {
      FakeSocket.instances[0].serverMessage(JSON.stringify({ type: 'scheduler_started' }))
    })
    expect(screen.getByTestId('running').textContent).toBe('true')
    act(() => {
      FakeSocket.instances[0].serverMessage(JSON.stringify({ type: 'scheduler_stopped' }))
    })
    expect(screen.getByTestId('running').textContent).toBe('false')
  })

  it('a queue-updated event replaces the stored counts', async () => {
    const fetchMock = installFetch()
    await bootstrapped(fetchMock)
    act(() => {
      FakeSocket.instances[0].serverMessage(
        JSON.stringify({
          type: 'queue_updated',
          pending: 7,
          processing: 2,
          completed: 11,
          failed: 1,
          cancelled: 3,
        }),
      )
    })
    const counts = JSON.parse(screen.getByTestId('counts').textContent ?? '{}')
    expect(counts).toEqual({ pending: 7, processing: 2, completed: 11, failed: 1, cancelled: 3 })
  })

  it('a generation-failed event increments the failed tally without touching the index', async () => {
    const fetchMock = installFetch()
    await bootstrapped(fetchMock)
    expect(screen.getByTestId('failed').textContent).toBe('0')
    act(() => {
      FakeSocket.instances[0].serverMessage(
        JSON.stringify({ type: 'generation_failed', timestamp: 9, error: 'boom' }),
      )
    })
    act(() => {
      FakeSocket.instances[0].serverMessage(
        JSON.stringify({ type: 'generation_failed', timestamp: 10, error: 'boom again' }),
      )
    })
    expect(screen.getByTestId('failed').textContent).toBe('2')
    expect(screen.getByTestId('index-length').textContent).toBe('2')
  })

  it('a reconnect refetches the index and replaces it wholesale rather than merging', async () => {
    vi.useFakeTimers()
    const fetchMock = installFetch()
    render(
      <MediaProvider projectId="t">
        <Probe />
      </MediaProvider>,
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByTestId('index-length').textContent).toBe('2')

    // Drop the socket; while disconnected the run's frames are wiped and only
    // one new-run frame exists. The refetched index must reflect exactly that.
    act(() => {
      FakeSocket.instances[0].serverClose()
    })
    server.frames = [frame(0.5, '/api/projects/t/frame/000000.500')]
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECONNECT_BASE_MS)
    })
    expect(FakeSocket.instances.length).toBe(2)
    act(() => {
      FakeSocket.instances[1].serverOpen()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByTestId('index-length').textContent).toBe('1')
    expect(screen.getByTestId('last-url').textContent).toBe('/api/projects/t/frame/000000.500')
  })

  it('start posts a scheduler body assembled from mode and range, then refreshes status', async () => {
    const fetchMock = installFetch()
    await bootstrapped(fetchMock)
    const statusBefore = callsTo(fetchMock, '/generation/status')

    fireEvent.click(screen.getByTestId('btn-mode-export'))
    fireEvent.click(screen.getByTestId('btn-set-start'))
    fireEvent.click(screen.getByTestId('btn-set-end'))
    expect(JSON.parse(screen.getByTestId('range').textContent ?? '{}')).toEqual({
      start: 10,
      end: 20,
    })
    fireEvent.click(screen.getByTestId('btn-start'))

    await waitFor(() => {
      expect(server.startBodies.length).toBe(1)
    })
    expect(server.startBodies[0]).toEqual({
      full_video: false,
      current_time: 0,
      range_start: 10,
      range_duration: 10,
    })
    await waitFor(() => {
      expect(callsTo(fetchMock, '/generation/status')).toBeGreaterThan(statusBefore)
    })
  })

  it('stop posts to scheduler/stop and refreshes status', async () => {
    const fetchMock = installFetch()
    await bootstrapped(fetchMock)
    const statusBefore = callsTo(fetchMock, '/generation/status')
    fireEvent.click(screen.getByTestId('btn-stop'))
    await waitFor(() => {
      expect(server.stopCalls).toBe(1)
    })
    await waitFor(() => {
      expect(callsTo(fetchMock, '/generation/status')).toBeGreaterThan(statusBefore)
    })
  })

  it('a failed index fetch leaves an error state naming the failure without unmounting children', async () => {
    installFetch()
    server.framesStatus = 500
    render(
      <MediaProvider projectId="t">
        <Probe />
      </MediaProvider>,
    )
    await act(async () => {})
    await act(async () => {})
    await waitFor(() => {
      expect(screen.getByTestId('error').textContent).toContain('backend exploded')
    })
    // Children still mounted and rendering through the provider.
    expect(screen.getByTestId('status')).toBeTruthy()
    expect(screen.getByTestId('project-id').textContent).toBe('t')
  })

  it('the exported reducer is pure: equal inputs yield equal plain objects', () => {
    const initial = mediaReducer(initialMediaState, { type: 'PROJECT_SET', projectId: 't' })
    const action = { type: 'INDEX_UPSERTED', entry: { timestamp: 4, url: 'u4' } } as const
    const a = mediaReducer(initial, action)
    const b = mediaReducer(initial, action)
    expect(a).toEqual(b)
    expect(a).not.toBe(b) // new object, same value — no shared mutation
    // Reducer output carries no functions or thunks.
    for (const value of Object.values(a)) {
      expect(typeof value).not.toBe('function')
    }
  })
})
