/** The player card: video, swapped-frame overlay, transport (plan 05.1-05, D-06).
 *
 * Overlay mechanism — positioned image, not canvas compositing. An <img> laid
 * over the video at the same box keeps decoding on the browser's own path,
 * needs no per-frame draw call, and has no way to touch the video element's
 * clock. Canvas would put a draw on every frame in our own code and invite a
 * read-back from the element; this cannot.
 *
 * The invariants this file exists to keep (D-06):
 *   - the timeupdate handler awaits nothing and issues no request: it looks the
 *     playhead up in the in-memory frame index (`frameAtOrBefore`, nearest
 *     PREVIOUS only, D-15d) and sets an image source. Frame urls carry an
 *     immutable cache header, so a repeated source is a memory hit.
 *   - a missing or broken frame degrades to the raw video, never to a paused
 *     player: on image error the overlay is cleared and playback continues.
 *   - position reporting is advisory (backend/api/generation.py report_playback)
 *     and fire-and-forget with its rejection swallowed — a network error during
 *     normal playback must never reach the screen.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { reportPlayback } from '../lib/api'
import { frameAtOrBefore } from '../lib/frameindex'
import { useMedia } from '../state/MediaContext'
import { StudioCard } from './StudioLayout'
import { Transport } from './Transport'

/** At most one advisory position post per this many ms of continuous play. */
export const PLAYBACK_THROTTLE_MS = 500

export function PlayerCard() {
  const { projectId, project, index, range, setStartMark, setEndMark, clearRange } = useMedia()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [overlayUrl, setOverlayUrl] = useState<string | null>(null)
  const [markMessage, setMarkMessage] = useState<string | null>(null)
  const lastReportRef = useRef(0)

  // The index the handler reads, held in a ref so the listener never has to be
  // torn down and re-attached as frames arrive.
  const indexRef = useRef(index)
  useEffect(() => {
    indexRef.current = index
  }, [index])

  const report = useCallback(
    (t: number, seeked: boolean) => {
      if (!projectId) return
      // Fire and forget: the backend documents this call as advisory.
      void reportPlayback(projectId, { current_time: t, seeked }).catch(() => {
        /* advisory — a failed report must never surface or interrupt playback */
      })
    },
    [projectId],
  )

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const onTimeUpdate = () => {
      const t = video.currentTime
      const entry = frameAtOrBefore(indexRef.current, t)
      setOverlayUrl(entry?.url ?? null)
      const now = Date.now()
      if (now - lastReportRef.current >= PLAYBACK_THROTTLE_MS) {
        lastReportRef.current = now
        report(t, false)
      }
    }
    const onSeeked = () => {
      lastReportRef.current = Date.now()
      report(video.currentTime, true)
    }

    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('seeked', onSeeked)
    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('seeked', onSeeked)
    }
    // Re-run once the video element actually mounts (it only renders after the
    // project payload arrives), otherwise the listeners are never attached.
  }, [report, project?.video_src])

  // A new run wipes the previous run's frames; an index that no longer holds
  // the displayed url must not keep showing it (T-05.1-05-06). Derived during
  // render, not stored: a stale frame must never survive even one paint.
  const displayedUrl =
    overlayUrl !== null && index.some((e) => e.url === overlayUrl) ? overlayUrl : null

  const currentTime = () => videoRef.current?.currentTime ?? 0

  const handleSetStart = () => {
    setMarkMessage(null)
    setStartMark(currentTime())
  }

  const handleSetEnd = () => {
    const t = currentTime()
    if (range.start !== null && t <= range.start) {
      // Refused, never silently reordered: a swapped range would export a span
      // the user did not ask for.
      setMarkMessage('End mark must come after the start mark.')
      return
    }
    setMarkMessage(null)
    setEndMark(t)
  }

  const handleClear = () => {
    setMarkMessage(null)
    clearRange()
  }

  const seekTo = (t: number) => {
    const video = videoRef.current
    if (video) video.currentTime = t
  }

  const fps = project?.fps ?? null

  return (
    <StudioCard title="Player" defaultOpen>
      <div className="relative min-h-[280px] bg-black">
        {project?.video_src ? (
          <video
            ref={videoRef}
            data-testid="player-video"
            src={project.video_src}
            controls
            className="block h-full w-full"
          />
        ) : (
          <p className="p-4 text-sm text-muted">Load a target video to begin.</p>
        )}
        {displayedUrl !== null && (
          <img
            data-testid="overlay-image"
            src={displayedUrl}
            alt=""
            // A broken frame clears the overlay; the video is never touched.
            onError={() => setOverlayUrl(null)}
            className="pointer-events-none absolute inset-0 h-full w-full object-contain"
          />
        )}
      </div>
      <Transport
        marks={range}
        message={markMessage}
        onSetStart={handleSetStart}
        onSetEnd={handleSetEnd}
        onClear={handleClear}
        onSeekSeconds={seekTo}
        onSeekFrame={(f) => seekTo(fps && fps > 0 ? f / fps : f)}
      />
    </StudioCard>
  )
}
