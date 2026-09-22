import { useCallback, useEffect, useState } from 'react'
import {
  deleteTake,
  listTakes,
  takeUrl,
  outputUrl,
  releaseRecording,
  composeProject,
  listComposeJobs,
  cancelComposeJob,
  forgetComposeJob,
} from '../lib/api'
import { useMedia } from '../state/MediaContext'
import type { Take, ComposeJob } from '../types'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatModified(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString()
}

export function MobileTakesView() {
  const { projectId, recording, running } = useMedia()

  const [takes, setTakes] = useState<Take[]>([])
  const [composeJobs, setComposeJobs] = useState<ComposeJob[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const [playingTake, setPlayingTake] = useState<string | null>(null)
  const [deletingName, setDeletingName] = useState<string | null>(null)

  const reloadAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [tList, jList] = await Promise.all([listTakes(), listComposeJobs()])
      setTakes(tList)
      setComposeJobs(jList)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed loading takes')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reloadAll()
  }, [reloadAll])

  const handleCompose = async () => {
    if (!projectId) return
    setComposing(true)
    try {
      await composeProject(projectId)
      await reloadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Compose request failed')
    } finally {
      setComposing(false)
    }
  }

  const handleDeleteTake = async (name: string) => {
    setDeletingName(name)
    try {
      await deleteTake(name)
      if (playingTake === name) setPlayingTake(null)
      await reloadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setDeletingName(null)
    }
  }

  const handleReleaseRecording = async () => {
    if (!projectId) return
    try {
      await releaseRecording(projectId)
      await reloadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Release failed')
    }
  }

  return (
    <div className="space-y-4 p-4 pb-28">
      {/* 1. Live Run Recording Status Card */}
      <div className="rounded-2xl border border-line bg-card p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text">Active Recording</h3>
          {recording?.available ? (
            <span
              className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
                recording.complete
                  ? 'bg-good/15 text-good'
                  : 'bg-accent/15 text-accent animate-pulse'
              }`}
            >
              {recording.complete ? 'Finished' : 'Recording…'}
            </span>
          ) : (
            <span className="rounded-full bg-raised px-2.5 py-0.5 text-[11px] text-muted">
              None
            </span>
          )}
        </div>

        {recording?.available && projectId ? (
          <div className="mt-3 space-y-3">
            <div className="flex items-center justify-between text-xs text-muted">
              <span>Recorded Size:</span>
              <span className="font-semibold text-text">{formatBytes(recording.bytes)}</span>
            </div>

            {/* In-browser Video preview */}
            <div className="overflow-hidden rounded-xl bg-black">
              <video
                src={outputUrl(projectId)}
                controls
                playsInline
                className="max-h-48 w-full object-contain"
              />
            </div>

            <div className="flex gap-2">
              <a
                href={outputUrl(projectId)}
                download
                className="flex-1 rounded-xl bg-accent py-2.5 text-center text-xs font-bold text-bg shadow-sm active:bg-accent/80"
              >
                Download MP4
              </a>
              {!running && (
                <button
                  type="button"
                  onClick={handleReleaseRecording}
                  className="rounded-xl border border-line bg-raised px-3 py-2 text-xs font-semibold text-muted active:text-text"
                >
                  Clear
                </button>
              )}
            </div>
          </div>
        ) : (
          <p className="mt-2 text-xs text-muted">
            Start a run in the Studio tab to automatically generate a recording.
          </p>
        )}
      </div>

      {/* 2. Compose Full Video Button */}
      <div className="rounded-2xl border border-line bg-card p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-text">Full Video Assembly</h3>
            <p className="text-[11px] text-muted">
              Re-encode all generated frames into a finalized MP4 take
            </p>
          </div>
          <button
            type="button"
            onClick={handleCompose}
            disabled={composing || running || !projectId}
            className="rounded-xl bg-raised border border-line px-3 py-2 text-xs font-semibold text-text active:bg-active disabled:opacity-40"
          >
            {composing ? 'Queuing…' : 'Compose'}
          </button>
        </div>

        {/* Compose job list */}
        {composeJobs.length > 0 && (
          <div className="mt-3 space-y-2 border-t border-line/50 pt-3">
            <span className="block text-[11px] font-semibold text-muted">Recent Compose Jobs</span>
            {composeJobs.map((job) => (
              <div
                key={job.id}
                className="flex items-center justify-between rounded-xl bg-raised p-2.5 text-xs"
              >
                <div>
                  <span className="font-medium text-text">{job.project_name}</span>
                  <span className="block text-[10px] text-muted">
                    {job.frames_written}/{job.total_frames} frames • {job.state}
                  </span>
                </div>
                {job.state === 'running' || job.state === 'pending' ? (
                  <button
                    type="button"
                    onClick={() => void cancelComposeJob(job.id).then(reloadAll)}
                    className="text-[11px] text-bad"
                  >
                    Cancel
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void forgetComposeJob(job.id).then(reloadAll)}
                    className="text-[11px] text-muted hover:text-text"
                  >
                    Dismiss
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 3. Exported Takes Gallery */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text">Saved Takes ({takes.length})</h3>
          <button
            type="button"
            onClick={reloadAll}
            disabled={loading}
            className="text-xs text-accent"
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {error && <p className="rounded-xl bg-bad/10 p-3 text-xs text-bad">{error}</p>}

        <div className="space-y-3">
          {takes.map((take) => {
            const isPlaying = playingTake === take.name
            return (
              <div
                key={take.name}
                className="overflow-hidden rounded-2xl border border-line bg-card shadow-sm"
              >
                {/* Take Header */}
                <div className="p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-semibold text-text">
                        {take.name}
                      </span>
                      <span className="block text-[10px] text-muted">
                        {formatModified(take.modified)} • {formatBytes(take.bytes)}
                      </span>
                    </div>

                    <button
                      type="button"
                      disabled={deletingName === take.name}
                      onClick={() => handleDeleteTake(take.name)}
                      className="text-muted hover:text-bad active:text-bad"
                      title="Delete take"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>

                  {/* Player if toggled */}
                  {isPlaying && (
                    <div className="mt-2.5 overflow-hidden rounded-xl bg-black">
                      <video
                        src={takeUrl(take.name)}
                        controls
                        autoPlay
                        playsInline
                        className="max-h-52 w-full object-contain"
                      />
                    </div>
                  )}

                  {/* Action buttons */}
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => setPlayingTake(isPlaying ? null : take.name)}
                      className="flex-1 rounded-xl border border-line bg-raised py-2 text-xs font-semibold text-text active:bg-active"
                    >
                      {isPlaying ? 'Close Preview' : 'Play Video'}
                    </button>
                    <a
                      href={takeUrl(take.name)}
                      download
                      className="flex-1 rounded-xl bg-accent py-2 text-center text-xs font-bold text-bg active:bg-accent/80"
                    >
                      Download
                    </a>
                  </div>
                </div>
              </div>
            )
          })}

          {takes.length === 0 && !loading && (
            <div className="rounded-2xl border border-line bg-card p-8 text-center text-xs text-muted">
              No exported takes yet.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
