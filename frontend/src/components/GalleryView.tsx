/** The takes gallery (plan 05.1-07 Task 3, D-10/D-12).
 *
 * A view on the one page, never a route (D-01). Names come from the server's
 * list response and are rendered as React children and sent back encoded as a
 * single path component — the server re-validates them either way.
 *
 * Deleting a take destroys the user's accumulated work, so it goes through a
 * confirmation rather than a bare button (T-05.1-07-07).
 */

import { useCallback, useEffect, useState } from 'react'
import { deleteTake, listTakes, takeUrl } from '../lib/api'
import type { Take } from '../types'
import { Button, Card, Modal } from './ui'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** The backend reports POSIX seconds; the list shows a readable local time. */
function formatModified(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString()
}

export function GalleryView() {
  const [takes, setTakes] = useState<Take[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      setTakes(await listTakes())
    } catch (err) {
      // Keep whatever list is on screen; the retry re-issues the request.
      setError(err instanceof Error ? err.message : 'Could not list takes')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const confirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteTake(pendingDelete)
      if (playing === pendingDelete) setPlaying(null)
      setPendingDelete(null)
      await load()
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-4 p-6" data-testid="gallery-view">
      <h2 className="text-base font-semibold">Takes</h2>

      {playing && (
        <Card>
          <video
            data-testid="take-player"
            src={takeUrl(playing)}
            controls
            autoPlay
            className="w-full rounded"
          />
        </Card>
      )}

      {error !== null && (
        <Card>
          <p role="alert" className="text-sm text-bad" data-testid="gallery-error">
            {error}
          </p>
          <Button className="mt-3" onClick={() => void load()} data-testid="gallery-retry">
            Retry loading takes
          </Button>
        </Card>
      )}

      {takes !== null && takes.length === 0 && error === null && (
        <Card>
          <p className="text-sm text-muted" data-testid="gallery-empty">
            No takes yet. A take appears here after a run ends and its recording is exported.
          </p>
        </Card>
      )}

      {takes !== null && takes.length > 0 && (
        <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {takes.map((take) => (
            <li key={take.name}>
              <Card data-testid="take-item">
                <p className="truncate text-sm font-medium" title={take.name}>
                  {take.name}
                </p>
                <p className="mt-1 text-xs text-muted">
                  <span data-testid="take-size">{formatBytes(take.bytes)}</span>
                  {' · '}
                  <span data-testid="take-modified">{formatModified(take.modified)}</span>
                </p>
                {take.partial && (
                  <p className="mt-1 text-xs text-bad" data-testid="take-partial">
                    Partial — this run did not finish
                  </p>
                )}
                <div className="mt-3 flex items-center gap-2">
                  <Button onClick={() => setPlaying(take.name)} data-testid="take-play">
                    Play
                  </Button>
                  <a
                    href={takeUrl(take.name)}
                    download
                    className="text-xs underline"
                    data-testid="take-download"
                  >
                    Download
                  </a>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setDeleteError(null)
                      setPendingDelete(take.name)
                    }}
                    data-testid="take-delete"
                  >
                    Delete
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {pendingDelete !== null && (
        <Modal open onClose={() => setPendingDelete(null)} title="Delete take">
          <p className="text-sm">
            Delete <span className="font-medium">{pendingDelete}</span>? The file is removed from
            the output folder and cannot be recovered.
          </p>
          {deleteError && (
            <p role="alert" className="mt-3 text-xs text-bad" data-testid="take-delete-error">
              {deleteError}
            </p>
          )}
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setPendingDelete(null)} disabled={deleting}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void confirmDelete()}
              disabled={deleting}
              data-testid="take-delete-confirm"
            >
              {deleting ? 'Deleting…' : 'Delete take'}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  )
}
