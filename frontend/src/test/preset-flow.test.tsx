/** Preset apply flow integration tests (D-04, UI-SPEC) with stubbed fetch. */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import schemaJson from '../../../visoswap/schema/schema.json'

const widgets = schemaJson.widgets as Record<
  string,
  { type: string; tier: string; group: string; label: string; default?: unknown; options?: string[] | null }
>

function fakeSchema() {
  const resolved: Record<string, unknown> = {}
  for (const [k, e] of Object.entries(widgets)) {
    resolved[k] = { ...e, options: e.options ?? [] }
  }
  return { schema: schemaJson.schema, widgets: resolved }
}

function defaultValues() {
  const values: Record<string, unknown> = {}
  for (const [k, e] of Object.entries(widgets)) {
    values[k] = e.default ?? ''
  }
  return values
}

// The two real preset payload shapes: a handful of project keys differing from
// defaults, plus a global tier.
const PRESET_A = {
  id: 'a'.repeat(32),
  name: 'A',
  project: { KeyA: 1, KeyB: true },
  global: { GlobalA: 'x' },
  created_at: '2025-01-01 00:00:00',
  updated_at: '2025-01-01 00:00:00',
}

const PRESET_WITH_AUD = {
  id: 'b'.repeat(32),
  name: 'with AUD',
  project: { KeyA: 2 },
  global: { GlobalB: false },
  created_at: '2025-01-01 00:00:00',
  updated_at: '2025-01-01 00:00:00',
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'ok',
    json: async () => body,
  }
}

function stubWithPresets(onApply?: (url: string) => void, applyStatus = 200) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init && init.method === 'POST') {
      onApply?.(url)
      return jsonResponse(
        {
          report: {
            project_written: 2,
            project_cleared: 0,
            global_written: 0,
            global_cleared: 0,
            unavailable: [],
          },
          values: { ...defaultValues(), KeyA: 1, KeyB: true },
        },
        applyStatus,
      )
    }
    if (url === '/api/schema') return jsonResponse(fakeSchema())
    if (url === '/api/projects/t/settings') return jsonResponse({ values: defaultValues() })
    if (url === '/api/presets') return jsonResponse({ presets: [PRESET_A, PRESET_WITH_AUD] })
    if (url === '/api/projects') return jsonResponse([{ id: 't', name: 'test' }])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => {
  Object.defineProperty(window, 'location', {
    value: { search: '?project=t' },
    writable: true,
  })
})

describe('preset flow', () => {
  it('selecting a preset opens the diff modal with the changed-key list', async () => {
    const user = userEvent.setup()
    stubWithPresets()
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    await user.selectOptions(screen.getByLabelText('Preset'), PRESET_A.id)
    await waitFor(() => {
      expect(screen.getByText('Apply preset A')).toBeTruthy()
    })
    expect(screen.getByText(/Changes 2 setting\(s\)/)).toBeTruthy()
    expect(screen.getByText('KeyA')).toBeTruthy()
    expect(screen.getByText('KeyB')).toBeTruthy()
  })

  it('Cancel issues no request and closes', async () => {
    const user = userEvent.setup()
    const applied = vi.fn()
    stubWithPresets(applied)
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    await user.selectOptions(screen.getByLabelText('Preset'), PRESET_WITH_AUD.id)
    await waitFor(() => {
      expect(screen.getByText('Apply preset with AUD')).toBeTruthy()
    })
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.queryByText('Apply preset with AUD')).toBeNull()
    })
    expect(applied).not.toHaveBeenCalled()
  })

  it('Apply posts the preset id and replaces values', async () => {
    const user = userEvent.setup()
    const applied = vi.fn()
    stubWithPresets(applied)
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    await user.selectOptions(screen.getByLabelText('Preset'), PRESET_A.id)
    await waitFor(() => {
      expect(screen.getByText('Apply preset A')).toBeTruthy()
    })
    await user.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() => {
      expect(applied).toHaveBeenCalled()
    })
    expect(applied.mock.calls[0][0]).toContain(`/presets/${PRESET_A.id}`)
    // Modal closes after success.
    await waitFor(() => {
      expect(screen.queryByText('Apply preset A')).toBeNull()
    })
  })

  it('apply failure keeps the modal open with an error and no value change', async () => {
    const user = userEvent.setup()
    stubWithPresets(undefined, 500)
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    await user.selectOptions(screen.getByLabelText('Preset'), PRESET_A.id)
    await waitFor(() => {
      expect(screen.getByText('Apply preset A')).toBeTruthy()
    })
    await user.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() => {
      expect(screen.getByText(/Couldn't apply the preset/)).toBeTruthy()
    })
    // Modal still open.
    expect(screen.getByText('Apply preset A')).toBeTruthy()
  })

  it('zero presets renders the disabled selector', async () => {
    stubWithPresets()
    // Override the presets fetch to return none.
    const orig = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    orig.mockImplementation(async (url: string) => {
      if (url === '/api/presets') return jsonResponse({ presets: [] })
      if (url === '/api/schema') return jsonResponse(fakeSchema())
      if (url === '/api/projects/t/settings') return jsonResponse({ values: defaultValues() })
      if (url === '/api/projects') return jsonResponse([{ id: 't', name: 'test' }])
      return jsonResponse({ detail: 'not found' }, 404)
    })
    render(<App />)
    await waitFor(() => {
      expect(screen.getByText('No presets')).toBeTruthy()
    })
    expect(screen.getByLabelText('Preset')).toBeDisabled()
  })
})
