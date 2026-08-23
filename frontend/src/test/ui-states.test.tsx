/** UI state coverage per the UI-SPEC: loading / error / empty / populated. */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'ok',
    json: async () => body,
  }
}

function fakeSchema(widgetCount: number) {
  const widgets: Record<string, unknown> = {}
  for (let i = 0; i < widgetCount; i++) {
    widgets[`key${i}`] = { type: 'toggle', tier: 'project', group: '', label: `Label ${i}` }
  }
  return { schema: {}, widgets }
}

beforeEach(() => {
  // Real jsdom URL handling — the component reads location.search and its
  // history.replaceState sync must land somewhere assertions can see.
  window.history.replaceState({}, '', '/?project=t')
})

describe('UI states', () => {
  it('error state renders banner + zero controls, retry recovers', async () => {
    const user = userEvent.setup()
    let schemaAttempts = 0
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/presets') return jsonResponse({ presets: [] })
      if (url === '/api/schema') {
        schemaAttempts += 1
        if (schemaAttempts === 1) throw new Error('network')
        return jsonResponse(fakeSchema(2))
      }
      if (url === '/api/projects/t/settings')
        return jsonResponse({ values: { key0: true, key1: false } })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await waitFor(() => {
      expect(screen.getByText("Couldn't load settings")).toBeTruthy()
    })
    expect(document.querySelectorAll('[data-key]').length).toBe(0)

    await user.click(screen.getByText('Retry'))
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(2)
    })
  })

  it('empty schema renders documented empty copy', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/schema') return jsonResponse(fakeSchema(0))
        if (url === '/api/projects/t/settings') return jsonResponse({ values: {} })
        return jsonResponse({ detail: 'not found' }, 404)
      }),
    )
    render(<App />)
    await waitFor(() => {
      expect(screen.getByText('No settings available')).toBeTruthy()
    })
    expect(document.querySelectorAll('[data-key]').length).toBe(0)
  })

  it('loading state renders skeletons before resolution', async () => {
    let resolveSchema: (v: unknown) => void
    const schemaGate = new Promise<unknown>((r) => (resolveSchema = r))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/schema') return jsonResponse(await schemaGate)
        if (url === '/api/projects/t/settings') return jsonResponse({ values: {} })
        return jsonResponse({ detail: 'not found' }, 404)
      }),
    )
    render(<App />)
    // Initially loading: skeleton present, no controls.
    expect(document.querySelector('.animate-pulse')).toBeTruthy()
    expect(document.querySelectorAll('[data-key]').length).toBe(0)

    resolveSchema!(fakeSchema(1))
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(1)
    })
  })

  it('root without ?project auto-selects a project so saves have a target', async () => {
    window.history.replaceState({}, '', '/')
    const fetchedUrls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        fetchedUrls.push(`${init?.method ?? 'GET'} ${url}`)
        if (url === '/api/schema') return jsonResponse(fakeSchema(1))
        if (url === '/api/projects' && !init?.method)
          return jsonResponse([{ id: 'latest01', name: 'recent project' }])
        if (url === '/api/projects/latest01/settings')
          return jsonResponse({ values: { key0: true } })
        return jsonResponse({ detail: 'not found' }, 404)
      }),
    )
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(1)
    })
    // The latest project's settings were loaded, not empty defaults.
    expect(fetchedUrls).toContain('GET /api/projects/latest01/settings')
    // URL synced so a plain reload reopens the same project.
    expect(window.location.search).toBe('?project=latest01')
  })

  it('root with zero projects creates one instead of leaving saves targetless', async () => {
    window.history.replaceState({}, '', '/')
    const fetchedUrls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        fetchedUrls.push(`${init?.method ?? 'GET'} ${url}`)
        if (url === '/api/schema') return jsonResponse(fakeSchema(1))
        if (url === '/api/projects' && !init?.method) return jsonResponse([])
        if (url === '/api/projects' && init?.method === 'POST')
          return jsonResponse({ id: 'brandnew', name: 'My project' })
        if (url === '/api/projects/brandnew/settings')
          return jsonResponse({ values: { key0: false } })
        return jsonResponse({ detail: 'not found' }, 404)
      }),
    )
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(1)
    })
    expect(fetchedUrls).toContain('POST /api/projects')
    expect(fetchedUrls).toContain('GET /api/projects/brandnew/settings')
    expect(window.location.search).toBe('?project=brandnew')
  })
})
