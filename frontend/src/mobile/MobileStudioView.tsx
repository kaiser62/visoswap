import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { reportPlayback, setSourceUrl, uploadSource, MAX_UPLOAD_BYTES } from '../lib/api'
import { entriesAfter, frameAtOrBefore } from '../lib/frameindex'
import { useMedia, type MediaMode } from '../state/MediaContext'

const PLAYBACK_THROTTLE_MS = 500
const PREFETCH_FRAMES = 3

const MODES: { id: MediaMode; label: string }[] = [
  { id: 'live', label: 'Stream-Live' },
  { id: 'interval', label: 'Interval' },
  { id: 'export', label: 'Export' },
]

const SCALES = [
  { value: 0.5, label: '50% Speed' },
  { value: 0.75, label: '75%' },
  { value: 1.0, label: '100% Full' },
]

interface MobileStudioViewProps {
  onGoToFaces: () => void
  onOpenProjects?: () => void
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

export function MobileStudioView({ onGoToFaces, onOpenProjects }: MobileStudioViewProps) {
  const {
    projectId,
    project,
    index,
    range,
    error: mediaError,
    running,
    previewUrl,
    mode,
    selectMode,
    updateInterval,
    updateScale,
    startRun,
    stopRun,
    setStartMark,
    setEndMark,
    clearRange,
    reportPlayhead,
    setVideoPaused,
    clearPreview,
    recordCoverage,
    refreshProject,
  } = useMedia()

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [overlayUrl, setOverlayUrl] = useState<string | null>(null)
  const [overlayVisible, setOverlayVisible] = useState(true)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [isScrubbing, setIsScrubbing] = useState(false)
  const [scrubTime, setScrubTime] = useState(0)
  const [isMuted, setIsMuted] = useState(true)
  const [useNativeControls, setUseNativeControls] = useState(false)
  const [theaterMode, setTheaterMode] = useState(false)
  const isScrubbingRef = useRef(false)
  const [duration, setDuration] = useState(0)

  useEffect(() => {
    if (!theaterMode) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setTheaterMode(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [theaterMode])
  const [markMessage, setMarkMessage] = useState<string | null>(null)
  const [intervalText, setIntervalText] = useState('')
  const [showVideoSource, setShowVideoSource] = useState(false)
  const [videoUrl, setVideoUrlInput] = useState('')
  const [videoLoading, setVideoLoading] = useState(false)
  const [sourceNotice, setSourceNotice] = useState<string | null>(null)

  const lastReportRef = useRef(0)
  const indexRef = useRef(index)
  useEffect(() => {
    indexRef.current = index
  }, [index])

  const prefetchedRef = useRef(new Set<string>())
  const prefetchAhead = useCallback((t: number) => {
    for (const entry of entriesAfter(indexRef.current, t, PREFETCH_FRAMES)) {
      if (prefetchedRef.current.has(entry.url)) continue
      prefetchedRef.current.add(entry.url)
      const img = new Image()
      img.src = entry.url
      void img.decode?.().catch(() => {})
    }
  }, [])

  const report = useCallback(
    (t: number, seeked: boolean) => {
      if (!projectId) return
      void reportPlayback(projectId, { current_time: t, seeked }).catch(() => {})
    },
    [projectId],
  )

  // Track project interval value
  useEffect(() => {
    if (project?.interval != null) setIntervalText(String(project.interval))
  }, [project?.interval])

  // Sync duration from project
  useEffect(() => {
    if (project?.duration) setDuration(project.duration)
  }, [project?.duration])

  // Keep screen awake while in Studio
  useEffect(() => {
    let wakeLock: any = null
    const requestLock = async () => {
      try {
        if ('wakeLock' in navigator) {
          wakeLock = await (navigator as any).wakeLock.request('screen')
        }
      } catch {
        /* WakeLock not supported or blocked */
      }
    }
    void requestLock()

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void requestLock()
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      void wakeLock?.release().catch(() => {})
    }
  }, [])

  // Sync initial and reactive overlay frame from index and currentTime
  useEffect(() => {
    if (index.length === 0) {
      setOverlayUrl(null)
      return
    }
    const entry = frameAtOrBefore(index, currentTime)
    setOverlayUrl(entry ? entry.url : null)
  }, [index, currentTime])

  // Smooth per-frame overlay updates when video is playing
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
      const t = meta.mediaTime
      if (!isScrubbingRef.current) {
        setCurrentTime(t)
      }
      reportPlayhead(t)
      const entry = frameAtOrBefore(indexRef.current, t)
      if (entry) {
        setOverlayUrl(entry.url)
        prefetchAhead(t)
      }
      const now = Date.now()
      if (now - lastReportRef.current >= PLAYBACK_THROTTLE_MS) {
        lastReportRef.current = now
        report(t, false)
      }
      if (!stopped) handle = video.requestVideoFrameCallback!(onFrame)
    }
    handle = video.requestVideoFrameCallback(onFrame)
    return () => {
      stopped = true
      video.cancelVideoFrameCallback?.(handle)
    }
  }, [prefetchAhead, project?.video_src, report, reportPlayhead])

  // Video event listeners
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const onTimeUpdate = () => {
      const t = video.currentTime
      if (!isScrubbingRef.current) {
        setCurrentTime(t)
      }
      reportPlayhead(t)
      prefetchAhead(t)
      const entry = frameAtOrBefore(indexRef.current, t)
      setOverlayUrl(entry ? entry.url : null)
      recordCoverage(entry !== null)

      const now = Date.now()
      if (now - lastReportRef.current >= PLAYBACK_THROTTLE_MS) {
        lastReportRef.current = now
        report(t, false)
      }
    }

    const onSeeked = () => {
      lastReportRef.current = Date.now()
      const t = video.currentTime
      if (!isScrubbingRef.current) {
        setCurrentTime(t)
      }
      reportPlayhead(t)
      report(t, true)
      const entry = frameAtOrBefore(indexRef.current, t)
      setOverlayUrl(entry ? entry.url : null)
    }

    const onPlay = () => {
      setIsPlaying(true)
      setVideoPaused(false)
      clearPreview()
    }

    const onPause = () => {
      setIsPlaying(false)
      setVideoPaused(true)
    }

    const onLoadedMetadata = () => {
      if (video.duration && Number.isFinite(video.duration)) {
        setDuration(video.duration)
      }
      const entry = frameAtOrBefore(indexRef.current, video.currentTime)
      if (entry) setOverlayUrl(entry.url)
    }

    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('seeked', onSeeked)
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    video.addEventListener('loadedmetadata', onLoadedMetadata)

    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('seeked', onSeeked)
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
      video.removeEventListener('loadedmetadata', onLoadedMetadata)
    }
  }, [report, project?.video_src, prefetchAhead, reportPlayhead, setVideoPaused, clearPreview, recordCoverage])

  const togglePlay = () => {
    const video = videoRef.current
    if (!video) return
    if (video.paused) {
      video.play().catch(() => {
        // Fall back to muted playback if browser/OS gesture requirements block audio
        video.muted = true
        setIsMuted(true)
        void video.play().catch(() => {})
      })
    } else {
      video.pause()
    }
  }

  const toggleMute = () => {
    const next = !isMuted
    setIsMuted(next)
    if (videoRef.current) {
      videoRef.current.muted = next
    }
  }

  const skipSeconds = (delta: number) => {
    const video = videoRef.current
    if (!video) return
    const next = Math.max(0, Math.min(duration || 9999, video.currentTime + delta))
    video.currentTime = next
    setCurrentTime(next)
    reportPlayhead(next)
    report(next, true)
    const entry = frameAtOrBefore(indexRef.current, next)
    setOverlayUrl(entry ? entry.url : null)
  }

  const stepFrames = (frames: number) => {
    const fps = project?.fps && project.fps > 0 ? project.fps : 30
    skipSeconds(frames / fps)
  }

  const handleScrubberPointerDown = () => {
    isScrubbingRef.current = true
    setIsScrubbing(true)
    setScrubTime(currentTime)
  }

  const handleScrubberChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const targetTime = Number(e.target.value)
    setScrubTime(targetTime)
    const entry = frameAtOrBefore(indexRef.current, targetTime)
    if (entry) setOverlayUrl(entry.url)
  }

  const handleScrubberCommit = (targetVal?: number) => {
    const finalTime = targetVal !== undefined ? targetVal : scrubTime
    isScrubbingRef.current = false
    setIsScrubbing(false)
    const video = videoRef.current
    if (video) {
      video.currentTime = finalTime
    }
    setCurrentTime(finalTime)
    reportPlayhead(finalTime)
    report(finalTime, true)
    const entry = frameAtOrBefore(indexRef.current, finalTime)
    setOverlayUrl(entry ? entry.url : null)
  }

  // Active overlay url derivation
  const indexUrls = useMemo(() => new Set(index.map((e) => e.url)), [index])
  const indexedUrl = overlayUrl !== null && indexUrls.has(overlayUrl) ? overlayUrl : null
  const displayedUrl = previewUrl ?? indexedUrl

  const handleStart = () => {
    setMarkMessage(null)
    const video = videoRef.current
    const t = video?.currentTime ?? 0
    reportPlayhead(t)
    void startRun()
  }

  const handleStop = () => {
    void stopRun()
  }

  const handleForceStop = () => {
    void stopRun({ force: true })
  }

  const commitInterval = () => {
    const n = Number(intervalText)
    if (intervalText.trim() !== '' && Number.isFinite(n) && n > 0) {
      void updateInterval(n)
    }
  }

  const activeScale = project?.processing_scale ?? 1.0

  // Handle Video Upload / URL
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !projectId) return
    if (file.size > MAX_UPLOAD_BYTES) {
      setSourceNotice('File exceeds size limit.')
      return
    }
    setVideoLoading(true)
    setSourceNotice(null)
    try {
      await uploadSource(projectId, file)
      setSourceNotice('Video uploaded successfully.')
      await refreshProject()
    } catch (err) {
      setSourceNotice(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setVideoLoading(false)
      e.target.value = ''
    }
  }

  const handleUrlSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!videoUrl.trim() || !projectId) return
    setVideoLoading(true)
    setSourceNotice(null)
    try {
      await setSourceUrl(projectId, videoUrl.trim())
      setSourceNotice('Video URL loaded.')
      await refreshProject()
      setVideoUrlInput('')
    } catch (err) {
      setSourceNotice(err instanceof Error ? err.message : 'Failed loading URL')
    } finally {
      setVideoLoading(false)
    }
  }

  return (
    <div className="space-y-3 p-3">
      {/* 1. Video Player Container with Live Overlay */}
      <div
        className={
          theaterMode
            ? 'fixed inset-0 z-50 flex flex-col bg-black'
            : 'overflow-hidden rounded-2xl border border-line bg-black shadow-lg'
        }
      >
        <div
          className={`relative w-full bg-black ${
            theaterMode
              ? 'flex flex-1 min-h-0 items-center justify-center'
              : 'aspect-video'
          }`}
        >
          {project?.video_src ? (
            <video
              ref={videoRef}
              src={project.video_src}
              preload="auto"
              playsInline
              muted={isMuted}
              controls={useNativeControls}
              disablePictureInPicture
              className="h-full w-full object-contain"
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center p-4 text-center">
              <svg className="mb-2 h-10 w-10 text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <p className="text-sm font-medium text-text">No target video loaded</p>
              <div className="mt-2.5 flex items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowVideoSource(true)}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-bg active:bg-accent/80"
                >
                  + Add Video
                </button>
                {onOpenProjects && (
                  <button
                    type="button"
                    onClick={onOpenProjects}
                    className="rounded-lg border border-line bg-raised px-3 py-1.5 text-xs font-semibold text-text active:bg-active"
                  >
                    Select Project
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Overlaid swapped face frame */}
          {displayedUrl !== null && overlayVisible && (
            <img
              src={displayedUrl}
              alt=""
              onError={() => setOverlayUrl(null)}
              className="pointer-events-none absolute inset-0 z-10 h-full w-full object-contain"
            />
          )}

          {/* Quick Player & Overlay Floating Controls */}
          {project?.video_src && (
            <div className="absolute top-2.5 right-2.5 z-20 flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setTheaterMode(!theaterMode)}
                className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold backdrop-blur-md transition-colors ${
                  theaterMode
                    ? 'border border-accent/50 bg-active/90 text-accent'
                    : 'border border-line bg-card/80 text-muted active:text-text'
                }`}
                title="Toggle Theater Mode"
              >
                <svg className="h-3 w-3 fill-none stroke-current" viewBox="0 0 24 24" strokeWidth={2}>
                  <rect x="2" y="4" width="20" height="16" rx="2" />
                  <path d="M7 15h10" />
                </svg>
                {theaterMode ? 'Exit Theater' : 'Theater'}
              </button>
              <button
                type="button"
                onClick={() => setUseNativeControls(!useNativeControls)}
                className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold backdrop-blur-md transition-colors ${
                  useNativeControls
                    ? 'border border-accent/50 bg-active/90 text-accent'
                    : 'border border-line bg-card/80 text-muted active:text-text'
                }`}
                title="Toggle native controls"
              >
                {useNativeControls ? 'Native UI' : 'Custom UI'}
              </button>
              <button
                type="button"
                onClick={() => setOverlayVisible(!overlayVisible)}
                className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold backdrop-blur-md transition-colors ${
                  overlayVisible
                    ? 'border border-accent/50 bg-active/90 text-accent'
                    : 'border border-line bg-card/80 text-muted active:text-text'
                }`}
              >
                <span className={`h-2 w-2 rounded-full ${overlayVisible ? 'bg-accent' : 'bg-muted'}`} />
                {overlayVisible ? 'Overlay ON' : 'Overlay OFF'}
              </button>
            </div>
          )}
        </div>

        {/* Player Transport Bar */}
        {project?.video_src && (
          <div className="border-t border-line/60 bg-card p-3">
            {/* Scrubber slider */}
            <div className="flex items-center gap-2.5">
              <span className="text-[11px] tabular-nums text-muted">
                {formatTime(isScrubbing ? scrubTime : currentTime)}
              </span>
              <input
                type="range"
                min="0"
                max={duration || 100}
                step="0.033"
                value={isScrubbing ? scrubTime : currentTime}
                onPointerDown={handleScrubberPointerDown}
                onTouchStart={handleScrubberPointerDown}
                onPointerUp={(e) => handleScrubberCommit(Number(e.currentTarget.value))}
                onTouchEnd={() => handleScrubberCommit()}
                onKeyUp={() => handleScrubberCommit()}
                onChange={handleScrubberChange}
                className="h-2.5 flex-1 cursor-pointer accent-accent"
              />
              <span className="text-[11px] tabular-nums text-muted">{formatTime(duration)}</span>
            </div>

            {/* Play/Pause & Transport controls */}
            <div className="mt-2.5 flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => skipSeconds(-5)}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-raised text-muted active:bg-active active:text-text"
                  title="Rewind 5 seconds"
                >
                  <span className="text-[11px] font-bold">-5s</span>
                </button>

                <button
                  type="button"
                  onClick={() => stepFrames(-1)}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-raised text-muted active:bg-active active:text-text"
                  title="Previous frame"
                >
                  <span className="text-xs font-bold">&lt;</span>
                </button>

                <button
                  type="button"
                  onClick={togglePlay}
                  className="flex h-11 w-11 items-center justify-center rounded-full bg-accent text-bg shadow-md shadow-accent/20 active:scale-95"
                  title={isPlaying ? "Pause" : "Play"}
                >
                  {isPlaying ? (
                    <svg className="h-5 w-5 fill-current" viewBox="0 0 24 24">
                      <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
                    </svg>
                  ) : (
                    <svg className="ml-0.5 h-5 w-5 fill-current" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => stepFrames(1)}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-raised text-muted active:bg-active active:text-text"
                  title="Next frame"
                >
                  <span className="text-xs font-bold">&gt;</span>
                </button>

                <button
                  type="button"
                  onClick={() => skipSeconds(5)}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-raised text-muted active:bg-active active:text-text"
                  title="Forward 5 seconds"
                >
                  <span className="text-[11px] font-bold">+5s</span>
                </button>

                <button
                  type="button"
                  onClick={toggleMute}
                  className={`ml-1 flex h-8 w-8 items-center justify-center rounded-full border transition-colors ${
                    isMuted ? 'border-line bg-raised text-muted' : 'border-accent/40 bg-active text-accent'
                  }`}
                  title={isMuted ? "Unmute" : "Mute"}
                >
                  {isMuted ? (
                    <svg className="h-3.5 w-3.5 fill-current" viewBox="0 0 24 24">
                      <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27l4.73 4.73H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
                    </svg>
                  ) : (
                    <svg className="h-3.5 w-3.5 fill-current" viewBox="0 0 24 24">
                      <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
                    </svg>
                  )}
                </button>
              </div>

              {/* Marks readout if export mode */}
              {mode === 'export' && (
                <div className="flex items-center gap-1 text-[11px]">
                  <button
                    type="button"
                    onClick={() => setStartMark(currentTime)}
                    className="rounded bg-raised px-2 py-1 text-text active:bg-active"
                  >
                    In {range.start !== null ? formatTime(range.start) : '--'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setEndMark(currentTime)}
                    className="rounded bg-raised px-2 py-1 text-text active:bg-active"
                  >
                    Out {range.end !== null ? formatTime(range.end) : '--'}
                  </button>
                  {(range.start !== null || range.end !== null) && (
                    <button
                      type="button"
                      onClick={() => clearRange()}
                      className="rounded bg-raised p-1 text-muted hover:text-bad"
                    >
                      ✕
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 2. Primary Start / Stop / Force Stop Actions */}
      <div className="grid grid-cols-4 gap-2">
        <button
          type="button"
          onClick={handleStart}
          disabled={running || !projectId || !project?.video_src}
          className="col-span-2 flex h-12 items-center justify-center gap-2 rounded-xl bg-accent text-sm font-bold text-bg shadow-md shadow-accent/25 transition-transform active:scale-[0.98] disabled:opacity-40"
        >
          <svg className="h-4 w-4 fill-current" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z" />
          </svg>
          Start Run
        </button>

        <button
          type="button"
          onClick={handleStop}
          disabled={!running}
          className="col-span-1 flex h-12 items-center justify-center rounded-xl border border-line bg-raised text-sm font-semibold text-text transition-transform active:scale-[0.98] disabled:opacity-40"
        >
          Stop
        </button>

        <button
          type="button"
          onClick={handleForceStop}
          disabled={!projectId}
          className="col-span-1 flex h-12 items-center justify-center rounded-xl border border-bad/40 bg-bad/10 text-xs font-semibold text-bad transition-transform active:scale-[0.98] disabled:opacity-40"
        >
          Force Stop
        </button>
      </div>

      {/* 3. Run Mode Selection (iOS Segmented Control) */}
      <div className="rounded-2xl border border-line bg-card p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-text">Run Mode</span>
          {mode === 'interval' && (
            <div className="flex items-center gap-1.5 text-xs text-muted">
              <span>Every</span>
              <input
                type="number"
                min="0.1"
                step="0.1"
                value={intervalText}
                onChange={(e) => setIntervalText(e.target.value)}
                onBlur={commitInterval}
                className="w-16 rounded border border-line bg-bg px-2 py-0.5 text-center text-xs text-text"
              />
              <span>sec</span>
            </div>
          )}
        </div>

        {/* Segmented chips */}
        <div className="grid grid-cols-3 gap-1 rounded-xl bg-bg p-1">
          {MODES.map(({ id, label }) => {
            const isSelected = mode === id
            return (
              <button
                key={id}
                type="button"
                onClick={() => selectMode(id)}
                className={`rounded-lg py-2 text-xs font-semibold transition-colors ${
                  isSelected ? 'bg-active text-accent shadow-sm' : 'text-muted active:text-text'
                }`}
              >
                {label}
              </button>
            )
          })}
        </div>

        <p className="mt-2 text-[11px] text-muted">
          {mode === 'live' &&
            'Live preview: generates rolling buffer ahead of playhead while video plays.'}
          {mode === 'interval' &&
            `Sampled preview: generates 1 frame every ${intervalText || '1'}s across playback.`}
          {mode === 'export' &&
            'Full render: generates every single frame sequentially for seamless 30fps video.'}
        </p>

        {/* Processing Scale Chips */}
        <div className="mt-3 flex items-center justify-between border-t border-line/50 pt-2.5">
          <span className="text-xs text-muted">Resolution:</span>
          <div className="flex gap-1">
            {SCALES.map(({ value, label }) => (
              <button
                key={value}
                type="button"
                onClick={() => void updateScale(value)}
                className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                  activeScale === value
                    ? 'border-accent bg-active text-accent'
                    : 'border-line bg-raised text-muted active:text-text'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 4. Active Face Snapshot & Fast Switch */}
      <div className="flex items-center justify-between rounded-2xl border border-line bg-card px-3 py-2">
        <div className="flex items-center gap-2.5">
          <div className="h-9 w-9 shrink-0 overflow-hidden rounded-lg border border-accent/40 bg-raised">
            {project?.source_face_id ? (
              <img
                src={`/api/faces/${project.source_face_id}/thumbnail`}
                alt="Source Face"
                className="h-full w-full object-cover"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-muted">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
            )}
          </div>
          <div className="min-w-0">
            <span className="block text-[10px] font-medium text-muted">Active Source Face</span>
            <span className="block truncate text-xs font-semibold text-text">
              {project?.source_face_id ? 'Face Active' : 'No face selected'}
            </span>
          </div>
        </div>

        <button
          type="button"
          onClick={onGoToFaces}
          className="rounded-xl border border-line bg-raised px-2.5 py-1.5 text-xs font-semibold text-text active:bg-active active:text-accent"
        >
          Change Face →
        </button>
      </div>

      {/* 5. Target Video Settings Accordion */}
      <div className="rounded-2xl border border-line bg-card">
        <button
          type="button"
          onClick={() => setShowVideoSource(!showVideoSource)}
          className="flex w-full items-center justify-between p-3 text-left"
        >
          <div>
            <span className="block text-xs font-semibold text-text">Target Video Source</span>
            <span className="block text-[11px] text-muted">
              {project?.video_filename ?? (project?.has_video ? 'Target video attached' : 'None loaded')}
            </span>
          </div>
          <svg
            className={`h-4 w-4 text-muted transition-transform ${showVideoSource ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {showVideoSource && (
          <div className="space-y-3 border-t border-line/60 p-3">
            {sourceNotice && (
              <p className="rounded-lg bg-raised p-2 text-xs text-accent">{sourceNotice}</p>
            )}

            <div>
              <label className="block text-xs font-medium text-muted mb-1">
                Upload from Camera Roll / Files:
              </label>
              <input
                type="file"
                accept="video/*"
                disabled={videoLoading}
                onChange={handleFileUpload}
                className="w-full text-xs text-muted file:mr-2 file:rounded-lg file:border-0 file:bg-raised file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-text"
              />
            </div>

            <form onSubmit={handleUrlSubmit} className="space-y-1.5">
              <label className="block text-xs font-medium text-muted">
                Or paste remote video URL:
              </label>
              <div className="flex gap-2">
                <input
                  type="url"
                  placeholder="https://.../video.mp4"
                  value={videoUrl}
                  onChange={(e) => setVideoUrlInput(e.target.value)}
                  className="flex-1 rounded-xl border border-line bg-raised px-3 py-1.5 text-xs text-text placeholder-muted focus:border-accent focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={videoLoading || !videoUrl.trim()}
                  className="rounded-xl bg-raised px-3 py-1.5 text-xs font-semibold text-text active:bg-active disabled:opacity-50"
                >
                  {videoLoading ? 'Loading…' : 'Load'}
                </button>
              </div>
            </form>
          </div>
        )}
      </div>

      {markMessage && (
        <div className="rounded-xl border border-bad/40 bg-bad/10 p-3 text-xs text-bad">
          {markMessage}
        </div>
      )}

      {mediaError && (
        <div className="rounded-xl border border-bad/40 bg-bad/10 p-3 text-xs text-bad">
          {mediaError}
        </div>
      )}
    </div>
  )
}
