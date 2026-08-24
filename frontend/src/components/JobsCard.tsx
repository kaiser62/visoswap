/** Queue and cadence panel (plan 05.1-07 Task 1, D-10/D-15d).
 *
 * Everything here is honest about what it does and does not know. Coverage is
 * a measurement the player takes on the overlay lookups it already performs —
 * before any lookup happens there is no ratio to show, so the card says so
 * instead of printing a fabricated 0%. The generation rate comes from the
 * backend's reported average duration; absent that, there is no rate.
 *
 * The queue counts arrive over the socket, so this card never polls.
 */

import { useState } from 'react'
import { retryFailed } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import { Button, Card } from './ui'

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div
      className="flex items-baseline justify-between"
      data-testid={`jobs-count-${label.toLowerCase()}`}
    >
      <span className="text-muted">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  )
}

export function JobsCard() {
  const { projectId, counts, coverage, averageDuration, inFlight, project, running } = useMedia()
  const [retrying, setRetrying] = useState(false)
  const [retryResult, setRetryResult] = useState<string | null>(null)

  const measured = coverage.lookups > 0
  // Rounded for display only; the raw counts are shown beside it so the figure
  // can always be checked against what was actually measured.
  const percent = measured ? Math.round((coverage.hits / coverage.lookups) * 100) : null
  const generatedPerSecond =
    averageDuration !== null && averageDuration > 0 ? 1 / averageDuration : null
  const idle =
    !running &&
    inFlight.length === 0 &&
    counts.pending === 0 &&
    counts.processing === 0 &&
    counts.completed === 0 &&
    counts.failed === 0 &&
    counts.cancelled === 0

  const handleRetry = async () => {
    if (!projectId) return
    setRetrying(true)
    setRetryResult(null)
    try {
      const result = await retryFailed(projectId)
      setRetryResult(`Requeued ${result.requeued} failed frame${result.requeued === 1 ? '' : 's'}.`)
    } catch (err) {
      setRetryResult(err instanceof Error ? err.message : 'Retry failed')
    } finally {
      setRetrying(false)
    }
  }

  return (
    <Card data-testid="jobs-card">
      <h3 className="mb-3 text-sm font-medium">Generation</h3>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
        <Count label="Pending" value={counts.pending} />
        <Count label="Processing" value={counts.processing} />
        <Count label="Completed" value={counts.completed} />
        <Count label="Failed" value={counts.failed} />
        <Count label="Cancelled" value={counts.cancelled} />
      </div>

      <div className="mt-4 space-y-1 text-sm">
        <p data-testid="jobs-coverage">
          {measured
            ? `Overlay coverage: ${percent}%`
            : 'Overlay coverage: not yet measured — play the video to sample it.'}
        </p>
        {measured && (
          <p className="text-xs text-muted" data-testid="jobs-coverage-counts">
            {coverage.hits} of {coverage.lookups} playhead lookups found a generated frame.
          </p>
        )}
        <p className="text-xs text-muted" data-testid="jobs-rate">
          {generatedPerSecond !== null
            ? `Generating about ${generatedPerSecond.toFixed(1)} frames per second`
            : 'Generation rate: not yet reported'}
        </p>
        <p className="text-xs text-muted" data-testid="jobs-fps">
          Video plays at {project?.fps ?? '—'} fps
        </p>
        <p className="text-xs text-muted" data-testid="jobs-policy">
          The overlay shows the nearest previous generated frame, and never a future one —
          playback never waits for generation.
        </p>
      </div>

      {inFlight.length > 0 && (
        <ul className="mt-4 space-y-1 text-xs" data-testid="jobs-inflight">
          {/* Newest first: the frame that just entered the worker is the one
            the user is most likely waiting on. */}
          {[...inFlight].reverse().map((t) => (
            <li key={t} data-testid="jobs-inflight-item">
              Generating frame at {t}s
            </li>
          ))}
        </ul>
      )}

      {counts.failed > 0 && (
        <div className="mt-4 space-y-2">
          <Button onClick={() => void handleRetry()} disabled={retrying} data-testid="jobs-retry">
            {retrying ? 'Requeueing…' : `Retry ${counts.failed} failed`}
          </Button>
          {retryResult && (
            <p className="text-xs text-muted" data-testid="jobs-retry-result">
              {retryResult}
            </p>
          )}
        </div>
      )}

      {idle && (
        <p className="mt-4 text-xs text-muted" data-testid="jobs-idle">
          No run yet — nothing queued, nothing generating.
        </p>
      )}
    </Card>
  )
}
