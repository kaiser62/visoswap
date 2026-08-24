/** Media card tests (plan 05.1-06 Task 1) — stubbed fetch, one test per
 *  behavior bullet. The card renders inside the real MediaProvider so the
 *  refresh path it depends on is exercised, not mocked away.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MediaCard } from '../components/MediaCard'
import { MAX_UPLOAD_BYTES } from '../lib/api'
import { MediaProvider } from '../state/MediaContext'
import type { GenerationStatusResponse, Project } from '../types'

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

const statusPayload: GenerationStatusResponse = {
  project_id: 'p1',
  running: false,
  full_video_mode: false,
  current_time: 0,
  counts: { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 },
  average_duration: null,
  recording: { available: false, complete: false, bytes: 0 },
}

function projectPayload(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    name: 'Test',
    has_video: false,
    video_src: null,
    video_filename: null,
    effective_interval: 1,
    fps: 24,
    ...overrides,
  }
}

let serverProject: Project
let sourceResponses: { status: number; body?: unknown }[] = []
let urlResponse: { status: number; body?: unknown }

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/projects/p1') return jsonResponse(serverProject)
    if (url === '/api/projects/p1/frames')
      return jsonResponse({ project_id: 'p1', interval: 1, duration: null, frames: [] })
    if (url === '/api/projects/p1/generation/status') return jsonResponse(statusPayload)
    if (url === '/api/projects/p1/source' && init?.method === 'POST') {
      const r = sourceResponses.shift() ?? { status: 200, body: serverProject }
      if (r.status >= 300) return jsonResponse(r.body ?? { detail: 'error' }, r.status)
      serverProject = (r.body as Project) ?? serverProject
      return jsonResponse(serverProject)
    }
    if (url === '/api/projects/p1/url' && init?.method === 'POST') {
      if (urlResponse.status >= 300) return jsonResponse(urlResponse.body, urlResponse.status)
      serverProject = (urlResponse.body as Project) ?? serverProject
      return jsonResponse(serverProject)
    }
    if (url === '/api/faces') return jsonResponse([])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const callsTo = (mock: ReturnType<typeof installFetch>, needle: string, method?: string) =>
  mock.mock.calls.filter(
    ([u, i]) =>
      String(u).includes(needle) && (method === undefined || (i as RequestInit)?.method === method),
  ).length

async function mounted(fetchMock: ReturnType<typeof installFetch>) {
  render(
    <MediaProvider projectId="p1">
      <MediaCard />
    </MediaProvider>,
  )
  await act(async () => {})
  await act(async () => {})
  FakeSocket.instances[0]?.onopen?.()
  await act(async () => {})
  expect(callsTo(fetchMock, '/api/projects/p1')).toBeGreaterThan(0)
}

const oversizeFile = () => {
  const f = new File(['x'], 'huge.mp4', { type: 'video/mp4' })
  Object.defineProperty(f, 'size', { value: MAX_UPLOAD_BYTES + 1 })
  return f
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  serverProject = projectPayload()
  sourceResponses = []
  urlResponse = { status: 200 }
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('media card — video ingest', () => {
  it('shows an empty state naming both ways to add a video when none exists', async () => {
    await mounted(installFetch())
    expect(screen.getByText(/add a video by choosing a local file or pointing at a url/i))
      .toBeInTheDocument()
    expect(screen.getByTestId('media-file-input')).toBeInTheDocument()
    expect(screen.getByTestId('media-url-input')).toBeInTheDocument()
  })

  it('posts a chosen file, refetches the project, and shows the video filename', async () => {
    const fetchMock = installFetch()
    const getsBefore = callsTo(fetchMock, '/api/projects/p1')
    sourceResponses.push({
      status: 200,
      body: projectPayload({ has_video: true, video_src: '/api/projects/p1/video', video_filename: 'test.mp4' }),
    })
    await mounted(fetchMock)
    fireEvent.change(screen.getByTestId('media-file-input'), {
      target: { files: [new File(['v'], 'test.mp4', { type: 'video/mp4' })] },
    })
    await waitFor(() =>
      expect(screen.getByTestId('media-video-filename').textContent).toBe('test.mp4'),
    )
    expect(callsTo(fetchMock, '/projects/p1/source', 'POST')).toBe(1)
    expect(callsTo(fetchMock, '/api/projects/p1')).toBeGreaterThan(getsBefore + 1)
  })

  it('refuses an oversize file client-side with zero requests, naming the limit', async () => {
    const fetchMock = installFetch()
    await mounted(fetchMock)
    fireEvent.change(screen.getByTestId('media-file-input'), { target: { files: [oversizeFile()] } })
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByTestId('media-error').textContent).toMatch(/8 GB/)
    expect(callsTo(fetchMock, '/projects/p1/source', 'POST')).toBe(0)
  })

  it('surfaces a 413 with the server message verbatim', async () => {
    const fetchMock = installFetch()
    sourceResponses.push({ status: 413, body: { detail: 'file too large' } })
    await mounted(fetchMock)
    fireEvent.change(screen.getByTestId('media-file-input'), {
      target: { files: [new File(['v'], 'ok.mp4', { type: 'video/mp4' })] },
    })
    await waitFor(() => expect(screen.getByTestId('media-error').textContent).toBe('file too large'))
  })

  it('submits a URL and refetches the project on success', async () => {
    const fetchMock = installFetch()
    urlResponse = {
      status: 200,
      body: projectPayload({ has_video: true, video_src: '/api/projects/p1/video' }),
    }
    await mounted(fetchMock)
    fireEvent.change(screen.getByTestId('media-url-input'), {
      target: { value: 'https://example.com/video.mp4' },
    })
    fireEvent.click(screen.getByTestId('media-url-submit'))
    await waitFor(() => expect(screen.getByText(/video url set/i)).toBeInTheDocument())
    expect(callsTo(fetchMock, '/projects/p1/url', 'POST')).toBe(1)
    expect(callsTo(fetchMock, '/api/projects/p1')).toBeGreaterThan(2)
  })

  it('shows a failed URL ingest with the server message and keeps the entered URL', async () => {
    const fetchMock = installFetch()
    urlResponse = { status: 400, body: { detail: 'unsupported host' } }
    await mounted(fetchMock)
    const input = screen.getByTestId('media-url-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'https://bad.example/v.mp4' } })
    fireEvent.click(screen.getByTestId('media-url-submit'))
    await waitFor(() => expect(screen.getByTestId('media-error').textContent).toBe('unsupported host'))
    expect(input.value).toBe('https://bad.example/v.mp4')
  })

  it('disables submit controls and shows progress while an upload is in flight', async () => {
    let release!: (p: Project) => void
    const gate = new Promise<Project>((r) => {
      release = r
    })
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/projects/p1/source' && init?.method === 'POST') {
        const p = await gate
        return jsonResponse(p)
      }
      if (url === '/api/projects/p1') return jsonResponse(serverProject)
      if (url === '/api/projects/p1/frames')
        return jsonResponse({ project_id: 'p1', interval: 1, duration: null, frames: [] })
      if (url === '/api/projects/p1/generation/status') return jsonResponse(statusPayload)
      if (url === '/api/faces') return jsonResponse([])
      return jsonResponse({ detail: 'nope' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MediaProvider projectId="p1">
        <MediaCard />
      </MediaProvider>,
    )
    await act(async () => {})
    await act(async () => {})

    fireEvent.change(screen.getByTestId('media-file-input'), {
      target: { files: [new File(['v'], 'slow.mp4')] },
    })
    expect(await screen.findByTestId('media-busy')).toBeInTheDocument()
    expect(screen.getByTestId('media-file-input')).toBeDisabled()
    expect(screen.getByTestId('media-url-submit')).toBeDisabled()

    await act(async () => {
      release(projectPayload())
    })
  })

  it('replaces an existing video without leaving the project', async () => {
    const fetchMock = installFetch()
    await mounted(fetchMock)
    sourceResponses.push({
      status: 200,
      body: projectPayload({ has_video: true, video_src: '/v', video_filename: 'first.mp4' }),
    })
    fireEvent.change(screen.getByTestId('media-file-input'), {
      target: { files: [new File(['a'], 'first.mp4')] },
    })
    await waitFor(() => expect(screen.getByTestId('media-video-filename').textContent).toBe('first.mp4'))
    expect(screen.getByTestId('media-file-input')).toBeInTheDocument()

    sourceResponses.push({
      status: 200,
      body: projectPayload({ has_video: true, video_src: '/v', video_filename: 'second.mp4' }),
    })
    fireEvent.change(screen.getByTestId('media-file-input'), {
      target: { files: [new File(['b'], 'second.mp4')] },
    })
    await waitFor(() =>
      expect(screen.getByTestId('media-video-filename').textContent).toBe('second.mp4'),
    )
    expect(screen.queryByText(/first\.mp4/)).not.toBeInTheDocument()
    expect(callsTo(fetchMock, '/projects/p1/source', 'POST')).toBe(2)
  })
})
