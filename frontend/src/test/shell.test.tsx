/** Studio shell gate (plan 05.1-04): one page at `/`, three cards, and D-02's
 * mount discipline — collapsing the Controls card hides it with CSS and never
 * unmounts a schema control, so the `[data-key]` count is identical open or
 * collapsed. This file is the only place that discipline is mechanically
 * checked across a collapse click.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import schemaJson from '../../../visoswap/schema/schema.json'

interface Entry {
  type: string
  tier: string
  group: string
  label: string
  options?: string[] | null
  default?: unknown
}

const widgets = schemaJson.widgets as Record<string, Entry>
const keys = Object.keys(widgets)

/** Same stub shape as render-count.test.tsx: three URLs answered, 404 else. */
function stubFetch() {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/schema') {
      return jsonResponse(schemaDoc())
    }
    if (url.startsWith('/api/projects/t/settings')) {
      return jsonResponse(valuesDoc())
    }
    if (url === '/api/projects') {
      return jsonResponse([{ id: 't', name: 'test' }])
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function schemaDoc() {
  const resolved: Record<string, Entry> = {}
  for (const [k, e] of Object.entries(widgets)) {
    resolved[k] = { ...e, options: e.options ?? [] }
  }
  return { schema: schemaJson.schema, widgets: resolved }
}

function valuesDoc() {
  const values: Record<string, unknown> = {}
  for (const [k, e] of Object.entries(widgets)) {
    values[k] = e.default ?? ''
  }
  return { values }
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'ok',
    json: async () => body,
  }
}

beforeEach(() => {
  stubFetch()
  Object.defineProperty(window, 'location', {
    value: { search: '?project=t' },
    writable: true,
  })
})

describe('studio shell', () => {
  it('one page shows the Player, Media and Controls card headings together', async () => {
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(keys.length)
    })
    expect(screen.getByRole('heading', { name: 'Player' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Media' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Controls' })).toBeTruthy()
  })

  it('renders every schema control, and a Controls-card collapse unmounts none of them', async () => {
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(keys.length)
    })

    // Before the collapse: exactly one control per schema key.
    expect(document.querySelectorAll('[data-key]').length).toBe(keys.length)

    // Collapse the Controls card by clicking its header.
    await user.click(screen.getByRole('heading', { name: 'Controls' }))

    // After the collapse: still exactly one control per schema key — the card
    // hid with CSS, nothing left the tree.
    expect(document.querySelectorAll('[data-key]').length).toBe(keys.length)
  })

  it('the settings sidebar keeps its accessible name inside the Controls card', async () => {
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(keys.length)
    })
    const sidebar = screen.getByRole('navigation', {
      name: 'Settings sections',
    })
    expect(sidebar).toBeTruthy()

    // Even collapsed, the nav stays in the DOM — hidden, not unmounted.
    await user.click(screen.getByRole('heading', { name: 'Controls' }))
    expect(
      screen.getByRole('navigation', { name: 'Settings sections', hidden: true }),
    ).toBeTruthy()
  })
})
