/** The three run modes (plan 05.1-06 Task 3, D-07).
 *
 * Stream-Live is the default; Export resolves its span from the transport
 * marks at start time; Interval reveals an N-seconds field whose value
 * persists through the project update endpoint (the scheduler reads the row,
 * never the start body).
 */

import { useEffect, useState } from 'react'
import { useMedia, type MediaMode } from '../state/MediaContext'

const MODES: { id: MediaMode; label: string }[] = [
  { id: 'live', label: 'Stream-Live' },
  { id: 'interval', label: 'Interval' },
  { id: 'export', label: 'Export' },
]

export function ModeSelector() {
  const { mode, selectMode, project, updateInterval } = useMedia()
  const [intervalText, setIntervalText] = useState('')

  // Track the persisted row value; a blank field means "use the project's".
  useEffect(() => {
    if (project?.interval != null) setIntervalText(String(project.interval))
  }, [project?.interval])

  const commitInterval = () => {
    const n = Number(intervalText)
    if (intervalText.trim() !== '' && Number.isFinite(n) && n > 0) void updateInterval(n)
  }

  return (
    <div className="space-y-2" data-testid="mode-selector">
      <div className="flex flex-wrap items-center gap-1" role="radiogroup" aria-label="Run mode">
        {MODES.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={mode === id}
            data-testid={`mode-${id}`}
            onClick={() => selectMode(id)}
            className={`rounded border px-2.5 py-1 text-xs font-semibold ${
              mode === id
                ? 'border-accent bg-active text-text'
                : 'border-line bg-raised text-muted hover:text-text'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === 'live' && (
        <p className="text-xs text-muted">Every video frame from the playhead forward.</p>
      )}

      {mode === 'interval' && (
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-muted">
            Every
            <input
              data-testid="interval-input"
              type="number"
              min="0.1"
              step="0.1"
              value={intervalText}
              onChange={(e) => setIntervalText(e.target.value)}
              onBlur={commitInterval}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitInterval()
              }}
              aria-label="Interval seconds"
              className="w-20 rounded border border-line bg-bg px-2 py-1 text-xs text-text"
            />
            seconds
          </label>
        </div>
      )}

      {mode === 'export' && (
        <p className="text-xs text-muted">
          Uses the start and end marks from the transport — or the whole video
          when no marks are set.
        </p>
      )}
    </div>
  )
}
