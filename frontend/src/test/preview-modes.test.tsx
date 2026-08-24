/** Mode selector + preview control tests (plan 05.1-06 Task 3, D-07/D-09).
 *
 * Both components render inside the real MediaProvider against a stubbed
 * global fetch, so the thunks they call are exercised rather than mocked. The
 * settings surface is injected through the exported context so no schema or
 * values fetch is needed to change a value's identity. jsdom has no media
 * playback, so the paused/playing cases drive the provider's paused flag
 * directly — the same flag the player card reports from the element's events.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ModeSelector } from '../components/ModeSelector'
import { AUTO_PREVIEW_DEBOUNCE_MS, PreviewControls } from '../components/PreviewControls'
import { MediaProvider, useMedia } from '../state/MediaContext'
import { SettingsContext } from '../state/SettingsContext'
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

let serverProject: Project
let statusPayload: GenerationStatusResponse
let previewResponse: { status: number; body?: unknown }
let patchBodies: Record<string, unknown>[]

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/projects/p1' && init?.method === 'PATCH') {
      const patch = JSON.parse(String(init.body)) as Record<string, unknown>
      patchBodies.push(patch)
      serverProject = { ...serverProject, ...patch }
      return jsonResponse(serverProject)
    }
    if (url === '/api/projects/p1') return jsonResponse(serverProject)
    if (url === '/api/projects/p1/frames')
      return jsonResponse({ project_id: 'p1', interval: 1, duration: null, frames: [] })
    if (url === '/api/projects/p1/generation/status') return jsonResponse(statusPayload)
    if (url === '/api/projects/p1/face' && init?.method === 'POST') {
      const chosen = JSON.parse(String(init.body)).face_id as string
      serverProject = { ...serverProject, source_face_id: chosen }
      return jsonResponse({ assignments: [{ face_id: chosen }] })
    }
    if (url === '/api/projects/p1/preview' && init?.method === 'POST') {
      if (previewResponse.status >= 300)
        return jsonResponse(previewResponse.body, previewResponse.status)
      return jsonResponse(previewResponse.body ?? { url: '/preview/frame.jpg' })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const previewCalls = (mock: ReturnType<typeof installFetch>) =>
  mock.mock.calls.filter(([u]) => String(u).includes('/preview')).length

type SettingsValue = ComponentProps<typeof SettingsContext.Provider>['value']

/** Only `values` is read by the preview control; the rest of the surface is
 *  present so the context shape stays honest. */
function settingsStub(values: Record<string, unknown>): SettingsValue {
  return {
    values,
    schema: null,
    dirty: {},
    loading: false,
    saving: false,
    saveError: null,
    projectId: 'p1',
    projects: [],
    load: async () => {},
    loadProject: async () => {},
    setProject: () => {},
    setValue: () => {},
    save: async () => {},
    discard: () => {},
    applyPresetValues: () => {},
  } as unknown as SettingsValue
}

/** Test-only handles onto the provider surface the two components do not own:
 *  the paused flag the player card reports, and the face activation the
 *  library strip performs. */
function Probe() {
  const { setVideoPaused, selectFace, previewUrl, sourceFaceId } = useMedia()
  return (
    <div>
      <button data-testid="probe-play" onClick={() => setVideoPaused(false)}>
        play
      </button>
      <button data-testid="probe-pause" onClick={() => setVideoPaused(true)}>
        pause
      </button>
      <button data-testid="probe-face" onClick={() => void selectFace(`f${Date.now()}`)}>
        face
      </button>
      <span data-testid="probe-preview-url">{previewUrl ?? ''}</span>
      <span data-testid="probe-face-id">{sourceFaceId ?? ''}</span>
    </div>
  )
}

function Harness({ values }: { values: Record<string, unknown> }) {
  return (
    <SettingsContext.Provider value={settingsStub(values)}>
      <MediaProvider projectId="p1">
        <ModeSelector />
        <PreviewControls />
        <Probe />
      </MediaProvider>
    </SettingsContext.Provider>
  )
}

