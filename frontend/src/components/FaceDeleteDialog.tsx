/** The cascade-delete dialog (plan 05.1-06 Task 2, D-05).
 *
 * Usage is fetched before ANY confirm control exists — a dialog that let the
 * user confirm before it knew the consequences is exactly the silent-breakage
 * failure D-05 was written against. Two bodies: a plain confirmation when no
 * project uses the face, or the affected project names with an explicit
 * warning. Confirming sends `force` only when usage said something uses it.
 */

import { useEffect, useRef, useState } from 'react'
import { deleteFace, getFaceUsage, type FaceUsageProject } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import { Button, Modal } from './ui'

export default function FaceDeleteDialog({
  faceId,
  initialUsage,
  onClose,
  onDeleted,
}: {
  faceId: string
  /** What the strip already knew when the delete was clicked; the dialog
   *  re-fetches so the answer is fresh at confirmation time. */
  initialUsage: FaceUsageProject[]
  onClose: () => void
  onDeleted: () => void
}) {
  const { refreshProject } = useMedia()
  // The strip's prefetch seeds the body so an already-known consequence is on
  // screen with the confirm control, never after it. The fetch below still
  // runs and overwrites it — the confirm always acts on a fresh answer.
  const [usage, setUsage] = useState<FaceUsageProject[] | null>(
    initialUsage.length > 0 ? initialUsage : null,
  )
  const shownFace = useRef(faceId)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // A different face than the one the seed described: drop it and wait.
    if (shownFace.current !== faceId) {
      shownFace.current = faceId
      setUsage(null)
    }
    setLoadError(null)
    setDeleteError(null)
    getFaceUsage(faceId)
      .then((projects) => {
        if (!cancelled) setUsage(projects)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Failed to check usage')
      })
    return () => {
      cancelled = true
    }
  }, [faceId])

  const handleConfirm = async () => {
    if (usage === null) return // unreachable: no confirm control until usage lands
    const force = usage.length > 0
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteFace(faceId, force)
      onDeleted()
      // A project that just lost its face shows its no-source state now,
      // not at the next reload.
      await refreshProject()
      onClose()
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal open onClose={onClose} title="Delete face">
      {loadError !== null ? (
        <p role="alert" className="text-sm text-bad" data-testid="face-delete-load-error">
          {loadError}
        </p>
      ) : usage === null ? (
        <p className="text-sm text-muted" data-testid="face-delete-checking">
          Checking which projects use this face…
        </p>
      ) : usage.length > 0 ? (
        <div className="space-y-3 text-sm">
          <p className="text-bad" data-testid="face-delete-warning">
            This face is the source of the projects below. Deleting it leaves
            them without a source face.
          </p>
          <ul className="list-disc space-y-1 pl-5" data-testid="face-delete-usage">
            {usage.map((p) => (
              <li key={p.id}>{p.name}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-muted">No project uses this face.</p>
      )}

      {deleteError && (
        <p role="alert" className="mt-3 text-xs text-bad" data-testid="face-delete-error">
          {deleteError}
        </p>
      )}

      {/* The confirm pair renders only once usage is known; cancelling issues
        no delete request. */}
      {usage !== null && loadError === null && (
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={deleting}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => void handleConfirm()}
            disabled={deleting}
            data-testid="face-delete-confirm"
          >
            {deleting ? 'Deleting…' : 'Delete face'}
          </Button>
        </div>
      )}
    </Modal>
  )
}
