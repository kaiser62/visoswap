/** Player card overlay tests (plan 05.1-05 Task 3).
 *
 * D-06's invariant is the whole point of this file: the overlay swaps a
 * positioned image as generated frames arrive, and NOTHING about it can make
 * the video wait — no per-frame HTTP request, no await in the timeupdate
 * handler, no visible failure from an advisory position report.
 *
 * jsdom implements no media playback, so the element is driven directly:
 * `currentTime` and `paused` are installed as plain configurable properties and
 * the events are dispatched by hand.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobsCard } from '../components/JobsCard'
import { PlayerCard } from '../components/PlayerCard'
import { MediaProvider } from '../state/MediaContext'
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
  close() {
    this.readyState = 3
  }
  send() {}
}

function frame(timestamp: number, url: string): FrameEntry {
  return {
    timestamp,
    status: 'completed',
    priority: 1,
    duration: null,
    attempts: 0,
    error: null,
    url,
  }
}

const statusPayload: GenerationStatusResponse = {
  project_id: 't',
  running: false,
  full_video_mode: false,
  current_time: 0,
  counts: { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 },
  average_duration: null,
  recording: { available: false, complete: false, bytes: 0 },
}

const projectPayload: Project = {
  id: 't',
  name: 'Test',
  has_video: true,
  video_src: '/api/projects/t/video',
  effective_interval: 1,
  fps: 24,
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

const server = {
  frames: [frame(1, '/f/1.png'), frame(2, '/f/2.png'), frame(4, '/f/4.png')],
  playbackBodies: [] as unknown[],
  playbackRejects: false,
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/projects/t/frames') {
      return jsonResponse({ project_id: 't', interval: 1, duration: 60, frames: server.frames })
    }
    if (url === '/api/projects/t/generation/status') return jsonResponse(statusPayload)
    if (url === '/api/projects/t') return jsonResponse(projectPayload)
    if (url === '/api/projects/t/playback') {
      server.playbackBodies.push(JSON.parse(String(init?.body)))
      if (server.playbackRejects) return jsonResponse({ detail: 'nope' }, 500)
      return jsonResponse({ ok: true })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const callsTo = (mock: ReturnType<typeof installFetch>, needle: string) =>
  mock.mock.calls.filter((c) => String(c[0]).includes(needle)).length

/** Install `currentTime`/`paused` as plain properties — jsdom has no playback. */
function drivable(video: HTMLVideoElement, opts: { paused?: boolean } = {}) {
  Object.defineProperty(video, 'currentTime', { value: 0, writable: true, configurable: true })
  Object.defineProperty(video, 'paused', {
    value: opts.paused ?? false,
    writable: true,
    configurable: true,
  })
  return video
}

function tick(video: HTMLVideoElement, t: number) {
  ;(video as unknown as { currentTime: number }).currentTime = t
  fireEvent(video, new Event('timeupdate'))
}

async function mounted(fetchMock: ReturnType<typeof installFetch>) {
  render(
    <MediaProvider projectId="t">
      <PlayerCard />
    </MediaProvider>,
  )
  await act(async () => {})
  await act(async () => {})
  act(() => {
    FakeSocket.instances[0]?.serverOpen()
  })
  await act(async () => {})
  expect(callsTo(fetchMock, '/frames')).toBeGreaterThan(0)
  const video = screen.getByTestId('player-video') as HTMLVideoElement
  return drivable(video)
}

