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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { reportPlayback } from '../lib/api'
import { entriesAfter, frameAtOrBefore } from '../lib/frameindex'
import { useMedia } from '../state/MediaContext'
import { ModeSelector } from './ModeSelector'
import { PreviewControls } from './PreviewControls'
import { StudioCard } from './StudioLayout'
import { Transport } from './Transport'

/** At most one advisory position post per this many ms of continuous play. */
export const PLAYBACK_THROTTLE_MS = 500

/** How many frames ahead of the playhead to fetch and decode early.
 *
 * Three, not thirty: generated frames are large, the run is producing them at
 * a few per second, and warming a long tail would compete for bandwidth with
 * the generation the user is waiting on. Three covers the swap that is about
 * to happen and the two behind it. */
export const PREFETCH_FRAMES = 3

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
    recordCoverage,
  } = useMedia()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [overlayUrl, setOverlayUrl] = useState<string | null>(null)
  // Show/hide is a display concern and nothing else. The lookup, the coverage
  // measurement and the advisory position report all keep running while the
  // layer is hidden -- otherwise turning the overlay off to compare against the
  // source would quietly zero the coverage figure and make the run look broken.
  const [overlayVisible, setOverlayVisible] = useState(true)
  const [markMessage, setMarkMessage] = useState<string | null>(null)
  const lastReportRef = useRef(0)

  // The index the handler reads, held in a ref so the listener never has to be
  // torn down and re-attached as frames arrive.
  const indexRef = useRef(index)
  useEffect(() => {
    indexRef.current = index
  }, [index])

  // The overlay element itself, written to directly on the frame callback, and
  // the preview url the write has to stand down for -- both in refs for the
  // same reason as the index: the frame callback is armed once and must not be
  // re-armed on every render.
  const overlayImgRef = useRef<HTMLImageElement | null>(null)
  const previewUrlRef = useRef(previewUrl)
  useEffect(() => {
    previewUrlRef.current = previewUrl
  }, [previewUrl])

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

  // Urls already handed to the browser to fetch and decode. Frame urls carry an
  // immutable cache header, so the second reference is a memory hit and one
  // attempt per url is enough for the life of the card.
  const prefetchedRef = useRef(new Set<string>())

  /** Warm the next few frames so the swap itself never waits on a decode.
   *
   * Assigning `src` to a cold image decodes on the swap, which on a large
   * frame is precisely the hitch this is here to remove. Detached `Image`
   * objects do the fetch and the decode in advance and are then dropped —
   * what survives is the browser's own cache entry, which is what the visible
   * <img> ends up hitting. Failures are ignored on purpose: a frame that
   * cannot be prefetched simply loads the old way, and the overlay already
   * degrades to the raw video if it cannot load at all.
   */
  const prefetchAhead = useCallback((t: number) => {
    for (const entry of entriesAfter(indexRef.current, t, PREFETCH_FRAMES)) {
      if (prefetchedRef.current.has(entry.url)) continue
      prefetchedRef.current.add(entry.url)
      const img = new Image()
      img.src = entry.url
      void img.decode?.().catch(() => {})
    }
  }, [])

  /** Point the overlay at whatever covers `t`, or at nothing.
   *
   * Idempotent by design: the same url in means the same state object out, so
   * React bails out of the render entirely. That is what makes it safe to call
   * this once per composited video frame.
   *
   * The url is also written straight to the mounted <img>, ahead of the state
   * update. Measured in Chrome over a covered span, going through the render
   * alone left the layer a median of 30ms — one whole video frame — staler
   * than the frame index could already have supplied, on 30% of frames: the
   * lookup happens in the frame callback but the pixels only change after
   * React commits, which is the following frame at best. Writing the attribute
   * here paints on this frame instead, and the commit that follows sets the
   * same value, so it is a no-op rather than a second paint.
   *
   * React still owns the element's existence and owns `src` outright whenever
   * a rendered preview is up, which is why the direct write stands down then.
   */
  const swapOverlay = useCallback((t: number) => {
    const next = frameAtOrBefore(indexRef.current, t)?.url ?? null
    const img = overlayImgRef.current
    if (next !== null && img !== null && previewUrlRef.current === null) {
      if (img.getAttribute('src') !== next) img.setAttribute('src', next)
    }
    setOverlayUrl((prev) => (prev === next ? prev : next))
    prefetchAhead(t)
  }, [])

  /** Swap the overlay once per *video* frame instead of once per `timeupdate`.
   *
   * `timeupdate` fires roughly four times a second. Against 30fps source that
   * is a 4Hz layer over a 30Hz picture, and the beat between the two is the
   * choppiness — the swapped face visibly lags and snaps while the video under
   * it runs smooth. `requestVideoFrameCallback` fires once per frame the
   * compositor actually shows and hands back that frame's `mediaTime`, so the
   * overlay changes on the same beat as the picture it belongs to.
   *
   * D-06 is untouched: this still only reads the in-memory index and assigns a
   * string. Nothing is awaited, nothing is requested, and the callback drops
   * itself when the element goes away. Browsers without the API (and jsdom)
   * simply keep the `timeupdate` path below, which remains correct and merely
   * coarser.
   */
  useEffect(() => {
    const video = videoRef.current as
      | (HTMLVideoElement & {
          requestVideoFrameCallback?: (
            cb: (now: number, meta: { mediaTime: number }) => void,
          ) => number
          cancelVideoFrameCallback?: (handle: number) => void
        })
      | null
    if (!video?.requestVideoFrameCallback) return
    let handle = 0
    let stopped = false
    const onFrame = (_now: number, meta: { mediaTime: number }) => {
      swapOverlay(meta.mediaTime)
      if (!stopped) handle = video.requestVideoFrameCallback!(onFrame)
    }
    handle = video.requestVideoFrameCallback(onFrame)
    return () => {
      stopped = true
      video.cancelVideoFrameCallback?.(handle)
    }
  }, [swapOverlay, project?.video_src])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const onTimeUpdate = () => {
      const t = video.currentTime
      reportPlayhead(t)
      const entry = frameAtOrBefore(indexRef.current, t)
      setOverlayUrl((prev) => (prev === (entry?.url ?? null) ? prev : entry?.url ?? null))
      // The coverage figure is measured from this lookup, using the result
      // already in hand: no second lookup, nothing awaited, nothing that can
      // make playback wait on generation.
      recordCoverage(entry !== null)
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
  }, [report, project?.video_src, reportPlayhead, setVideoPaused, clearPreview, recordCoverage])

  // A new run wipes the previous run's frames; an index that no longer holds
  // the displayed url must not keep showing it (T-05.1-05-06). Derived during
  // render, not stored: a stale frame must never survive even one paint.
  // A rendered preview outranks the index while it exists — it is cleared on
  // play, on start, and by rendering a new one.
  // A set, not a scan: the overlay now swaps at frame rate, and an O(n) walk of
  // a several-thousand-entry index on every one of those renders is exactly the
  // kind of cost that shows up as stutter.
  const indexUrls = useMemo(() => new Set(index.map((e) => e.url)), [index])
  const indexedUrl = overlayUrl !== null && indexUrls.has(overlayUrl) ? overlayUrl : null
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
      {/* No min height once a video is mounted: the stage is exactly the video's
          box, which is what keeps the absolutely-positioned overlay registered
          with the frame instead of centred in a taller black field. */}
      <div className={`relative bg-black ${project?.video_src ? '' : 'min-h-[280px]'}`}>
        {project?.video_src ? (
          <video
            ref={videoRef}
            data-testid="player-video"
            src={project.video_src}
            controls
            // Capped at 80% of the viewport height and letterboxed inside the
            // full card width. A portrait source is the case that breaks: given
            // only `w-full` it takes its own aspect ratio and runs off the
            // bottom of the screen. `object-contain` on both the video and the
            // overlay makes them resolve to the same box, so the swapped layer
            // still lands exactly on the frame.
            className="block max-h-[80vh] w-full object-contain"
          />
        ) : (
          <p className="p-4 text-sm text-muted">Load a target video to begin.</p>
        )}
        {displayedUrl !== null && overlayVisible && (
          <img
            data-testid="overlay-image"
            ref={overlayImgRef}
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
        <button
          type="button"
          data-testid="btn-toggle-overlay"
          aria-pressed={overlayVisible}
          onClick={() => setOverlayVisible((v) => !v)}
          title="Hide the swapped layer to compare against the source video"
          className={`rounded border px-3 py-1.5 text-xs font-semibold ${
            overlayVisible
              ? 'border-accent bg-active text-text'
              : 'border-line bg-raised text-muted'
          }`}
        >
          {overlayVisible ? 'Overlay on' : 'Overlay off'}
        </button>
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
          {/* Deliberately not gated on `running`: the case this exists for is a
              stop that never came back, which leaves the card's idea of the run
              stuck at whatever it was. A disabled escape hatch is not one. */}
          <button
            type="button"
            data-testid="btn-force-stop-run"
            onClick={() => void stopRun({ force: true })}
            disabled={!projectId}
            title="Kill the run now. Anything still being written to the recording may be lost."
            className="rounded border border-bad/60 bg-raised px-3 py-1.5 text-xs font-semibold text-bad hover:bg-bad/10 disabled:pointer-events-none disabled:opacity-50"
          >
            Force stop
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
