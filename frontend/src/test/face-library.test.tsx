/** Face library + cascade-delete dialog tests (plan 05.1-06 Task 2, D-03/D-05)
 *  — stubbed fetch against the real components inside the real MediaProvider.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FaceLibrary } from '../components/FaceLibrary'
import FaceDeleteDialog from '../components/FaceDeleteDialog'
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

function face(id: string) {
  return {
    face_id: id,
    display_name: id === 'a'.repeat(32) ? 'Alice' : 'Bob',
    bytes: 10,
    url: `/api/faces/${id}/image`,
    thumbnail_url: `/api/faces/${id}/thumbnail`,
  }
}
const FACE_A = 'a'.repeat(32)
const FACE_B = 'b'.repeat(32)

let faces: ReturnType<typeof face>[]
let projectsUsingA: { id: string; name: string }[]
let uploadResponse: { status: number; body?: unknown }
/** The id the store resolves an upload to. Content addressing means the SAME
 *  bytes always come back as the same id, so a repeat upload adds no entry. */
let uploadedFaceId: string
let deletedCalls: string[]
/** Which face each project points at — the library is global, the active mark is not. */
let projectFaces: Record<string, string | null>

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}
function noContent() {
  return { ok: true, status: 204, statusText: 'ok', json: async () => undefined }
}