const overlay = () => screen.queryByTestId('overlay-image') as HTMLImageElement | null

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  server.frames = [frame(1, '/f/1.png'), frame(2, '/f/2.png'), frame(4, '/f/4.png')]
  server.playbackBodies = []
  server.playbackRejects = false
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('player card overlay', () => {
  it('renders a video element sourced from the project video url', async () => {
    const video = await mounted(installFetch())
    expect(video.getAttribute('src')).toBe('/api/projects/t/video')
  })

  it('swaps on every composited video frame, not on the 4Hz timeupdate', async () => {
    // `timeupdate` fires ~4 times a second. Driving a 30fps picture from it
    // makes the swapped layer beat against the video under it, which is the
    // whole of the reported choppiness. rVFC is the per-frame signal, and this
    // pins that the overlay follows it with no `timeupdate` at all.
    const pending: ((now: number, meta: { mediaTime: number }) => void)[] = []
    const proto = HTMLVideoElement.prototype as unknown as Record<string, unknown>
    proto.requestVideoFrameCallback = function (
      cb: (now: number, meta: { mediaTime: number }) => void,
    ) {
      pending.push(cb)
      return pending.length
    }
    proto.cancelVideoFrameCallback = function () {}
    // Detached prefetch images, captured rather than loaded — jsdom fetches no
    // resources, so the url handed to the browser is the whole observable.
    const warmed: string[] = []
    vi.stubGlobal(
      'Image',
      class {
        set src(url: string) {
          warmed.push(url)
        }
        decode() {
          return Promise.resolve()
        }
      },
    )
    try {
      const fetchMock = installFetch()
      await mounted(fetchMock)
      const paint = (mediaTime: number) => {
        const cb = pending.pop()
        pending.length = 0
        act(() => cb?.(0, { mediaTime }))
      }

      paint(1.5)
      expect(overlay()?.getAttribute('src')).toBe('/f/1.png')
      paint(2.4)
      expect(overlay()?.getAttribute('src')).toBe('/f/2.png')

      // The advisory position post stays on the coarse path: a frame-rate
      // callback must not turn into a frame-rate request.
      expect(callsTo(fetchMock, '/playback')).toBe(0)

      // The frames ahead of the playhead were warmed, so the next swap is a
      // cache hit rather than a decode. Only the ones ahead: the displayed
      // frame is already on screen.
      expect(warmed.sort()).toEqual(['/f/2.png', '/f/4.png'])
    } finally {
      delete proto.requestVideoFrameCallback
      delete proto.cancelVideoFrameCallback
    }
  })

  it('swaps the overlay image to a frame once the playhead passes its timestamp', async () => {
    const video = await mounted(installFetch())
    act(() => tick(video, 1.5))
    expect(overlay()?.getAttribute('src')).toBe('/f/1.png')
    act(() => tick(video, 2.4))
    expect(overlay()?.getAttribute('src')).toBe('/f/2.png')
  })

  it('shows no overlay while no completed frame is at or before the playhead', async () => {
    const video = await mounted(installFetch())
    act(() => tick(video, 0.5))
    expect(overlay()).toBeNull()
  })

  it('never advances to a frame whose timestamp is ahead of the playhead', async () => {
    const video = await mounted(installFetch())
    // 3s sits between the 2s and 4s frames: the nearest PREVIOUS one wins.
    act(() => tick(video, 3))
    expect(overlay()?.getAttribute('src')).toBe('/f/2.png')
  })

  it('produces zero frame-at requests across sixty timeupdate events', async () => {
    const fetchMock = installFetch()
    const video = await mounted(fetchMock)
    act(() => {
      for (let i = 0; i < 60; i += 1) tick(video, 1 + i * 0.05)
    })
    expect(callsTo(fetchMock, '/frame-at')).toBe(0)
  })

  it('leaves the video unpaused with its clock untouched when the overlay image errors', async () => {
    const video = await mounted(installFetch())
    act(() => tick(video, 1.5))
    const img = overlay()
    expect(img).not.toBeNull()
    const pausedBefore = video.paused
    const timeBefore = video.currentTime
    act(() => {
      fireEvent.error(img as HTMLImageElement)
    })
    expect(video.paused).toBe(pausedBefore)
    expect(video.currentTime).toBe(timeBefore)
    expect(overlay()).toBeNull()
  })

  it('reports position at most once per throttle window, and immediately on seek', async () => {
    const fetchMock = installFetch()
    const video = await mounted(fetchMock)
    await act(async () => {
      for (let i = 0; i < 60; i += 1) tick(video, 1 + i * 0.05)
    })
    expect(callsTo(fetchMock, '/playback')).toBe(1)
    expect((server.playbackBodies[0] as { seeked: boolean }).seeked).toBe(false)

    await act(async () => {
      ;(video as unknown as { currentTime: number }).currentTime = 9
      fireEvent(video, new Event('seeked'))
    })
    expect(callsTo(fetchMock, '/playback')).toBe(2)
    expect(server.playbackBodies[1]).toEqual({ current_time: 9, seeked: true })
  })

  it('swallows a rejected position report: no alert surfaces and playback continues', async () => {
    server.playbackRejects = true
    const fetchMock = installFetch()
    const video = await mounted(fetchMock)
    await act(async () => {
      tick(video, 1.5)
    })
    expect(callsTo(fetchMock, '/playback')).toBe(1)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(video.paused).toBe(false)
    expect(overlay()?.getAttribute('src')).toBe('/f/1.png')
  })

  it('records the start and end marks from the current time and clears both', async () => {
    const video = await mounted(installFetch())
    act(() => tick(video, 1.5))
    fireEvent.click(screen.getByTestId('btn-set-start'))
    expect(screen.getByTestId('mark-start').textContent).toBe('1.500')
    act(() => tick(video, 4.25))
    fireEvent.click(screen.getByTestId('btn-set-end'))
    expect(screen.getByTestId('mark-end').textContent).toBe('4.250')
    fireEvent.click(screen.getByTestId('btn-clear-range'))
    expect(screen.getByTestId('mark-start').textContent).toBe('—')
    expect(screen.getByTestId('mark-end').textContent).toBe('—')
  })

  it('refuses an end mark before the start with a visible message, marks unchanged', async () => {
    const video = await mounted(installFetch())
    act(() => tick(video, 4))
    fireEvent.click(screen.getByTestId('btn-set-start'))
    act(() => tick(video, 1))
    fireEvent.click(screen.getByTestId('btn-set-end'))
    expect(screen.getByRole('alert').textContent).toMatch(/end/i)
    expect(screen.getByTestId('mark-start').textContent).toBe('4.000')
    expect(screen.getByTestId('mark-end').textContent).toBe('—')
  })

  it('hides and restores the layer on demand without disturbing the video', async () => {
    const video = await mounted(installFetch())
    act(() => tick(video, 1.5))
    expect(overlay()?.getAttribute('src')).toBe('/f/1.png')

    const toggle = screen.getByTestId('btn-toggle-overlay')
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(toggle)

    // The layer is gone -- the source video underneath it is not.
    expect(overlay()).toBeNull()
    expect(toggle.getAttribute('aria-pressed')).toBe('false')
    expect(video.paused).toBe(false)
    expect(video.currentTime).toBe(1.5)

    // The lookup kept running while hidden, so showing again needs no seek.
    act(() => tick(video, 2.4))
    expect(overlay()).toBeNull()
    fireEvent.click(toggle)
    expect(overlay()?.getAttribute('src')).toBe('/f/2.png')
  })

  it('keeps measuring coverage while the layer is hidden', async () => {
    const fetchMock = installFetch()
    render(
      <MediaProvider projectId="t">
        <PlayerCard />
        <JobsCard />
      </MediaProvider>,
    )
    await act(async () => {})
    await act(async () => {})
    act(() => {
      FakeSocket.instances[0]?.serverOpen()
    })
    await act(async () => {})
    expect(callsTo(fetchMock, '/frames')).toBeGreaterThan(0)
    const video = drivable(screen.getByTestId('player-video') as HTMLVideoElement)

    fireEvent.click(screen.getByTestId('btn-toggle-overlay'))
    // Two lookups that both land on a frame: hiding the layer is a display
    // choice, and a coverage figure that collapsed to 0% here would report a
    // healthy run as a broken one.
    await act(async () => {
      tick(video, 1.5)
      tick(video, 2.4)
    })
    await waitFor(() =>
      expect(screen.getByTestId('jobs-coverage').textContent).toContain('100%'),
    )
    expect(overlay()).toBeNull()
  })

  it('seeks by time and by frame number using the project frame rate', async () => {
    const video = await mounted(installFetch())
    const seconds = screen.getByTestId('seek-seconds')
    fireEvent.change(seconds, { target: { value: '3.5' } })
    fireEvent.keyDown(seconds, { key: 'Enter' })
    expect(video.currentTime).toBe(3.5)

    const frames = screen.getByTestId('seek-frame')
    fireEvent.change(frames, { target: { value: '48' } })
    fireEvent.keyDown(frames, { key: 'Enter' })
    // 48 frames at the project's 24fps is exactly two seconds in.
    expect(video.currentTime).toBe(2)
  })
})
