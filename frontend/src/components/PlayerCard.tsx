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
import { ModeSelector } from './ModeSelector'
import { PreviewControls } from './PreviewControls'
import { StudioCard } from './StudioLayout'
import { Transport } from './Transport'

/** At most one advisory position post per this many ms of continuous play. */
export const PLAYBACK_THROTTLE_MS = 500

export function PlayerCard() {
  const {
    projectId,
    project,
    index,
    range,
    error: mediaError,
    running,
    previewUrl,
    startRun,
    stopRun,
    setStartMark,
    setEndMark,
    clearRange,
    reportPlayhead,
    setVideoPaused,
    clearPreview,
  } = useMedia()
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
      reportPlayhead(t)
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
      reportPlayhead(video.currentTime)
      report(video.currentTime, true)
    }
    const onPlay = () => {
      setVideoPaused(false)
      // Playback invalidates a held preview: the live index lookup below owns
      // the overlay again from here on.
      clearPreview()
    }
    const onPause = () => setVideoPaused(true)

    setVideoPaused(video.paused)
    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('seeked', onSeeked)
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('seeked', onSeeked)
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
    }
    // Re-run once the video element actually mounts (it only renders after the
    // project payload arrives), otherwise the listeners are never attached.
  }, [report, project?.video_src, reportPlayhead, setVideoPaused, clearPreview])

  // A new run wipes the previous run's frames; an index that no longer holds
  // the displayed url must not keep showing it (T-05.1-05-06). Derived during
  // render, not stored: a stale frame must never survive even one paint.
  // A rendered preview outranks the index while it exists — it is cleared on
  // play, on start, and by rendering a new one.
  const indexedUrl =
    overlayUrl !== null && index.some((e) => e.url === overlayUrl) ? overlayUrl : null
  const displayedUrl = previewUrl ?? indexedUrl

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

  const handleStartRun = () => {
    setMarkMessage(null)
    reportPlayhead(currentTime())
    void startRun()
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
      <div
        className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3"
        data-testid="player-actions"
      >
        <ModeSelector />
        <PreviewControls />
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            data-testid="btn-start-run"
            onClick={handleStartRun}
            disabled={running || !projectId}
            className="rounded bg-accent px-3 py-1.5 text-xs font-semibold text-bg hover:bg-accent/90 disabled:pointer-events-none disabled:opacity-50"
          >
            Start
          </button>
          <button
            type="button"
            data-testid="btn-stop-run"
            onClick={() => void stopRun()}
            disabled={!running}
            className="rounded border border-line bg-raised px-3 py-1.5 text-xs font-semibold text-text hover:bg-active disabled:pointer-events-none disabled:opacity-50"
          >
            Stop
          </button>
        </div>
      </div>
      {mediaError !== null && (
        <p role="alert" className="px-4 pb-3 text-xs text-bad" data-testid="media-error">
          {mediaError}
        </p>
      )}
    </StudioCard>
  )
}
