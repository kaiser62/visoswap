/** Access-key dialog. Opens when any API call gets a 401 (see api.ts), logs in
 *  through the cookie endpoint, and lets the waiting calls retry. Mounted once in
 *  main.tsx so it covers both the studio and /mobile. */

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { loginWithKey, subscribeKeyRequest } from '../lib/api'
import { Button, Modal } from './ui'

export function KeyPrompt() {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => subscribeKeyRequest(setOpen), [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!key || busy) return
    setBusy(true)
    setError(null)
    try {
      await loginWithKey(key)
      setKey('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={() => {}} title="Access key required">
      <form onSubmit={submit}>
        <p className="mb-3 text-sm text-muted">
          This server is protected. Enter its access key to continue.
        </p>
        <input
          type="password"
          autoFocus
          autoComplete="current-password"
          aria-label="Access key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          className="mb-2 w-full rounded border border-line bg-raised px-3 py-2 text-sm text-text"
        />
        {error && (
          <p role="alert" className="mb-2 text-sm text-bad">
            {error}
          </p>
        )}
        <div className="flex justify-end">
          <Button type="submit" disabled={!key || busy}>
            {busy ? 'Checking…' : 'Unlock'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
