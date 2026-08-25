/** Projects overview + New/Delete.
 *
 * Two things are pinned here because both were broken or missing and neither
 * fails loudly: the header picker disappearing when a project is named in the
 * URL (the list was only fetched on the fallback path, so opening a project by
 * link removed the only way to reach the others), and deleting the open project
 * leaving the app with no project selected — a state in which every Save is a
 * silent no-op.
 */

import { useEffect } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Header } from '../components/Header'
import { ProjectsView } from '../components/ProjectsView'
import { SettingsProvider, useSettings } from '../state/SettingsContext'
import type { Project } from '../types'

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

const schema = { schema: {}, widgets: { key0: { type: 'toggle', tier: 'project', group: '', label: 'K' } } }

interface Server {
  projects: Project[]
  created: string[]
  deleted: string[]
}

function installServer(initial: Project[]): Server {
  const server: Server = { projects: [...initial], created: [], deleted: [] }
  let nextId = 1
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      if (url === '/api/schema') return jsonResponse(schema)
      if (url === '/api/presets') return jsonResponse({ presets: [] })
      if (url === '/api/projects' && method === 'GET') return jsonResponse(server.projects)
      if (url === '/api/projects' && method === 'POST') {
        const name = JSON.parse(String(init?.body)).name as string
        const created: Project = { id: `new${nextId++}`, name, has_video: false }
        server.created.push(name)
        server.projects.push(created)
        return jsonResponse(created, 201)
      }
      const del = /^\/api\/projects\/([^/]+)$/.exec(url)
      if (del && method === 'DELETE') {
        server.deleted.push(del[1])
        server.projects = server.projects.filter((p) => p.id !== del[1])
        return { ok: true, status: 204, statusText: 'no content', json: async () => undefined }
      }
      const settings = /^\/api\/projects\/([^/]+)\/settings$/.exec(url)
      if (settings) return jsonResponse({ values: { key0: true } })
      return jsonResponse({ detail: 'not found' }, 404)
    }),
  )
  return server
}

/** Renders the two surfaces together with a real provider, and boots it the way
 *  App does — the loader is what resolves which project is open. */
function Harness() {
  const { load } = useSettings()
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <>
      <Header />
      <ProjectsView active />
    </>
  )
}

function mount(initial: Project[]) {
  const server = installServer(initial)
  render(
    <SettingsProvider>
      <Harness />
    </SettingsProvider>,
  )
  return server
}

const rows = () => screen.queryAllByTestId('project-row')

beforeEach(() => {
  window.history.replaceState({}, '', '/?project=p1')
  vi.unstubAllGlobals()
})

describe('projects overview', () => {
  it('lists every project with its source facts and marks the open one', async () => {
    mount([
      { id: 'p1', name: 'Alpha', has_video: true, video_filename: 'clip.mp4', width: 720, height: 1280, duration: 72 },
      { id: 'p2', name: 'Beta', has_video: false },
    ])
    await waitFor(() => expect(rows()).toHaveLength(2))

    const alpha = rows()[0]
    expect(within(alpha).getByText(/clip\.mp4/)).toBeTruthy()
    expect(within(alpha).getByText(/720×1280/)).toBeTruthy()
    // 72s reads as 1:12, not as "72".
    expect(within(alpha).getByText(/1:12/)).toBeTruthy()
    expect(within(alpha).getByText('Open', { selector: 'span' })).toBeTruthy()

    // A project with nothing bound says so rather than showing empty facts.
    expect(within(rows()[1]).getByText(/No video bound/)).toBeTruthy()
    expect(screen.getByTestId('projects-count').textContent).toBe('2 total')
  })

  it('offers the header picker even when the project came from the URL', async () => {
    mount([
      { id: 'p1', name: 'Alpha' },
      { id: 'p2', name: 'Beta' },
    ])
    // The picker only renders when the list is non-empty; with ?project=p1 in
    // the address bar the list used to be left empty and the picker vanished.
    await waitFor(() =>
      expect((screen.getByLabelText('Project') as HTMLSelectElement).value).toBe('p1'),
    )
    const picker = screen.getByLabelText('Project')
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Alpha',
      'Beta',
    ])
  })

  it('creates a project from the overview and opens it', async () => {
    const user = userEvent.setup()
    const server = mount([{ id: 'p1', name: 'Alpha' }])
    await waitFor(() => expect(rows()).toHaveLength(1))

    await user.click(screen.getByTestId('btn-new-project-home'))
    await waitFor(() => expect(server.created).toEqual(['Untitled project']))
    // Created *and* opened: a new project the user has to go find is a bug.
    await waitFor(() =>
      expect((screen.getByLabelText('Project') as HTMLSelectElement).value).toBe('new1'),
    )
  })

  it('deletes only after confirmation, and never leaves nothing open', async () => {
    const user = userEvent.setup()
    const server = mount([
      { id: 'p1', name: 'Alpha' },
      { id: 'p2', name: 'Beta' },
    ])
    await waitFor(() => expect(rows()).toHaveLength(2))

    await user.click(within(rows()[0]).getByText('Delete'))
    // Nothing is gone yet — the dialog names what is about to be destroyed.
    expect(server.deleted).toEqual([])
    expect(screen.getByTestId('project-delete-body').textContent).toContain('Alpha')

    await user.click(screen.getByTestId('btn-confirm-delete-project'))
    await waitFor(() => expect(server.deleted).toEqual(['p1']))
    await waitFor(() => expect(rows()).toHaveLength(1))
    // The open project was the one deleted, so the survivor is opened instead.
    await waitFor(() =>
      expect((screen.getByLabelText('Project') as HTMLSelectElement).value).toBe('p2'),
    )
  })

  it('creates a replacement when the last project is deleted', async () => {
    const user = userEvent.setup()
    const server = mount([{ id: 'p1', name: 'Only' }])
    await waitFor(() => expect(rows()).toHaveLength(1))

    await user.click(within(rows()[0]).getByText('Delete'))
    await user.click(screen.getByTestId('btn-confirm-delete-project'))

    // No project selected means every Save is a silent no-op, so the app
    // refuses to sit in that state.
    await waitFor(() => expect(server.created).toEqual(['My project']))
    await waitFor(() =>
      expect((screen.getByLabelText('Project') as HTMLSelectElement).value).toBe('new1'),
    )
  })
})
