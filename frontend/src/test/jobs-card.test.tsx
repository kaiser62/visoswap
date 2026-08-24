/** Jobs card tests (plan 05.1-07 Task 1, D-10/D-15d).
 *
 * The coverage figure must come from a measurement the player actually takes,
 * so these tests drive the real PlayerCard's timeupdate handler over a real
 * frame index rather than dispatching samples by hand. Queue counts arrive
 * through the fake socket — the card must never need a poll for them.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobsCard } from '../components/JobsCard'
import { PlayerCard } from '../components/PlayerCard'
import { MediaProvider } from '../state/MediaContext'
import type { GenerationStatusResponse, Project, SocketEvent } from '../types'

class FakeSocket {
  static instances: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(_url: string) {
    FakeSocket.instances.push(this)
  }
  close() {}
  send() {}
}

/** Push a server event at the mounted provider's socket. */
async function serverEvent(event: SocketEvent) {
  await act(async () => {
    FakeSocket.instances[0]?.onmessage?.({ data: JSON.stringify(event) })
  })
}

let serverProject: Project
let statusPayload: GenerationStatusResponse
let framesPayload: { project_id: string; interval: number; duration: number | null; frames: unknown[] }
let retryResponse: { status: number; body?: unknown }

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/projects/p1') return jsonResponse(serverProject)
    if (url === '/api/projects/p1/frames') return jsonResponse(framesPayload)
    if (url === '/api/projects/p1/generation/status') return jsonResponse(statusPayload)
    if (url === '/api/projects/p1/generation/retry-failed' && init?.method === 'POST') {
      if (retryResponse.status >= 300) return jsonResponse(retryResponse.body, retryResponse.status)
      return jsonResponse(retryResponse.body)
    }
    if (url === '/api/projects/p1/scheduler/start' && init?.method === 'POST') {
      statusPayload = { ...statusPayload, running: true }
      return jsonResponse(statusPayload)
    }
    if (url === '/api/projects/p1/playback' && init?.method === 'POST')
      return jsonResponse({ ok: true })
    if (url === '/api/faces') return jsonResponse([])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const statusCalls = (mock: ReturnType<typeof installFetch>) =>
  mock.mock.calls.filter(([u]) => String(u).includes('/generation/status')).length

/** jsdom has no playback: currentTime and paused are plain properties and
 *  timeupdate is dispatched by hand, exactly as plan 05's overlay tests do. */
function drivable(video: HTMLVideoElement) {
  let t = 0
  Object.defineProperty(video, 'currentTime', {
    configurable: true,
    get: () => t,
    set: (v: number) => {
      t = v
    },
  })
  Object.defineProperty(video, 'paused', { configurable: true, value: false })
}

async function mounted() {
  const fetchMock = installFetch()
  render(
    <MediaProvider projectId="p1">
      <PlayerCard />
      <JobsCard />
    </MediaProvider>,
  )
  await act(async () => {})
  await act(async () => {})
  FakeSocket.instances[0]?.onopen?.()
  await act(async () => {})
  const video = screen.queryByTestId('player-video') as HTMLVideoElement | null
  if (video) drivable(video)
  return { fetchMock, video }
}

