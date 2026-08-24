/** Transport row: range marks and seeking (plan 05.1-05 Task 3, D-08).
 *
 * Ported from the webui2 viewer card's transport row (set-in, set-out,
 * clear-range, seek-time, seek-frame). Purely presentational — it takes the
 * current time, the marks and callbacks from props, so it is testable with no
 * media element attached.
 */

import { useState } from 'react'

const fmt = (t: number | null) => (t === null ? '—' : t.toFixed(3))

export function Transport({
  marks,
  message,
  onSetStart,
  onSetEnd,
  onClear,
  onSeekSeconds,
  onSeekFrame,
}: {
  marks: { start: number | null; end: number | null }
  message: string | null
  onSetStart: () => void
  onSetEnd: () => void
  onClear: () => void
  onSeekSeconds: (seconds: number) => void
  onSeekFrame: (frame: number) => void
}) {
  const [seconds, setSeconds] = useState('')
  const [frame, setFrame] = useState('')

  const commit = (raw: string, apply: (n: number) => void) => {
    const n = Number(raw)
    if (raw.trim() === '' || !Number.isFinite(n) || n < 0) return
    apply(n)
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
      <button
        type="button"
        data-testid="btn-set-start"
        onClick={onSetStart}
        className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-semibold text-text"
      >
        Set Start
      </button>
      <button
        type="button"
        data-testid="btn-set-end"
        onClick={onSetEnd}
        className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-semibold text-text"
      >
        Set End
      </button>
      <button
        type="button"
        data-testid="btn-clear-range"
        onClick={onClear}
        className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-semibold text-text"
      >
        Clear
      </button>

      <span className="ml-1 text-xs text-muted">
        in <span data-testid="mark-start">{fmt(marks.start)}</span> / out{' '}
        <span data-testid="mark-end">{fmt(marks.end)}</span>
      </span>

      <input
        data-testid="seek-seconds"
        aria-label="Seek to seconds"
        placeholder="seconds"
        value={seconds}
        onChange={(e) => setSeconds(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit(seconds, onSeekSeconds)
        }}
        className="ml-auto w-24 rounded-md border border-line bg-bg px-2 py-1.5 text-xs text-text"
      />
      <input
        data-testid="seek-frame"
        aria-label="Seek to frame number"
        placeholder="frame"
        value={frame}
        onChange={(e) => setFrame(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit(frame, onSeekFrame)
        }}
        className="w-24 rounded-md border border-line bg-bg px-2 py-1.5 text-xs text-text"
      />

      {message !== null && (
        <p role="alert" className="w-full text-xs text-bad">
          {message}
        </p>
      )}
    </div>
  )
}
