/** FRONTEND-01 criterion-1 gate: rendered controls == schema key count,
 * every control type matches its entry type, and SchemaControl contains none
 * of the 201 key strings (no manual per-key mapping).
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { cwd } from 'node:process'
import { render, screen, waitFor } from '@testing-library/react'
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

/** Build a schema document for fetch, resolving null DFM options to []. */
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

describe('render count and type fidelity', () => {
  it('renders exactly as many controls as schema keys, each with matching type', async () => {
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBe(keys.length)
    })
    const controls = Array.from(document.querySelectorAll('[data-control-type]'))
    expect(controls.length).toBe(keys.length)
    for (const el of controls) {
      const key = el.getAttribute('data-key')
      const expectedType = key ? widgets[key]?.type : undefined
      expect(el.getAttribute('data-control-type')).toBe(expectedType)
    }
  })

  it('SchemaControl source contains none of the 201 key strings', () => {
    const src = readFileSync(
      join(cwd(), 'src/components/SchemaControl.tsx'),
      'utf-8',
    )
    for (const key of keys) {
      expect(src).not.toContain(key)
    }
  })
})

describe('pathological keys render the right primitive', () => {
  it('ClipText renders a text input, not a slider or numeric parse', async () => {
    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key="ClipText"]').length).toBe(1)
    })
    const el = document.querySelector('[data-key="ClipText"]')
    expect(el?.getAttribute('type')).toBe('text')
  })

  it('VideoPlaybackCustomFpsSlider renders an int range honoring min/max/step', async () => {
    render(<App />)
    await waitFor(() => {
      expect(
        document.querySelectorAll('[data-key="VideoPlaybackCustomFpsSlider"]')
          .length,
      ).toBe(1)
    })
    const el = document.querySelector('[data-key="VideoPlaybackCustomFpsSlider"]')
    expect(el?.getAttribute('type')).toBe('range')
    expect(el?.getAttribute('min')).toBe('1')
    expect(el?.getAttribute('max')).toBe('120')
    expect(el?.getAttribute('step')).toBe('1')
  })

  it('ColorBrightnessDecimalSlider renders a float range honoring step 0.01', async () => {
    render(<App />)
    await waitFor(() => {
      expect(
        document.querySelectorAll('[data-key="ColorBrightnessDecimalSlider"]')
          .length,
      ).toBe(1)
    })
    const el = document.querySelector(
      '[data-key="ColorBrightnessDecimalSlider"]',
    )
    expect(el?.getAttribute('type')).toBe('range')
    expect(el?.getAttribute('step')).toBe('0.01')
  })

  it('WebCamMaxFPSSelection renders a select with string option values', async () => {
    render(<App />)
    await waitFor(() => {
      expect(
        document.querySelectorAll('[data-key="WebCamMaxFPSSelection"]').length,
      ).toBe(1)
    })
    const el = document.querySelector('[data-key="WebCamMaxFPSSelection"]')
    expect(el?.tagName.toLowerCase()).toBe('select')
    const options = Array.from(el?.querySelectorAll('option') ?? [])
    const values = options.map((o) => o.getAttribute('value'))
    expect(values).toEqual(['23', '30', '60'])
  })

  it('DFMModelSelection renders a disabled select with an unavailable hint when options are empty', async () => {
    render(<App />)
    await waitFor(() => {
      expect(
        document.querySelectorAll('[data-key="DFMModelSelection"]').length,
      ).toBe(1)
    })
    const el = document.querySelector('[data-key="DFMModelSelection"]')
    expect(el?.tagName.toLowerCase()).toBe('select')
    expect(el).toHaveAttribute('disabled')
    expect(screen.getByText('Unavailable')).toBeTruthy()
  })
})
