/** Processing scale control tests (plan 05.1-08 Task 2, D-15b/D-14).
 *
 * The control is only worth shipping if the chosen value reaches the project
 * row — the generator reads the row, never the click. So these tests assert
 * the PATCH body and the read-back, not the button's own styling.
 *
 * The label is checked verbatim against the rung-b section of
 * docs/benchmark-baseline.md: what the benchmark measured is what the user is
 * told, and a divergence between the two is a lie the tests should catch.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ModeSelector } from '../components/ModeSelector'
import { MediaProvider } from '../state/MediaContext'
import type { Project } from '../types'

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
let patchBodies: Record<string, unknown>[]

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: 'ok', json: async () => body }
}

function installFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/projects/p1' && init?.method === 'PATCH') {
        const patch = JSON.parse(String(init.body)) as Record<string, unknown>
        patchBodies.push(patch)
        serverProject = { ...serverProject, ...patch }
        return jsonResponse(serverProject)
      }
      if (url === '/api/projects/p1') return jsonResponse(serverProject)
      if (url === '/api/projects/p1/frames')
        return jsonResponse({ project_id: 'p1', interval: 1, duration: null, frames: [] })
      if (url === '/api/projects/p1/generation/status')
        return jsonResponse({
          running: false,
          full_video_mode: false,
          counts: {},
          average_duration: null,
          recording: null,
        })
      return jsonResponse({ detail: 'not found' }, 404)
    }),
  )
}

async function mounted() {
  render(
    <MediaProvider projectId="p1">
      <ModeSelector />
    </MediaProvider>,
  )
  await act(async () => {})
  await act(async () => {})
}

beforeEach(() => {
  FakeSocket.instances = []
  patchBodies = []
  serverProject = { id: 'p1', name: 'p', has_video: true, fps: 24 }
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  installFetch()
})

describe('the processing scale control', () => {
  it('persists the chosen scale through the project update endpoint', async () => {
    await mounted()
    fireEvent.click(screen.getByTestId('scale-50'))
    await waitFor(() => expect(patchBodies).toContainEqual({ processing_scale: 0.5 }))
  })

  it('shows the value already stored on the row', async () => {
    serverProject = { ...serverProject, processing_scale: 0.75 }
    await mounted()
    await waitFor(() =>
      expect(screen.getByTestId('scale-75')).toHaveAttribute('aria-checked', 'true'),
    )
    // An unset column is native width, so nothing else may claim to be chosen.
    expect(screen.getByTestId('scale-100')).toHaveAttribute('aria-checked', 'false')
  })

  it('defaults to native width when the row says nothing', async () => {
    await mounted()
    expect(screen.getByTestId('scale-100')).toHaveAttribute('aria-checked', 'true')
  })

  it('describes the effect in the words the benchmark recorded', async () => {
    await mounted()
    const shown = screen.getByTestId('scale-description').textContent ?? ''
    expect(shown).toMatch(/14% faster/)

    // The doc quotes the label as a blockquote wrapped across lines; drop the
    // quote markers and collapse whitespace so a reflow of the markdown is not
    // a test failure, while the words themselves still have to match.
    const here = dirname(fileURLToPath(import.meta.url))
    const doc = readFileSync(resolve(here, '../../../docs/benchmark-baseline.md'), 'utf-8')
      .replace(/^>\s?/gm, '')
      .replace(/\s+/g, ' ')
    expect(doc).toContain(shown.replace(/\s+/g, ' ').trim())
  })
})
