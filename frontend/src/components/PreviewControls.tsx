/** The preview control (plan 05.1-06 Task 3, D-09).
 *
 * Manual preview posts the playhead and points the player's overlay at the
 * returned url. Automatic preview subscribes to the active face and the
 * settings values, debounces so a slider drag is ONE request at rest, gates
 * on paused-at-fire-time (a user who hits play mid-debounce must not send a
 * request into a running scheduler), stays off by default, and never runs
 * while a run is active.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, renderPreview } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import { useSettingsOptional } from '../state/SettingsContext'

/** One debounce window for every auto-preview trigger. */
export const AUTO_PREVIEW_DEBOUNCE_MS = 500

const describe = (err: unknown) =>
  err instanceof ApiError || err instanceof Error ? err.message : 'Preview failed'

export function PreviewControls() {
  const {
    projectId,
    running,
    videoPaused,
    automaticPreview,
    sourceFaceId,
    toggleAutoPreview,
    setPreviewUrl,
    getPlayhead,
  } = useMedia()
  const settings = useSettingsOptional()
  const settingsValues = settings?.values

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // The fire closure reads flags at call time through a ref, so re-arming the
  // debounce never needs to depend on them (and cannot resurrect a timer that
  // a gate just cancelled).
  const fireRef = useRef<() => void>(() => {})
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const cancelTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    fireRef.current = () => {
      if (!projectId) return
      // Fire-time gates: a run outranks everything, and only a paused video
      // may generate GPU work — this is the contention the endpoint's 409
      // exists to prevent.
      if (running || !videoPaused) return
      void (async () => {
        setBusy(true)
        setError(null)
        try {
          const resp = await renderPreview(projectId, getPlayhead())
          setPreviewUrl(resp.url)
        } catch (err) {
          setError(describe(err))
        } finally {
          setBusy(false)
        }
      })()
    }
  }, [projectId, running, videoPaused, getPlayhead, setPreviewUrl])

  const armDebounce = useCallback(() => {
    cancelTimer()
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      fireRef.current()
    }, AUTO_PREVIEW_DEBOUNCE_MS)
  }, [cancelTimer])

  useEffect(() => () => cancelTimer(), [cancelTimer])

  // Auto-preview triggers. Subscribing arms one debounce; rapid re-triggers
  // within the window collapse into a single request at rest.
  useEffect(() => {
    if (!automaticPreview || running || !videoPaused) {
      cancelTimer()
      return
    }
    armDebounce()
    return cancelTimer
  }, [
    automaticPreview,
    running,
    videoPaused,
    sourceFaceId,
    settingsValues,
    armDebounce,
    cancelTimer,
  ])

  return (
    <div className="flex flex-wrap items-center gap-3" data-testid="preview-controls">
      <button
        type="button"
        data-testid="preview-button"
        onClick={() => fireRef.current()}
        disabled={busy || running}
        className="rounded border border-line bg-raised px-2.5 py-1.5 text-xs font-semibold text-text hover:bg-active disabled:pointer-events-none disabled:opacity-50"
      >
        {busy ? 'Rendering…' : 'Preview'}
      </button>
      <label className="flex items-center gap-1.5 text-xs text-muted">
        <input
          type="checkbox"
          data-testid="auto-preview-toggle"
          aria-label="Automatic preview"
          checked={automaticPreview}
          onChange={toggleAutoPreview}
          className="h-3.5 w-3.5 accent-accent"
        />
        Auto
      </label>
      {error && (
        <p role="alert" className="text-xs text-bad" data-testid="preview-error">
          {error}
        </p>
      )}
    </div>
  )
}