/** Move the playhead and fire one timeupdate — one overlay lookup, one sample. */
async function lookupAt(video: HTMLVideoElement, t: number) {
  await act(async () => {
    video.currentTime = t
    fireEvent(video, new Event('timeupdate'))
  })
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  serverProject = {
    id: 'p1',
    name: 'Test',
    has_video: true,
    video_src: '/v',
    effective_interval: 1,
    fps: 24,
  }
  statusPayload = {
    project_id: 'p1',
    running: false,
    full_video_mode: false,
    current_time: 0,
    counts: { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 },
    average_duration: null,
    recording: { available: false, complete: false, bytes: 0 },
  }
  // Frames exist at 10s and 12s only: a lookup below 10 is a miss, at or above
  // one of them is a hit (nearest previous, never a future frame).
  framesPayload = {
    project_id: 'p1',
    interval: 1,
    duration: null,
    frames: [
      { timestamp: 10, status: 'completed', url: '/f/10.jpg' },
      { timestamp: 12, status: 'completed', url: '/f/12.jpg' },
    ],
  }
  retryResponse = { status: 200, body: { requeued: 3, ...statusPayload } }
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('jobs card — cadence honesty (D-15d)', () => {
  it('shows an explicit not-yet-measured state with no percentage before any lookup', async () => {
    await mounted()
    const coverage = screen.getByTestId('jobs-coverage')
    expect(coverage.textContent).not.toContain('%')
    expect(coverage.textContent).toMatch(/not yet measured/i)
  })

  it('reports the exact coverage percentage and the raw counts it measured', async () => {
    const { video } = await mounted()
    expect(video).not.toBeNull()
    // Two misses (before the first frame), then three hits.
    await lookupAt(video!, 1)
    await lookupAt(video!, 5)
    await lookupAt(video!, 10)
    await lookupAt(video!, 11)
    await lookupAt(video!, 12)
    await waitFor(() => expect(screen.getByTestId('jobs-coverage').textContent).toContain('60%'))
    const counts = screen.getByTestId('jobs-coverage-counts').textContent ?? ''
    expect(counts).toContain('3')
    expect(counts).toContain('5')
  })

  it('resets the tally when a run starts, so a ratio describes one run', async () => {
    const { video } = await mounted()
    await lookupAt(video!, 1)
    await lookupAt(video!, 10)
    await waitFor(() => expect(screen.getByTestId('jobs-coverage').textContent).toContain('50%'))

    fireEvent.click(screen.getByTestId('btn-start-run'))
    await waitFor(() =>
      expect(screen.getByTestId('jobs-coverage').textContent).toMatch(/not yet measured/i),
    )
    expect(screen.getByTestId('jobs-coverage').textContent).not.toContain('%')
  })

  it('states the generation rate from the reported average duration beside the video frame rate', async () => {
    statusPayload = { ...statusPayload, average_duration: 0.1 }
    await mounted()
    // 0.1 s per frame is 10 generated frames per second, against a 24 fps video.
    expect(screen.getByTestId('jobs-rate').textContent).toContain('10')
    expect(screen.getByTestId('jobs-fps').textContent).toContain('24')
  })

  it('says the rate is unreported rather than dividing by nothing', async () => {
    await mounted()
    expect(screen.getByTestId('jobs-rate').textContent).toMatch(/not yet reported/i)
  })

  it('explains the nearest-previous overlay policy in one line', async () => {
    await mounted()
    expect(screen.getByTestId('jobs-policy').textContent).toMatch(/nearest previous/i)
    expect(screen.getByTestId('jobs-policy').textContent).toMatch(/never .*future/i)
  })
})

describe('jobs card — queue and in-flight (D-10)', () => {
  it('updates the counts from socket events without issuing a poll', async () => {
    const { fetchMock } = await mounted()
    const before = statusCalls(fetchMock)
    await serverEvent({
      type: 'queue_updated',
      pending: 7,
      processing: 2,
      completed: 5,
      failed: 1,
      cancelled: 0,
    })
    expect(screen.getByTestId('jobs-count-pending').textContent).toContain('7')
    expect(screen.getByTestId('jobs-count-processing').textContent).toContain('2')
    expect(screen.getByTestId('jobs-count-completed').textContent).toContain('5')
    expect(screen.getByTestId('jobs-count-failed').textContent).toContain('1')
    expect(screen.getByTestId('jobs-count-cancelled').textContent).toContain('0')
    expect(statusCalls(fetchMock)).toBe(before)
  })

  it('lists the frames currently being generated, newest first', async () => {
    await mounted()
    await serverEvent({ type: 'generation_started', timestamp: 4 })
    await serverEvent({ type: 'generation_started', timestamp: 9 })
    const items = screen.getAllByTestId('jobs-inflight-item')
    expect(items.length).toBe(2)
    expect(items[0].textContent).toContain('9')
    expect(items[1].textContent).toContain('4')

    await serverEvent({ type: 'generation_completed', timestamp: 9, path: '/f/9.jpg', duration: 0.1 })
    await waitFor(() => expect(screen.getAllByTestId('jobs-inflight-item').length).toBe(1))
  })

  it('offers a retry only when something failed, and reports how many were requeued', async () => {
    await mounted()
    expect(screen.queryByTestId('jobs-retry')).not.toBeInTheDocument()

    await serverEvent({
      type: 'queue_updated',
      pending: 0,
      processing: 0,
      completed: 4,
      failed: 3,
      cancelled: 0,
    })
    fireEvent.click(screen.getByTestId('jobs-retry'))
    await waitFor(() => expect(screen.getByTestId('jobs-retry-result').textContent).toContain('3'))
  })

  it('shows an idle empty state with no run controls when no run has ever started', async () => {
    await mounted()
    expect(screen.getByTestId('jobs-idle')).toBeInTheDocument()
    expect(screen.queryByTestId('jobs-retry')).not.toBeInTheDocument()
    expect(screen.queryByTestId('jobs-inflight-item')).not.toBeInTheDocument()
  })
})
