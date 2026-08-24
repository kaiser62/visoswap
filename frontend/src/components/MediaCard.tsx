/** The media card: video ingest by file or URL, and the face strip (plan
 *  05.1-06 Tasks 1–2, D-03).
 *
 * Both source controls stay mounted once a video exists, so replacing a video
 * never requires a new project. The oversize guard mirrors the server's
 * `max_upload_bytes` before a single byte is sent: the server reads the whole
 * stream to its limit before it can answer 413, so the client-side check is
 * the difference between an instant message and a wasted upload.
 */

import { useRef, useState } from 'react'
import { MAX_UPLOAD_BYTES, setSourceUrl, uploadSource } from '../lib/api'
import { ApiError } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import { FaceLibrary } from './FaceLibrary'
import { StudioCard } from './StudioLayout'

const fmtLimit = (bytes: number) => {
  const gib = bytes / 1024 ** 3
  return Number.isInteger(gib) ? `${gib} GB` : `${(gib).toFixed(1)} GB`
}

export function MediaCard() {
  const { projectId, project, refreshProject } = useMedia()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const describe = (err: unknown) =>
    err instanceof ApiError || err instanceof Error ? err.message : 'Upload failed'

  const handleFile = async (file: File) => {
    if (!projectId) {
      setError('No project is open.')
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      // Zero requests: refused against the same number the server enforces.
      setError(`That file is too large — the limit is ${fmtLimit(MAX_UPLOAD_BYTES)}.`)
      return
    }
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const updated = await uploadSource(projectId, file)
      setNotice(updated.video_filename ? `Video loaded: ${updated.video_filename}` : 'Video loaded.')
      await refreshProject()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleUrlSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!projectId || !url.trim()) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await setSourceUrl(projectId, url.trim())
      setNotice('Video URL set.')
      await refreshProject()
    } catch (err) {
      // The server's detail stays verbatim; the field keeps its value so the
      // user can correct it instead of retyping.
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <StudioCard title="Media" defaultOpen>
      <div className="space-y-4 p-4">
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-text">Target video</h3>
          {project?.has_video ? (
            <p className="text-xs text-muted" data-testid="media-source-readout">
              Current source:{' '}
              <span data-testid="media-video-filename" className="text-text">
                {project.video_filename ?? 'remote URL'}
              </span>
            </p>
          ) : (
            <p className="text-xs text-muted">
              Add a video by choosing a local file or pointing at a URL.
            </p>
          )}

          <label className="flex items-center gap-2 text-sm">
            <input
              ref={fileInputRef}
              data-testid="media-file-input"
              type="file"
              accept="video/*"
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleFile(file)
              }}
              className="w-full max-w-xs cursor-pointer rounded border border-line bg-raised px-3 py-1.5 text-xs text-text"
            />
          </label>

          <form onSubmit={handleUrlSubmit} className="flex items-center gap-2">
            <input
              data-testid="media-url-input"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/video.mp4"
              disabled={busy}
              aria-label="Video URL"
              className="min-w-0 flex-1 rounded border border-line bg-bg px-3 py-1.5 text-xs text-text"
            />
            <button
              data-testid="media-url-submit"
              type="submit"
              disabled={busy || !url.trim()}
              className="rounded bg-accent px-3 py-1.5 text-xs font-semibold text-bg hover:bg-accent/90 disabled:pointer-events-none disabled:opacity-50"
            >
              Load URL
            </button>
          </form>

          {busy && (
            <p role="status" className="text-xs text-muted" data-testid="media-busy">
              Uploading…
            </p>
          )}
          {error && (
            <p role="alert" className="text-xs text-bad" data-testid="media-error">
              {error}
            </p>
          )}
          {notice && !error && <p className="text-xs text-good">{notice}</p>}
        </section>

        <FaceLibrary />
      </div>
    </StudioCard>
  )
}
