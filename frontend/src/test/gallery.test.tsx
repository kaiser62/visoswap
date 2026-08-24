/** Gallery tests (plan 05.1-07 Task 3, D-01/D-10/D-12).
 *
 * The gallery is a view, not a route: switching to it hides the studio with
 * CSS and unmounts nothing, because the studio holds the gated controls whose
 * count is a phase gate. The toggle test asserts that count across the switch.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { GalleryView } from '../components/GalleryView'
import schemaJson from '../../../visoswap/schema/schema.json'
import type { Take } from '../types'

interface Entry {
  type: string
  tier: string
  group: string
  label: string
  options?: string[] | null
  default?: unknown
}

const widgets = schemaJson.widgets as Record<string, Entry>
const schemaKeyCount = Object.keys(widgets).length

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

let takes: Take[]
/** Number of remaining list requests that should fail. */
let failListTimes = 0
let deleteStatus = 204

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

function schemaDoc() {
  const resolved: Record<string, Entry> = {}
  for (const [k, e] of Object.entries(widgets)) resolved[k] = { ...e, options: e.options ?? [] }
  return { schema: schemaJson.schema, widgets: resolved }
}

function valuesDoc() {
  const values: Record<string, unknown> = {}
  for (const [k, e] of Object.entries(widgets)) values[k] = e.default ?? ''
  return { values }
}

function installFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/takes' && (init?.method ?? 'GET') === 'GET') {
      if (failListTimes > 0) {
        failListTimes -= 1
        throw new Error('takes unavailable')
      }
      return jsonResponse(takes)
    }
    if (url.startsWith('/api/takes/') && init?.method === 'DELETE') {
      if (deleteStatus >= 300) return jsonResponse({ detail: 'gone' }, deleteStatus)
      return jsonResponse(null, 204)
    }
    if (url === '/api/schema') return jsonResponse(schemaDoc())
    if (url.startsWith('/api/projects/t/settings')) return jsonResponse(valuesDoc())
    if (url === '/api/projects') return jsonResponse([{ id: 't', name: 'test' }])
    if (url === '/api/projects/t')
      return jsonResponse({
        id: 't',
        name: 'test',
        has_video: false,
        video_src: null,
        effective_interval: 1,
        fps: 24,
      })
    if (url === '/api/projects/t/frames')
      return jsonResponse({ project_id: 't', interval: 1, duration: null, frames: [] })
    if (url === '/api/projects/t/generation/status')
      return jsonResponse({
        project_id: 't',
        running: false,
        full_video_mode: false,
        current_time: 0,
        counts: { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 },
        average_duration: null,
        recording: { available: false, complete: false, bytes: 0 },
      })
    if (url === '/api/faces') return jsonResponse([])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const listCalls = (mock: ReturnType<typeof installFetch>) =>
  mock.mock.calls.filter(([u]) => u === '/api/takes').length

async function mountedGallery() {
  const fetchMock = installFetch()
  render(<GalleryView />)
  await act(async () => {})
  await act(async () => {})
  return { fetchMock }
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  failListTimes = 0
  deleteStatus = 204
  takes = [
    {
      name: 'newest.mp4',
      bytes: 5_242_880,
      modified: 1_700_000_400,
      partial: false,
      url: '/api/takes/newest.mp4',
    },
    {
      name: 'older.partial.mp4',
      bytes: 1_048_576,
      modified: 1_700_000_000,
      partial: true,
      url: '/api/takes/older.partial.mp4',
    },
  ]
  Object.defineProperty(window, 'location', {
    value: { search: '?project=t' },
    writable: true,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('gallery view', () => {
  it('lists takes newest first with name, size and modified time', async () => {
    await mountedGallery()
    const items = await screen.findAllByTestId('take-item')
    expect(items.length).toBe(2)
    expect(items[0].textContent).toContain('newest.mp4')
    expect(items[0].textContent).toContain('5.0 MB')
    // The modified time is rendered as something readable, not a raw epoch.
    expect(items[0].textContent).not.toContain('1700000400')
    expect(within(items[0]).getByTestId('take-modified').textContent).not.toBe('')
    expect(items[1].textContent).toContain('older.partial.mp4')
  })

  it('marks a take flagged partial', async () => {
    await mountedGallery()
    const items = await screen.findAllByTestId('take-item')
    expect(within(items[1]).getByTestId('take-partial')).toBeInTheDocument()
    expect(within(items[0]).queryByTestId('take-partial')).not.toBeInTheDocument()
  })

  it('plays a selected take inline from the take endpoint', async () => {
    await mountedGallery()
    const items = await screen.findAllByTestId('take-item')
    fireEvent.click(within(items[1]).getByTestId('take-play'))
    await waitFor(() =>
      expect(screen.getByTestId('take-player').getAttribute('src')).toBe(
        '/api/takes/older.partial.mp4',
      ),
    )
  })

  it('offers a download per take', async () => {
    await mountedGallery()
    const items = await screen.findAllByTestId('take-item')
    expect(within(items[0]).getByTestId('take-download').getAttribute('href')).toBe(
      '/api/takes/newest.mp4',
    )
    expect(within(items[1]).getByTestId('take-download')).toHaveAttribute('download')
  })

  it('confirms before deleting and drops the take from the list on success', async () => {
    const { fetchMock } = await mountedGallery()
    const items = await screen.findAllByTestId('take-item')
    fireEvent.click(within(items[0]).getByTestId('take-delete'))
    // Nothing is destroyed by opening the dialog.
    expect(fetchMock.mock.calls.filter(([, i]) => i?.method === 'DELETE').length).toBe(0)

    takes = takes.filter((t) => t.name !== 'newest.mp4')
    fireEvent.click(screen.getByTestId('take-delete-confirm'))
    await waitFor(() => expect(screen.getAllByTestId('take-item').length).toBe(1))
    expect(screen.getByTestId('take-item').textContent).toContain('older.partial.mp4')
  })

  it('explains the empty state and renders no take item', async () => {
    takes = []
    await mountedGallery()
    await waitFor(() => expect(screen.getByTestId('gallery-empty')).toBeInTheDocument())
    expect(screen.getByTestId('gallery-empty').textContent).toMatch(/after a run/i)
    expect(screen.queryByTestId('take-item')).not.toBeInTheDocument()
  })

  it('shows an error with a retry that re-issues the list request', async () => {
    failListTimes = 1
    const { fetchMock } = await mountedGallery()
    await waitFor(() => expect(screen.getByTestId('gallery-error')).toBeInTheDocument())
    const before = listCalls(fetchMock)

    fireEvent.click(screen.getByTestId('gallery-retry'))
    await waitFor(() => expect(listCalls(fetchMock)).toBe(before + 1))
    await waitFor(() => expect(screen.getAllByTestId('take-item').length).toBe(2))
  })
})

describe('view toggle (D-01/D-02)', () => {
  it('switches views and keeps every schema control mounted', async () => {
    installFetch()
    render(<App />)
    await waitFor(() =>
      expect(document.querySelectorAll('[data-key]').length).toBe(schemaKeyCount),
    )

    fireEvent.click(screen.getByTestId('view-gallery'))
    await waitFor(() => expect(screen.getByTestId('gallery-view')).toBeVisible())
    // The studio is hidden, not unmounted: the gated controls are still there.
    expect(document.querySelectorAll('[data-key]').length).toBe(schemaKeyCount)
    expect(screen.getByTestId('studio-view')).not.toBeVisible()

    fireEvent.click(screen.getByTestId('view-studio'))
    await waitFor(() => expect(screen.getByTestId('studio-view')).toBeVisible())
  })
})