async function mounted(values: Record<string, unknown> = { blend: 1 }) {
  const view = render(<Harness values={values} />)
  await act(async () => {})
  await act(async () => {})
  FakeSocket.instances[0]?.onopen?.()
  await act(async () => {})
  return {
    rerender: (next: Record<string, unknown>) => view.rerender(<Harness values={next} />),
  }
}

/** Let the debounce window elapse and any resulting request settle. */
async function settleDebounce() {
  await act(async () => {
    vi.advanceTimersByTime(AUTO_PREVIEW_DEBOUNCE_MS + 50)
  })
  await act(async () => {})
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  serverProject = {
    id: 'p1',
    name: 'Test',
    has_video: true,
    video_src: '/v',
    effective_interval: 1,
    interval: 2,
    fps: 24,
    source_face_id: null,
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
  previewResponse = { status: 200, body: { url: '/preview/frame.jpg' } }
  patchBodies = []
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('mode selector — the three run modes (D-07)', () => {
  it('offers exactly the three modes with Stream-Live selected by default', async () => {
    installFetch()
    await mounted()
    const group = screen.getByRole('radiogroup', { name: /run mode/i })
    expect(group.querySelectorAll('[role="radio"]').length).toBe(3)
    expect(screen.getByTestId('mode-live')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByTestId('mode-interval')).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByTestId('mode-export')).toHaveAttribute('aria-checked', 'false')
  })

  it('reveals the interval field only in Interval mode, seeded from the row', async () => {
    installFetch()
    await mounted()
    expect(screen.queryByTestId('interval-input')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('mode-interval'))
    const input = (await screen.findByTestId('interval-input')) as HTMLInputElement
    expect(input.value).toBe('2')

    fireEvent.click(screen.getByTestId('mode-export'))
    await waitFor(() => expect(screen.queryByTestId('interval-input')).not.toBeInTheDocument())
  })

  it('persists the generation grid for live and interval, and never for export', async () => {
    installFetch()
    await mounted()
    fireEvent.click(screen.getByTestId('mode-interval'))
    await waitFor(() => expect(patchBodies).toContainEqual({ generation_mode: 'interval' }))

    fireEvent.click(screen.getByTestId('mode-live'))
    await waitFor(() => expect(patchBodies).toContainEqual({ generation_mode: 'stream' }))

    const before = patchBodies.length
    fireEvent.click(screen.getByTestId('mode-export'))
    await act(async () => {})
    expect(patchBodies.length).toBe(before)
    expect(screen.getByTestId('mode-export')).toHaveAttribute('aria-checked', 'true')
  })

  it('persists an edited interval through the project update endpoint', async () => {
    installFetch()
    await mounted()
    fireEvent.click(screen.getByTestId('mode-interval'))
    const input = await screen.findByTestId('interval-input')
    fireEvent.change(input, { target: { value: '5' } })
    fireEvent.blur(input)
    await waitFor(() => expect(patchBodies).toContainEqual({ interval: 5 }))
  })

  it('explains that export resolves its span from the transport marks', async () => {
    installFetch()
    await mounted()
    fireEvent.click(screen.getByTestId('mode-export'))
    expect(await screen.findByText(/start and end marks from the transport/i)).toBeInTheDocument()
  })
})

describe('preview controls — manual and automatic (D-09)', () => {
  it('starts with automatic preview off', async () => {
    installFetch()
    await mounted()
    expect(screen.getByTestId('auto-preview-toggle')).not.toBeChecked()
  })

  it('renders one preview on demand and points the overlay at the returned url', async () => {
    const fetchMock = installFetch()
    await mounted()
    fireEvent.click(screen.getByTestId('preview-button'))
    await waitFor(() =>
      expect(screen.getByTestId('probe-preview-url').textContent).toBe('/preview/frame.jpg'),
    )
    expect(previewCalls(fetchMock)).toBe(1)
  })

  it('surfaces a failed preview with the server message', async () => {
    installFetch()
    previewResponse = { status: 409, body: { detail: 'A run is already using the GPU.' } }
    await mounted()
    fireEvent.click(screen.getByTestId('preview-button'))
    await waitFor(() =>
      expect(screen.getByTestId('preview-error').textContent).toBe('A run is already using the GPU.'),
    )
  })

  it('disables the manual preview while a run is active', async () => {
    installFetch()
    statusPayload = { ...statusPayload, running: true }
    await mounted()
    await waitFor(() => expect(screen.getByTestId('preview-button')).toBeDisabled())
  })

  it('renders one automatic preview after a face change on a paused video', async () => {
    const fetchMock = installFetch()
    await mounted()
    fireEvent.click(screen.getByTestId('auto-preview-toggle'))
    await settleDebounce()
    const before = previewCalls(fetchMock)

    fireEvent.click(screen.getByTestId('probe-face'))
    await waitFor(() => expect(screen.getByTestId('probe-face-id').textContent).not.toBe(''))
    await settleDebounce()
    expect(previewCalls(fetchMock) - before).toBe(1)
  })

  it('collapses ten rapid settings changes into a single request', async () => {
    const fetchMock = installFetch()
    const { rerender } = await mounted({ blend: 0 })
    fireEvent.click(screen.getByTestId('auto-preview-toggle'))
    await settleDebounce()
    const before = previewCalls(fetchMock)

    for (let i = 1; i <= 10; i += 1) {
      rerender({ blend: i })
      await act(async () => {
        vi.advanceTimersByTime(10)
      })
    }
    expect(previewCalls(fetchMock) - before).toBe(0)

    await settleDebounce()
    expect(previewCalls(fetchMock) - before).toBe(1)
  })

  it('sends zero automatic previews while the video is playing, for either trigger', async () => {
    const fetchMock = installFetch()
    const { rerender } = await mounted({ blend: 0 })
    fireEvent.click(screen.getByTestId('auto-preview-toggle'))
    await settleDebounce()
    fireEvent.click(screen.getByTestId('probe-play'))
    await act(async () => {})
    const before = previewCalls(fetchMock)

    fireEvent.click(screen.getByTestId('probe-face'))
    rerender({ blend: 1 })
    await settleDebounce()
    await settleDebounce()
    expect(previewCalls(fetchMock) - before).toBe(0)
  })

  it('drops a pending automatic preview when playback starts mid-window', async () => {
    const fetchMock = installFetch()
    const { rerender } = await mounted({ blend: 0 })
    fireEvent.click(screen.getByTestId('auto-preview-toggle'))
    await settleDebounce()
    const before = previewCalls(fetchMock)

    rerender({ blend: 1 })
    await act(async () => {
      vi.advanceTimersByTime(AUTO_PREVIEW_DEBOUNCE_MS / 2)
    })
    fireEvent.click(screen.getByTestId('probe-play'))
    await settleDebounce()
    expect(previewCalls(fetchMock) - before).toBe(0)

    // Pausing again re-arms it, so the drop is a cancellation, not a wedge.
    fireEvent.click(screen.getByTestId('probe-pause'))
    await settleDebounce()
    expect(previewCalls(fetchMock) - before).toBe(1)
  })

  it('sends no automatic preview while a run is active', async () => {
    const fetchMock = installFetch()
    statusPayload = { ...statusPayload, running: true }
    const { rerender } = await mounted({ blend: 0 })
    fireEvent.click(screen.getByTestId('auto-preview-toggle'))
    const before = previewCalls(fetchMock)
    rerender({ blend: 1 })
    await settleDebounce()
    await settleDebounce()
    expect(previewCalls(fetchMock) - before).toBe(0)
  })
})
