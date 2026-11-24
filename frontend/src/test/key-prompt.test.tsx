import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { KeyPrompt } from '../components/KeyPrompt'
import { listProjects } from '../lib/api'

afterEach(() => vi.unstubAllGlobals())

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status })

it('prompts once for concurrent 401s, then retries every call', async () => {
  let unlocked = false
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url === '/api/auth/session') {
      unlocked = true
      return json(200, { ok: true })
    }
    return unlocked ? json(200, []) : json(401, { detail: 'missing or invalid access key' })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<KeyPrompt />)
  const calls = Promise.all([listProjects(), listProjects()])
  const input = await screen.findByLabelText('Access key')
  expect(screen.getAllByRole('dialog')).toHaveLength(1)
  await userEvent.type(input, 'k3y')
  await userEvent.click(screen.getByRole('button', { name: 'Unlock' }))
  await expect(calls).resolves.toEqual([[], []])
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const login = fetchMock.mock.calls.find((c) => c[0] === '/api/auth/session')
  expect(JSON.parse(login![1]!.body as string)).toEqual({ key: 'k3y' })
})

it('shows an error on a wrong key and keeps the dialog open', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => json(401, { detail: 'missing or invalid access key' })),
  )
  render(<KeyPrompt />)
  void listProjects().catch(() => {})
  await userEvent.type(await screen.findByLabelText('Access key'), 'bad')
  await userEvent.click(screen.getByRole('button', { name: 'Unlock' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong access key')
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})
