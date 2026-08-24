/** Recording card (plan 05.1-07 Task 2).
 *
 * The recorder's in-progress `.part` is a valid fragmented mp4 at every
 * instant, so offering it mid-run is safe — what is not safe is calling it
 * finished. A run can stop before the promote happens, so the label follows
 * the backend's `complete` flag and never the running flag.
 *
 * The size is the one field that changes continuously and the backend reports
 * it from the status poll only, so this card polls while a run is active and
 * stops the moment it is not.
 */

import { useEffect, useRef, useState } from 'react'
import { getGenerationStatus, outputUrl } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import { Badge, Card } from './ui'
import type { RecordingInfo } from '../types'

/** Slow enough that a growing file's size is the only thing this costs, fast
 *  enough that the number on screen tracks the recording. */
export const RECORDING_POLL_MS = 2000

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function ResultsCard() {
  const { projectId, recording, running } = useMedia()
  // Seeded and re-seeded from the context's status loads, then advanced by
  // this card's own polls — which is where a failure is visible at all, since
  // the context's advisory refresh swallows it.
  const [info, setInfo] = useState<RecordingInfo | null>(recording)
  const [stale, setStale] = useState(false)
  const runningRef = useRef(running)
  runningRef.current = running

  useEffect(() => {
    if (recording) {
      setInfo(recording)
      setStale(false)
    }
  }, [recording])

  useEffect(() => {
    if (!projectId || !running) return
    const id = setInterval(() => {
      void getGenerationStatus(projectId)
        .then((status) => {
          setInfo(status.recording)
          setStale(false)
        })
        .catch(() => {
          // Keep whatever is on screen: a recording that momentarily fails to
          // report has not vanished, and blanking the card would say it had.
          setStale(true)
        })
    }, RECORDING_POLL_MS)
    return () => clearInterval(id)
  }, [projectId, running])

  const href = projectId ? outputUrl(projectId) : null

  return (
    <Card data-testid="results-card">
      <h3 className="mb-3 text-sm font-medium">Recording</h3>
      {!info?.available || !href ? (
        <p className="text-sm text-muted" data-testid="results-empty">
          Nothing recorded yet — start a run to compose one.
        </p>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-2">
            <span data-testid="results-label">
              <Badge>{info.complete ? 'Finished recording' : 'Recording in progress'}</Badge>
            </span>
            <span className="text-muted tabular-nums" data-testid="results-size">
              {formatBytes(info.bytes)}
            </span>
            {stale && (
              <span className="text-xs text-muted" data-testid="results-stale">
                (last known — the size could not be refreshed)
              </span>
            )}
          </div>

          {/* Both controls point the browser straight at the endpoint, which
            serves the partial and the finished file identically. */}
          <video
            data-testid="results-video"
            src={href}
            controls
            className="w-full rounded border border-line"
          />
          <a
            data-testid="results-download"
            href={href}
            download
            className="inline-block text-xs underline"
          >
            Download {info.complete ? 'the recording' : 'what has been recorded so far'}
          </a>
        </div>
      )}
    </Card>
  )
}
