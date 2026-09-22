import { useState } from 'react'
import { retryFailed } from '../lib/api'
import { useMedia } from '../state/MediaContext'

export function MobileQueueView() {
  const {
    projectId,
    counts,
    coverage,
    averageDuration,
    inFlight,
    project,
    running,
  } = useMedia()

  const [retrying, setRetrying] = useState(false)
  const [retryMessage, setRetryMessage] = useState<string | null>(null)

  const measured = coverage.lookups > 0
  const coveragePercent = measured
    ? Math.round((coverage.hits / coverage.lookups) * 100)
    : null

  const swapFps =
    averageDuration !== null && averageDuration > 0 ? (1 / averageDuration).toFixed(1) : null

  const handleRetry = async () => {
    if (!projectId) return
    setRetrying(true)
    setRetryMessage(null)
    try {
      const res = await retryFailed(projectId)
      setRetryMessage(`Requeued ${res.requeued} failed frame(s).`)
    } catch (err) {
      setRetryMessage(err instanceof Error ? err.message : 'Retry failed')
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className="space-y-4 p-4 pb-28">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-text">Generation Queue</h2>
          <p className="text-xs text-muted">Real-time inference and frame swap status</p>
        </div>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
            running ? 'bg-accent/15 text-accent animate-pulse' : 'bg-raised text-muted'
          }`}
        >
          {running ? 'Worker Active' : 'Idle'}
        </span>
      </div>

      {/* 4 Counter Metric Tiles */}
      <div className="grid grid-cols-2 gap-2.5">
        <div className="rounded-2xl border border-line bg-card p-3.5">
          <span className="block text-xs text-muted">Completed</span>
          <span className="mt-1 block text-2xl font-bold text-good tabular-nums">
            {counts.completed}
          </span>
          <span className="block text-[10px] text-muted">Generated frames</span>
        </div>

        <div className="rounded-2xl border border-line bg-card p-3.5">
          <span className="block text-xs text-muted">Pending</span>
          <span className="mt-1 block text-2xl font-bold text-accent tabular-nums">
            {counts.pending}
          </span>
          <span className="block text-[10px] text-muted">Awaiting swap</span>
        </div>

        <div className="rounded-2xl border border-line bg-card p-3.5">
          <span className="block text-xs text-muted">Processing</span>
          <span className="mt-1 block text-2xl font-bold text-text tabular-nums">
            {counts.processing}
          </span>
          <span className="block text-[10px] text-muted">In GPU workers</span>
        </div>

        <div className="rounded-2xl border border-line bg-card p-3.5">
          <span className="block text-xs text-muted">Failed</span>
          <span className={`mt-1 block text-2xl font-bold tabular-nums ${counts.failed > 0 ? 'text-bad' : 'text-muted'}`}>
            {counts.failed}
          </span>
          <span className="block text-[10px] text-muted">Retryable errors</span>
        </div>
      </div>

      {/* Retry Failed Frame Action */}
      {counts.failed > 0 && (
        <div className="flex items-center justify-between rounded-2xl border border-bad/30 bg-bad/10 p-3.5">
          <div>
            <span className="block text-xs font-semibold text-bad">
              {counts.failed} failed frame{counts.failed === 1 ? '' : 's'}
            </span>
            <span className="block text-[10px] text-muted">Re-queue all failed frames</span>
          </div>
          <button
            type="button"
            onClick={handleRetry}
            disabled={retrying}
            className="rounded-xl bg-bad px-3.5 py-1.5 text-xs font-bold text-white shadow-sm active:opacity-90 disabled:opacity-50"
          >
            {retrying ? 'Retrying…' : 'Retry Failed'}
          </button>
        </div>
      )}

      {retryMessage && (
        <p className="rounded-xl bg-raised p-3 text-xs text-accent">{retryMessage}</p>
      )}

      {/* Performance & Cadence Panel */}
      <div className="rounded-2xl border border-line bg-card p-4 space-y-3">
        <h3 className="text-xs font-semibold text-text">Performance Metrics</h3>

        <div className="space-y-2 text-xs">
          <div className="flex justify-between border-b border-line/40 pb-2">
            <span className="text-muted">Overlay Coverage:</span>
            <span className="font-semibold text-text tabular-nums">
              {coveragePercent !== null
                ? `${coveragePercent}% (${coverage.hits}/${coverage.lookups})`
                : 'Measuring…'}
            </span>
          </div>

          <div className="flex justify-between border-b border-line/40 pb-2">
            <span className="text-muted">Swap Throughput:</span>
            <span className="font-semibold text-text tabular-nums">
              {swapFps ? `${swapFps} fps` : 'Not yet reported'}
            </span>
          </div>

          <div className="flex justify-between border-b border-line/40 pb-2">
            <span className="text-muted">Target Video Rate:</span>
            <span className="font-semibold text-text tabular-nums">
              {project?.fps ? `${project.fps} fps` : 'Unknown'}
            </span>
          </div>

          <div className="flex justify-between">
            <span className="text-muted">In-flight Frames:</span>
            <span className="font-semibold text-text tabular-nums">
              {inFlight.length > 0 ? inFlight.join(', ') : 'None'}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
