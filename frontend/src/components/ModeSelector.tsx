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

/** Percentages of native width the engine may process. Nothing above 100 —
 *  upscaling before the swap is wasted work, and the generator refuses it. */
const SCALES: { value: number; label: string }[] = [
  { value: 1, label: '100%' },
  { value: 0.75, label: '75%' },
  { value: 0.5, label: '50%' },
]

/** Quoted verbatim from the rung-b section of docs/benchmark-baseline.md.
 *  The number in the label is the one the benchmark produced on this machine,
 *  not a promise: 75% is inside the run-to-run spread and is labelled as such
 *  rather than badged as a speedup that was never measured (D-14). */
const SCALE_DESCRIPTION =
  'Half width measured 14% faster on this machine (17.8 to 21.0 swap fps at 1080p). ' +
  'Three-quarter width was within noise of full. Quality drops with width.'

export function ModeSelector() {
  const { mode, selectMode, project, updateInterval, updateScale } = useMedia()
  const [intervalText, setIntervalText] = useState('')

  // Track the persisted row value; a blank field means "use the project's".
  useEffect(() => {
    if (project?.interval != null) setIntervalText(String(project.interval))
  }, [project?.interval])

  // The row is the truth; an unset column reads as native width.
  const activeScale = project?.processing_scale ?? 1

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

      {/* Independent of the run mode: how much of each frame the engine works
        on. Persisted to the project row, because that row is what the
        generator reads. */}
      <div className="space-y-1 pt-1" data-testid="scale-selector">
        <div
          className="flex flex-wrap items-center gap-1"
          role="radiogroup"
          aria-label="Processing scale"
        >
          {SCALES.map(({ value, label }) => (
            <button
              key={label}
              type="button"
              role="radio"
              aria-checked={activeScale === value}
              data-testid={`scale-${Math.round(value * 100)}`}
              onClick={() => void updateScale(value)}
              className={`rounded border px-2.5 py-1 text-xs font-semibold ${
                activeScale === value
                  ? 'border-accent bg-active text-text'
                  : 'border-line bg-raised text-muted hover:text-text'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted" data-testid="scale-description">
          {SCALE_DESCRIPTION}
        </p>
      </div>
    </div>
  )
}
