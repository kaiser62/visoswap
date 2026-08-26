/** Compose queue panel.
 *
 * The panel exists because a stop queues a full re-encode nobody pressed a
 * button for. So the two properties worth testing are that the work shows up
 * without anyone asking, and that it can be called off — waiting or running.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { COMPOSE_IDLE_POLL_MS, ComposeCard } from '../components/ComposeCard'
import { MediaProvider } from '../state/MediaContext'
import type { ComposeJob, GenerationStatusResponse, Project } from '../types'

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
let jobs: ComposeJob[]
let cancelled: string[]
let forgotten: string[]
let composeCalls: number
/** Set to make the next mutating call answer 409. */
let refuse: string | null
/** Set to make every job listing fail from then on. */
let failJobs = false

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

function job(over: Partial<ComposeJob> = {}): ComposeJob {
  return {
    id: 'j1',
    project_id: 'p1',
    project_name: 'Demo',
    state: 'running',
    created_at: 1,
    started_at: 2,
    finished_at: null,
    frames_written: 30,
    total_frames: 120,
    output_name: null,
    error: null,
    ...over,
  }
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url === '/api/compose/jobs') {
      if (failJobs) throw new Error('network down')
      return jsonResponse({ jobs })
    }
    if (url === '/api/projects/p1/compose') {
      composeCalls += 1
      if (refuse) return jsonResponse({ detail: refuse }, 409)
      return jsonResponse(job({ id: 'new', state: 'pending' }))
    }
    const cancel = url.match(/^\/api\/compose\/jobs\/(.+)\/cancel$/)
    if (cancel) {
      cancelled.push(cancel[1])
      return jsonResponse(job({ id: cancel[1], state: 'cancelled' }))
    }
    const forget = url.match(/^\/api\/compose\/jobs\/(.+)$/)
    if (forget && method === 'DELETE') {
      forgotten.push(forget[1])
      return { ok: true, status: 204, statusText: 'no content', json: async () => null }
    }
    if (url === '/api/projects/p1') return jsonResponse(serverProject)
    if (url === '/api/projects/p1/frames')
      return jsonResponse({ project_id: 'p1', interval: 1, duration: null, frames: [] })
    if (url === '/api/projects/p1/generation/status') return jsonResponse(statusPayload)
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function mounted() {
  const fetchMock = installFetch()
  render(
    <MediaProvider projectId="p1">
      <ComposeCard />
    </MediaProvider>,
  )
  await act(async () => {})
  await act(async () => {})
  return { fetchMock }
}

async function tick(ms = COMPOSE_IDLE_POLL_MS) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
  })
  await act(async () => {})
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  FakeSocket.instances = []
  jobs = []
  cancelled = []
  forgotten = []
  composeCalls = 0
  refuse = null
  failJobs = false
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

describe('compose card', () => {
  it('says the queue is empty and how one gets filled', async () => {
    await mounted()
    expect(screen.getByTestId('compose-empty').textContent).toMatch(/stopping a run/i)
  })

  it('picks up a job the stop queued, with nobody having pressed anything', async () => {
    // The stop happens on the backend; the panel has no other way of hearing
    // about the work it started, which is exactly why it polls when idle.
    await mounted()
    expect(screen.getByTestId('compose-empty')).toBeInTheDocument()

    jobs = [job()]
    await tick()
    await waitFor(() => expect(screen.getByTestId('compose-job-j1')).toBeInTheDocument())
    expect(composeCalls).toBe(0)
  })

  it('reports progress of the job that is running', async () => {
    jobs = [job({ frames_written: 30, total_frames: 120 })]
    await mounted()
    expect(screen.getByTestId('compose-job-j1').textContent).toContain('25%')
    expect(screen.getByTestId('compose-job-j1').textContent).toContain('30 / 120')
  })

  it('cancels a running job', async () => {
    jobs = [job()]
    await mounted()
    fireEvent.click(screen.getByTestId('compose-cancel'))
    await waitFor(() => expect(cancelled).toEqual(['j1']))
  })

  it('cancels a job that is still only waiting its turn', async () => {
    // The queue is serial, so a job can sit behind another project's for a long
    // time. Calling it off before it starts is the cheapest moment to do so.
    jobs = [job({ id: 'j2', state: 'pending', frames_written: 0, total_frames: 0 })]
    await mounted()
    fireEvent.click(screen.getByTestId('compose-cancel'))
    await waitFor(() => expect(cancelled).toEqual(['j2']))
  })

  it('links the take a finished job produced', async () => {
    jobs = [job({ state: 'done', output_name: 'Demo_20260101-000000_ada_composed.mp4' })]
    await mounted()
    const link = screen.getByTestId('compose-take')
    expect(link.getAttribute('href')).toBe(
      '/api/takes/Demo_20260101-000000_ada_composed.mp4',
    )
  })

  it('offers Clear rather than Cancel once a job is finished', async () => {
    jobs = [job({ state: 'done', output_name: 'take.mp4' })]
    await mounted()
    expect(screen.queryByTestId('compose-cancel')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('compose-clear'))
    await waitFor(() => expect(forgotten).toEqual(['j1']))
  })

  it('shows why a job failed', async () => {
    jobs = [job({ state: 'failed', error: 'ffmpeg produced no output' })]
    await mounted()
    expect(screen.getByTestId('compose-job-j1').textContent).toContain(
      'ffmpeg produced no output',
    )
  })

  it('calls a run that generated nothing nothing-to-compose, not failed', async () => {
    jobs = [job({ state: 'empty', total_frames: 0, frames_written: 0 })]
    await mounted()
    expect(screen.getByTestId('compose-job-j1').textContent).toMatch(/nothing to compose/i)
  })

  it('refuses to compose on demand while a run is going', async () => {
    statusPayload = { ...statusPayload, running: true }
    await mounted()
    expect(screen.getByTestId('compose-now')).toBeDisabled()
  })

  it('composes on demand when nothing is running', async () => {
    await mounted()
    fireEvent.click(screen.getByTestId('compose-now'))
    await waitFor(() => expect(composeCalls).toBe(1))
  })

  it('surfaces a refusal instead of swallowing it', async () => {
    refuse = 'stop the run before composing it'
    await mounted()
    fireEvent.click(screen.getByTestId('compose-now'))
    await waitFor(() =>
      expect(screen.getByTestId('compose-error').textContent).toContain(
        'stop the run before composing it',
      ),
    )
  })

  it('keeps the rows on screen when a poll fails', async () => {
    // A list that momentarily fails to load has not become empty; blanking it
    // would say the queue had drained.
    jobs = [job()]
    await mounted()
    expect(screen.getByTestId('compose-job-j1')).toBeInTheDocument()

    failJobs = true
    await tick()
    expect(screen.getByTestId('compose-job-j1')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('compose-error')).toBeInTheDocument())
  })
})
