/** Destructive confirmation modal for Discard (UI-SPEC). */

import { Button, Modal } from './ui'

export function ConfirmModal({
  open,
  count,
  onCancel,
  onConfirm,
}: {
  open: boolean
  count: number
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Modal open={open} onClose={onCancel} title="Discard changes">
      <p className="mb-4 text-sm text-text">
        This discards {count} unsaved change(s). This can't be undone.
      </p>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="destructive" onClick={onConfirm}>
          Discard
        </Button>
      </div>
    </Modal>
  )
}
