/** Results card tests (plan 05.1-07 Task 2).
 *
 * The card exists to stop one specific misreport: calling a partial recording
 * finished. A run can stop before the recorder promotes its `.part`, so the
 * label must follow the backend's `complete` flag and nothing else.
 */

import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ResultsCard } from '../components/ResultsCard'
import { MediaProvider } from '../state/MediaContext'
import type { GenerationStatusResponse, Project, RecordingInfo } from '../types'

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

let serverProject: Project
let statusPayload: GenerationStatusResponse
/** Set to reject the next status poll only. */
let failNextStatus = false

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/projects/p1') return jsonResponse(serverProject)
    if (url === '/api/projects/p1/frames')
      return jsonResponse({ project_id: 'p1', interval: 1, duration: null, frames: [] })
    if (url === '/api/projects/p1/generation/status') {
      if (failNextStatus) {
        failNextStatus = false
        throw new Error('network down')
      }
      return jsonResponse(statusPayload)
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const statusCalls = (mock: ReturnType<typeof installFetch>) =>
  mock.mock.calls.filter(([u]) => String(u).includes('/generation/status')).length

function recording(over: Partial<RecordingInfo>): RecordingInfo {
  return { available: true, complete: false, bytes: 1024, ...over }
}

async function mounted() {
  const fetchMock = installFetch()
  render(
    <MediaProvider projectId="p1">
      <ResultsCard />
    </MediaProvider>,
  )
  await act(async () => {})
  await act(async () => {})
  return { fetchMock }
}

/** Advance past one poll interval and let the resulting state land. */
async function tick(ms = 2000) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
  })
  await act(async () => {})
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  FakeSocket.instances = []
  failNextStatus = false
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
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('results card', () => {
  it('shows an empty state and no download control when nothing was recorded', async () => {
    await mounted()
    expect(screen.getByTestId('results-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('results-download')).not.toBeInTheDocument()
  })

  it('labels a growing recording as in progress and shows its size', async () => {
    statusPayload = {
      ...statusPayload,
      running: true,
      recording: recording({ bytes: 2_097_152 }),
    }
    await mounted()
    expect(screen.getByTestId('results-label').textContent).toMatch(/in progress/i)
    expect(screen.getByTestId('results-size').textContent).toContain('2')
  })

  it('offers a download that targets the output endpoint mid-run', async () => {
    statusPayload = { ...statusPayload, running: true, recording: recording({}) }
    await mounted()
    const link = screen.getByTestId('results-download')
    expect(link.getAttribute('href')).toBe('/api/projects/p1/output')
  })

  it('changes the label from partial to finished on the completion flag, with no reload', async () => {
    statusPayload = { ...statusPayload, running: true, recording: recording({}) }
    await mounted()
    expect(screen.getByTestId('results-label').textContent).toMatch(/in progress/i)

    statusPayload = {
      ...statusPayload,
      running: false,
      recording: recording({ complete: true }),
    }
    await tick()
    await waitFor(() =>
      expect(screen.getByTestId('results-label').textContent).toMatch(/finished|complete/i),
    )
  })

  it('updates the reported size while a run is active', async () => {
    statusPayload = {
      ...statusPayload,
      running: true,
      recording: recording({ bytes: 1_048_576 }),
    }
    await mounted()
    expect(screen.getByTestId('results-size').textContent).toContain('1')

    statusPayload = { ...statusPayload, recording: recording({ bytes: 5_242_880 }) }
    await tick()
    await waitFor(() => expect(screen.getByTestId('results-size').textContent).toContain('5'))
  })

  it('polls only while a run is active', async () => {
    const { fetchMock } = await mounted()
    const idle = statusCalls(fetchMock)
    await tick(10_000)
    expect(statusCalls(fetchMock)).toBe(idle)
  })

  it('keeps the last known values and marks itself stale when a poll fails', async () => {
    statusPayload = {
      ...statusPayload,
      running: true,
      recording: recording({ bytes: 3_145_728 }),
    }
    await mounted()
    expect(screen.getByTestId('results-size').textContent).toContain('3')

    failNextStatus = true
    await tick()
    expect(screen.getByTestId('results-size').textContent).toContain('3')
    await waitFor(() => expect(screen.getByTestId('results-stale')).toBeInTheDocument())
  })

  it('plays the current recording inline from the same endpoint', async () => {
    statusPayload = { ...statusPayload, running: true, recording: recording({}) }
    await mounted()
    const player = screen.getByTestId('results-video')
    expect(player.getAttribute('src')).toBe('/api/projects/p1/output')
  })
})