function installFetch(projectOverrides: Partial<Project> = {}) {
  const projectFor = (id: string): Project => ({
    id,
    name: id === 'p1' ? 'One' : 'Two',
    has_video: true,
    video_src: '/v',
    source_face_id: projectFaces[id] ?? null,
    effective_interval: 1,
    fps: 24,
    ...projectOverrides,
  })
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/faces' && init?.method === undefined) return jsonResponse(faces)
    if (url === '/api/faces' && init?.method === 'POST') {
      const r = uploadResponse
      if (r.status >= 300) return jsonResponse(r.body ?? { detail: 'bad image' }, r.status)
      const stored = face(uploadedFaceId)
      if (!faces.some((f) => f.face_id === stored.face_id)) faces = [...faces, stored]
      return jsonResponse(stored)
    }
    if (url === `/api/faces/${FACE_A}/usage`)
      return jsonResponse({ projects: projectsUsingA.map((p) => ({ ...p })) })
    if (url.startsWith('/api/faces/') && init?.method === 'DELETE') {
      deletedCalls.push(url)
      return noContent()
    }
    const facePost = /^\/api\/projects\/(\w+)\/face$/.exec(url)
    if (facePost && init?.method === 'POST') {
      const chosen = JSON.parse(String(init.body)).face_id as string
      projectFaces[facePost[1]] = chosen
      return jsonResponse({ assignments: [{ face_id: chosen }] })
    }
    const plain = /^\/api\/projects\/(\w+)$/.exec(url)
    if (plain) return jsonResponse(projectFor(plain[1]))
    const frames = /^\/api\/projects\/(\w+)\/frames$/.exec(url)
    if (frames)
      return jsonResponse({ project_id: frames[1], interval: 1, duration: null, frames: [] })
    if (/^\/api\/projects\/\w+\/generation\/status$/.test(url)) return jsonResponse(statusPayload)
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function mounted(mock: ReturnType<typeof installFetch>, projectId = 'p1') {
  render(
    <MediaProvider projectId={projectId}>
      <FaceLibrary />
    </MediaProvider>,
  )
  await act(async () => {})
  FakeSocket.instances[0]?.onopen?.()
  await act(async () => {})
  await act(async () => {})
  expect(screen.getByTestId('face-strip')).toBeInTheDocument()
  return mock
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  faces = [face(FACE_A), face(FACE_B)]
  projectsUsingA = [{ id: 'p2', name: 'Project Two' }]
  uploadResponse = { status: 201, body: null }
  uploadedFaceId = 'c'.repeat(32)
  deletedCalls = []
  projectFaces = { p1: FACE_A, p2: FACE_B }
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('face library', () => {
  it('lists every face in the store with its thumbnail, independent of the open project', async () => {
    const mock = installFetch()
    await mounted(mock)
    expect(screen.getByTestId(`face-${FACE_A}`)).toBeInTheDocument()
    expect(screen.getByTestId(`face-${FACE_B}`)).toBeInTheDocument()
    // The list request is global: its URL carries no project segment.
    const listUrls = mock.mock.calls.map((c) => String(c[0])).filter((u) => u === '/api/faces')
    expect(listUrls.length).toBeGreaterThan(0)
    expect(listUrls.every((u) => !u.includes('/projects/'))).toBe(true)
  })

  it('uploading an image adds it to the strip without a page reload', async () => {
    const mock = installFetch()
    await mounted(mock)
    const listCalls = () =>
      mock.mock.calls.filter((c) => String(c[0]) === '/api/faces' && c[1]?.method === undefined)
        .length
    const listsBefore = listCalls()
    fireEvent.change(screen.getByTestId('face-upload-input'), {
      target: { files: [new File(['img'], 'new.png', { type: 'image/png' })] },
    })
    await waitFor(() =>
      expect(screen.getByTestId(`face-${'c'.repeat(32)}`)).toBeInTheDocument(),
    )
    expect(listCalls()).toBe(listsBefore + 1)
  })

  it('uploading the same image twice leaves one entry — the store is content addressed', async () => {
    // Both uploads answer with the SAME content id — the one already stored.
    faces = [face(FACE_A)]
    uploadedFaceId = FACE_A
    const mock = installFetch()
    await mounted(mock)
    for (let i = 0; i < 2; i += 1) {
      fireEvent.change(screen.getByTestId('face-upload-input'), {
        target: { files: [new File(['same'], 'same.png')] },
      })
      await act(async () => {})
    }
    await waitFor(() =>
      expect(
        mock.mock.calls.filter((c) => String(c[0]) === '/api/faces' && c[1]?.method === 'POST')
          .length,
      ).toBe(2),
    )
    // One entry per content id after both uploads (the delete and name
    // affordances share the `face-` prefix, so they are excluded).
    const strip = screen.getByTestId('face-strip')
    const entries = Array.from(strip.querySelectorAll('[data-testid^="face-"]')).filter((el) => {
      const id = el.getAttribute('data-testid') ?? ''
      return !id.startsWith('face-delete-') && !id.startsWith('face-name-')
    })
    expect(entries.length).toBe(1)
    expect(entries[0].getAttribute('data-testid')).toBe(`face-${FACE_A}`)
  })

  it('labels each face with its original upload name, not its digest', async () => {
    faces = [face(FACE_A), face(FACE_B)]
    await mounted(installFetch())
    expect(screen.getByTestId(`face-name-${FACE_A}`).textContent).toBe('Alice')
    expect(screen.getByTestId(`face-name-${FACE_B}`).textContent).toBe('Bob')
    expect(screen.getByTestId('face-count').textContent).toContain('2 faces')
  })

  it('clicking a face activates it for the open project and marks it active', async () => {
    projectFaces = { p1: null, p2: FACE_B }
    const mock = installFetch()
    await mounted(mock)
    fireEvent.click(screen.getByTestId(`face-${FACE_B}`))
    await waitFor(() =>
      expect(mock.mock.calls.some((c) => String(c[0]).endsWith('/face'))).toBe(true),
    )
    await waitFor(() =>
      expect(screen.getByTestId(`face-${FACE_B}`).getAttribute('data-active')).toBe('true'),
    )
    expect(screen.getByTestId(`face-${FACE_A}`).getAttribute('data-active')).toBeNull()
  })

  it('switching the open project moves the active mark without changing the list or the request path', async () => {
    const mock = installFetch()
    const { rerender } = render(
      <MediaProvider projectId="p1">
        <FaceLibrary />
      </MediaProvider>,
    )
    await act(async () => {})
    await act(async () => {})
    await waitFor(() =>
      expect(screen.getByTestId(`face-${FACE_A}`).getAttribute('data-active')).toBe('true'),
    )

    rerender(
      <MediaProvider projectId="p2">
        <FaceLibrary />
      </MediaProvider>,
    )
    await act(async () => {})
    await act(async () => {})
    // p2's payload points at B; the list itself is unchanged.
    await waitFor(() =>
      expect(screen.getByTestId(`face-${FACE_B}`).getAttribute('data-active')).toBe('true'),
    )
    expect(screen.getByTestId(`face-${FACE_A}`).getAttribute('data-active')).toBeNull()
    // Every list request is global — no project segment in any of them.
    const listUrls = mock.mock.calls.map((c) => String(c[0])).filter((u) => u.startsWith('/api/faces'))
    expect(listUrls.every((u) => !u.includes('/projects/'))).toBe(true)
  })

  it('an unsupported file type surfaces the server message', async () => {
    const mock = installFetch()
    uploadResponse = { status: 400, body: { detail: 'unsupported suffix' } }
    await mounted(mock)
    fireEvent.change(screen.getByTestId('face-upload-input'), {
      target: { files: [new File(['x'], 'note.txt', { type: 'text/plain' })] },
    })
    await waitFor(() => expect(screen.getByTestId('face-upload-error').textContent).toBe('unsupported suffix'))
  })
})

describe('cascade delete flow (D-05)', () => {
  async function mountedWithDialog(
    usage: { id: string; name: string }[],
    mock = installFetch(),
  ) {
    projectsUsingA = usage
    await mounted(mock)
    return screen.getByTestId(`face-delete-${FACE_A}`)
  }

  it('fetches usage first and names the affected projects before offering confirm', async () => {
    const btn = await mountedWithDialog([{ id: 'p2', name: 'Project Two' }])
    fireEvent.click(btn)
    // While the usage request is pending the confirm control must not exist…
    expect(screen.queryByTestId('face-delete-confirm')).not.toBeInTheDocument()
    // …and once it resolves, the names and warning are rendered with confirm present.
    expect(await screen.findByTestId('face-delete-confirm')).toBeInTheDocument()
    expect(screen.getByTestId('face-delete-warning')).toBeInTheDocument()
    expect(screen.getByText('Project Two')).toBeInTheDocument()
  })

  it('confirming a cascade delete sends force=true and refreshes list + project', async () => {
    const mock = installFetch()
    const btn = await mountedWithDialog([{ id: 'p2', name: 'Project Two' }], mock)
    const projectsBefore = mock.mock.calls.filter((c) => String(c[0]) === '/api/projects/p1').length
    fireEvent.click(btn)
    fireEvent.click(await screen.findByTestId('face-delete-confirm'))
    await waitFor(() => expect(deletedCalls).toEqual([`/api/faces/${FACE_A}?force=true`]))
    const projectsAfter = mock.mock.calls.filter((c) => String(c[0]) === '/api/projects/p1').length
    expect(projectsAfter).toBeGreaterThan(projectsBefore)
  })

  it('a face used by no project deletes after a plain confirmation with NO force flag', async () => {
    const mock = installFetch()
    const btn = await mountedWithDialog([], mock)
    fireEvent.click(btn)
    expect(await screen.findByTestId('face-delete-confirm')).toBeInTheDocument()
    expect(screen.queryByTestId('face-delete-warning')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('face-delete-confirm'))
    await waitFor(() => expect(deletedCalls).toEqual([`/api/faces/${FACE_A}`]))
  })

  it('cancelling the dialog issues no delete request', async () => {
    installFetch()
    const btn = await mountedWithDialog([])
    fireEvent.click(btn)
    await screen.findByText(/no project uses this face/i)
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(deletedCalls).toEqual([])
  })

  it('renders the dialog standalone: plain body when unused, warning when used', async () => {
    projectsUsingA = []
    installFetch()
    render(
      <MediaProvider projectId="p1">
        <FaceDeleteDialog faceId={FACE_A} initialUsage={[]} onClose={() => {}} onDeleted={() => {}} />
      </MediaProvider>,
    )
    expect(await screen.findByText(/no project uses this face/i)).toBeInTheDocument()
  })
})
