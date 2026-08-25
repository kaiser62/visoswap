/** Confirmation for deleting a project.
 *
 * Deleting a project takes its generated frames and its source file with it
 * (backend/api/projects.py delete_project), so this is named plainly rather
 * than described as "removing" anything — there is no undo and no recycle bin.
 */

import { Button, Modal } from './ui'

export function ProjectDeleteDialog({
  open,
  name,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  open: boolean
  name: string
  busy: boolean
  error: string | null
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Modal open={open} onClose={onCancel} title="Delete project">
      <p className="mb-4 text-sm text-text" data-testid="project-delete-body">
        Delete <span className="font-semibold">{name}</span>? Its video, its
        generated frames and its recording are deleted with it. This can't be
        undone.
      </p>
      {error !== null && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          onClick={onConfirm}
          disabled={busy}
          data-testid="btn-confirm-delete-project"
        >
          {busy ? 'Deleting…' : 'Delete'}
        </Button>
      </div>
    </Modal>
  )
}
