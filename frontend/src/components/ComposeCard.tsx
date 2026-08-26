/** The compose queue panel.
 *
 * Every stop queues an offline compose — a second pass that re-encodes the
 * generated span with *every* generated frame in it, where the live recording
 * only holds what the writer could commit before each frame's deadline passed.
 *
 * That is work the user did not explicitly ask for, and a full re-encode is not
 * cheap, so it has to be visible and it has to be stoppable. This panel is both:
 * it shows the queue (which is serial and global, so a job can be waiting on
 * another project's), and every job in it can be called off, waiting or
 * already running.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  cancelComposeJob,
  composeProject,
  forgetComposeJob,
  listComposeJobs,
  takeUrl,
} from '../lib/api'
import { useMedia } from '../state/MediaContext'
import { Badge, Card } from './ui'
import type { ComposeJob, ComposeJobState } from '../types'

/** While something is going, the progress number is the point of the panel. */
export const COMPOSE_ACTIVE_POLL_MS = 1000
/** Idle, the panel is only watching for a job a *stop* queued, which the card
 *  has no other way of hearing about. Slow enough to cost nothing. */
export const COMPOSE_IDLE_POLL_MS = 5000

const LABELS: Record<ComposeJobState, string> = {
  pending: 'Waiting',
  running: 'Composing',
  done: 'Composed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  empty: 'Nothing to compose',
}

function isActive(job: ComposeJob): boolean {
  return job.state === 'pending' || job.state === 'running'
}

function progress(job: ComposeJob): string {
  if (job.total_frames <= 0) return ''
  const percent = Math.min(100, Math.round((job.frames_written / job.total_frames) * 100))
  return `${percent}% — ${job.frames_written} / ${job.total_frames} frames`
}

export function ComposeCard() {
  const { projectId, running } = useMedia()
  const [jobs, setJobs] = useState<ComposeJob[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Drives the poll rate. A boolean rather than the job list itself, so the
  // interval is torn down and rebuilt when the queue goes idle or busy — not on
  // every progress tick.
  const active = jobs.some(isActive)

  const refresh = useCallback(async () => {
    try {
      setJobs(await listComposeJobs())
      setError(null)
    } catch (err) {
      // Keep the rows on screen. A list that momentarily fails to load has not
      // become empty, and blanking it would say the queue had drained.
      setError(err instanceof Error ? err.message : 'Could not read the compose queue.')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const id = setInterval(
      () => void refresh(),
      active ? COMPOSE_ACTIVE_POLL_MS : COMPOSE_IDLE_POLL_MS,
    )
    return () => clearInterval(id)
  }, [active, refresh])

  async function act(work: () => Promise<unknown>) {
    setBusy(true)
    try {
      await work()
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card data-testid="compose-card">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium">Compose queue</h3>
        <button
          type="button"
          data-testid="compose-now"
          onClick={() => projectId && void act(() => composeProject(projectId))}
          disabled={busy || running || !projectId}
          title={
            running
              ? 'Stop the run before composing it — the frames are still being written.'
              : 'Compose every generated frame into one take, with its audio.'
          }
          className="text-xs underline disabled:cursor-not-allowed disabled:opacity-40"
        >
          Compose now
        </button>
      </div>

      {error && (
        <p className="mb-2 text-xs text-muted" data-testid="compose-error">
          {error}
        </p>
      )}

      {jobs.length === 0 ? (
        <p className="text-sm text-muted" data-testid="compose-empty">
          No compose jobs. Stopping a run queues one.
        </p>
      ) : (
        <ul className="space-y-2 text-sm" data-testid="compose-jobs">
          {jobs.map((job) => (
            <li
              key={job.id}
              data-testid={`compose-job-${job.id}`}
              className="flex items-center gap-2 border-b border-line pb-2 last:border-0"
            >
              <Badge>{LABELS[job.state]}</Badge>
              <span className="min-w-0 flex-1 truncate">
                <span className="text-muted">{job.project_name || job.project_id}</span>
                {job.state === 'running' && (
                  <span className="ml-2 tabular-nums text-xs text-muted">
                    {progress(job)}
                  </span>
                )}
                {job.state === 'done' && job.output_name && (
                  <a
                    className="ml-2 text-xs underline"
                    href={takeUrl(job.output_name)}
                    data-testid="compose-take"
                  >
                    {job.output_name}
                  </a>
                )}
                {job.state === 'failed' && job.error && (
                  <span className="ml-2 text-xs text-muted">{job.error}</span>
                )}
              </span>
              {isActive(job) ? (
                <button
                  type="button"
                  data-testid="compose-cancel"
                  onClick={() => void act(() => cancelComposeJob(job.id))}
                  disabled={busy}
                  className="text-xs underline disabled:opacity-40"
                >
                  Cancel
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="compose-clear"
                  onClick={() => void act(() => forgetComposeJob(job.id))}
                  disabled={busy}
                  className="text-xs underline disabled:opacity-40"
                >
                  Clear
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
