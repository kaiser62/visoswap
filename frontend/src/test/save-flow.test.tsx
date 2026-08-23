/** Save/discard flow integration tests (D-02, UI-SPEC) with stubbed fetch. */

import { render, screen, waitFor, within } from '@testing-library/react'
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

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'ok',
    json: async () => body,
  }
}

// A project-tier toggle key from the real schema.
const TOGGLE_KEY = Object.keys(widgets).find(
  (k) => widgets[k].type === 'toggle' && widgets[k].tier === 'project',
)!

beforeEach(() => {
  Object.defineProperty(window, 'location', {
    value: { search: '?project=t' },
    writable: true,
  })
})

function stubWithPut(onPut: (body: unknown) => void, putStatus = 200) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init && init.method === 'PUT') {
      onPut(JSON.parse(String(init.body)))
      return jsonResponse({ values: defaultValues() }, putStatus)
    }
    if (url === '/api/schema') return jsonResponse(fakeSchema())
    if (url === '/api/projects/t/settings') return jsonResponse({ values: defaultValues() })
    if (url === '/api/presets') return jsonResponse({ presets: [] })
    if (url === '/api/projects') return jsonResponse([{ id: 't', name: 'test' }])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('save flow', () => {
  it('changes a control, shows Save Changes (1), PUT body has exactly that key, dirty clears', async () => {
    const user = userEvent.setup()
    const putBodies: unknown[] = []
    stubWithPut((body) => putBodies.push(body))

    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    // The Save button is disabled at zero dirty.
    const saveBtn = screen.getByRole('button', { name: /Save Changes \(0\)/ })
    expect(saveBtn).toBeDisabled()

    // Toggle a project-tier control.
    const ctl = document.querySelector(`[data-key="${TOGGLE_KEY}"]`)
    await user.click(ctl as Element)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes \(1\)/ })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: /Save Changes \(1\)/ }))

    await waitFor(() => {
      expect(putBodies.length).toBe(1)
    })
    const overrides = (putBodies[0] as { overrides: Record<string, unknown> }).overrides
    expect(Object.keys(overrides)).toEqual([TOGGLE_KEY])

    // Dirty cleared -> button disabled again.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes \(0\)/ })).toBeDisabled()
    })
  })

  it('save failure keeps edits and shows the documented error banner', async () => {
    const user = userEvent.setup()
    stubWithPut(() => undefined, 500)

    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    await user.click(document.querySelector(`[data-key="${TOGGLE_KEY}"]`) as Element)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes \(1\)/ })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: /Save Changes \(1\)/ }))

    await waitFor(() => {
      expect(screen.getByText(/Couldn't save your changes/)).toBeTruthy()
    })
    // Dirty retained -> button still enabled.
    expect(screen.getByRole('button', { name: /Save Changes \(1\)/ })).toBeEnabled()
  })

  it('Discard requires confirmation, Cancel changes nothing', async () => {
    const user = userEvent.setup()
    stubWithPut(() => undefined)

    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    await user.click(document.querySelector(`[data-key="${TOGGLE_KEY}"]`) as Element)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes \(1\)/ })).toBeEnabled()
    })

    await user.click(screen.getByRole('button', { name: /Discard/ }))
    await waitFor(() => {
      expect(screen.getByText(/This discards 1 unsaved change/)).toBeTruthy()
    })
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    // Still dirty after cancel.
    expect(screen.getByRole('button', { name: /Save Changes \(1\)/ })).toBeEnabled()
  })

  it('Discard confirm reverts values and clears dirty', async () => {
    const user = userEvent.setup()
    stubWithPut(() => undefined)

    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    const ctl = document.querySelector(`[data-key="${TOGGLE_KEY}"]`) as HTMLInputElement
    const original = ctl.checked
    await user.click(ctl)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes \(1\)/ })).toBeEnabled()
    })

    await user.click(screen.getByRole('button', { name: /Discard/ }))
    await waitFor(() => {
      expect(screen.getByText(/This discards 1 unsaved change/)).toBeTruthy()
    })
    // Scope the confirm to the dialog — the header also has a Discard button.
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Discard' }))

    await waitFor(() => {
      const ctl2 = document.querySelector(
        `[data-key="${TOGGLE_KEY}"]`,
      ) as HTMLInputElement
      expect(ctl2.checked).toBe(original)
    })
    expect(screen.getByRole('button', { name: /Save Changes \(0\)/ })).toBeDisabled()
  })

  it('typed out-of-bounds numbers are clamped and never ship null', async () => {
    const user = userEvent.setup()
    const putBodies: Array<{ overrides: Record<string, unknown> }> = []
    stubWithPut((body) => putBodies.push(body as { overrides: Record<string, unknown> }))

    render(<App />)
    await waitFor(() => {
      expect(document.querySelectorAll('[data-key]').length).toBeGreaterThan(0)
    })

    // ColorBrightnessDecimalSlider: project-tier float, min 0 max 2, gated by
    // the project-tier toggle ColorEnableToggle (default false) — flip the
    // parent first so the control unlocks and can be edited.
    const labeled = screen.getAllByLabelText('Brightness') as HTMLInputElement[]
    const numberInput = labeled.find((i) => i.type === 'number')
    expect(numberInput).toBeTruthy()
    expect(numberInput!.type).toBe('number')
    expect(numberInput!.disabled).toBe(true)

    const parentToggle = document.querySelector(
      '[data-key="ColorEnableToggle"]',
    ) as HTMLInputElement
    await user.click(parentToggle)
    await waitFor(() => {
      expect(
        (screen.getAllByLabelText('Brightness') as HTMLInputElement[]).find(
          (i) => i.type === 'number',
        )!.disabled,
      ).toBe(false)
    })

    // Hand-typed values bypass the input's max attribute — the client must
    // clamp, because the store rejects out-of-bounds values with 400.
    await user.clear(numberInput!)
    await user.type(numberInput!, '999')

    // A cleared field must not ship NaN -> JSON null (the API 422s null):
    // clearing emits no change, so the last valid typed value stays dirty.
    await user.clear(numberInput!)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes \(\d+\)/ })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: /Save Changes \(\d+\)/ }))
    await waitFor(() => {
      expect(putBodies.length).toBe(1)
    })
    const overrides = putBodies[0].overrides
    // Clamped to the schema maximum of 2 despite typing 999.
    expect(overrides.ColorBrightnessDecimalSlider).toBe(2)
    // The flipped gate parent persists with it.
    expect(overrides.ColorEnableToggle).toBe(true)
    // Nothing null/undefined ever leaves the client.
    for (const v of Object.values(overrides)) {
      expect(v).not.toBeNull()
      expect(v).not.toBeUndefined()
    }
  })
})
