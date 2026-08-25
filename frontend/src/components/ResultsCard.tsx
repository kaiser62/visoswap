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
import { getGenerationStatus, outputUrl, releaseRecording } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import { Badge, Card } from './ui'
import type { RecordingInfo } from '../types'

/** Slow enough that a growing file's size is the only thing this costs, fast
 *  enough that the number on screen tracks the recording. */
export const RECORDING_POLL_MS = 2000

/** Grace between unmounting the player and asking the server to delete the
 *  file. The `<video>` element holds an open response, and the handle only
 *  goes when the browser tears that connection down — which it does on its own
 *  clock, not React's. Short enough not to feel like a wait. */
export const RELEASE_SETTLE_MS = 250

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
  // Set the instant Release is pressed: unmounting the player is the release,
  // the server call only sweeps what is left behind.
  const [detached, setDetached] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const [releaseNote, setReleaseNote] = useState<string | null>(null)
  const runningRef = useRef(running)
  runningRef.current = running

  async function onRelease() {
    if (!projectId) return
    setReleasing(true)
    setReleaseNote(null)
    setDetached(true)
    try {
      await new Promise((resolve) => setTimeout(resolve, RELEASE_SETTLE_MS))
      const result = await releaseRecording(projectId)
      setInfo(result.recording)
      setReleaseNote(
        result.held.length > 0
          ? `Still open elsewhere: ${result.held.join(', ')}. Close whatever is ` +
            'playing it and press Release again.'
          : 'Released. The next run starts from a clean recording.',
      )
    } catch (error) {
      setDetached(false)
      setReleaseNote(
        error instanceof Error ? error.message : 'Could not release the recording.',
      )
    } finally {
      setReleasing(false)
    }
  }

  useEffect(() => {
    if (recording) {
      setInfo(recording)
      setStale(false)
      // A recording that is available again is a new run's: take the player
      // back, and drop the note about the one before it.
      if (recording.available) {
        setDetached(false)
        setReleaseNote(null)
      }
    }
  }, [recording])

  useEffect(() => {
    if (!projectId || !running) return
    const id = setInterval(() => {
      void getGenerationStatus(projectId)
        .then((status) => {
          setInfo(status.recording)
          setStale(false)
          // A run recording again means this is a new file, not the one that
          // was released: take the player back.
          if (status.recording.available) {
            setDetached(false)
            setReleaseNote(null)
          }
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
        <div className="space-y-2">
          <p className="text-sm text-muted" data-testid="results-empty">
            Nothing recorded yet — start a run to compose one.
          </p>
          {releaseNote && (
            <p className="text-xs text-muted" data-testid="results-release-note">
              {releaseNote}
            </p>
          )}
        </div>
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
          {detached ? (
            <p className="text-sm text-muted" data-testid="results-detached">
              Player closed so the file can be deleted.
            </p>
          ) : (
            <video
              data-testid="results-video"
              src={href}
              controls
              className="w-full rounded border border-line"
            />
          )}
          <div className="flex items-center gap-3">
            <a
              data-testid="results-download"
              href={href}
              download
              className="text-xs underline"
            >
              Download {info.complete ? 'the recording' : 'what has been recorded so far'}
            </a>
            {/* This player is itself the commonest reason the file cannot be
              deleted: it holds the response open through the backend. The
              button closes it first, then sweeps. */}
            <button
              type="button"
              data-testid="results-release"
              onClick={() => void onRelease()}
              disabled={releasing || running}
              title={
                running
                  ? 'Stop the run before releasing its recording.'
                  : 'Close the player and delete this recording, so the next run starts clean.'
              }
              className="text-xs underline disabled:cursor-not-allowed disabled:opacity-40"
            >
              {releasing ? 'Releasing…' : 'Release'}
            </button>
          </div>
          {releaseNote && (
            <p className="text-xs text-muted" data-testid="results-release-note">
              {releaseNote}
            </p>
          )}
        </div>
      )}
    </Card>
  )
}
