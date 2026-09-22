import { useState, useEffect } from 'react'
import { Modal, Button } from './ui'
import { renameFaceGroup, updateFace } from '../lib/api'
import type { Face } from '../types'

interface RenameGroupModalProps {
  open: boolean
  oldName: string | null
  onClose: () => void
  onRenamed: () => void
}

export function RenameGroupModal({
  open,
  oldName,
  onClose,
  onRenamed,
}: RenameGroupModalProps) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (oldName) {
      setName(oldName)
      setError(null)
    }
  }, [oldName, open])

  if (!open || !oldName) return null

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) {
      setError('Group name cannot be empty')
      return
    }
    if (trimmed === oldName) {
      onClose()
      return
    }
    setSaving(true)
    setError(null)
    try {
      await renameFaceGroup(oldName, trimmed)
      onRenamed()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rename group')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Rename Face Group">
      <form onSubmit={handleSave} className="space-y-4">
        <div>
          <label className="mb-1 block text-xs font-semibold text-muted">
            Group Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            autoFocus
            className="w-full rounded-lg border border-line bg-raised px-3 py-2 text-sm text-text focus:border-accent focus:outline-none"
            placeholder="e.g. Person Name"
          />
        </div>

        {error && <p className="text-xs text-bad">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? 'Saving…' : 'Rename Group'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

interface AssignFaceGroupModalProps {
  open: boolean
  face: Face | null
  existingGroups: string[]
  onClose: () => void
  onAssigned: () => void
}

export function AssignFaceGroupModal({
  open,
  face,
  existingGroups,
  onClose,
  onAssigned,
}: AssignFaceGroupModalProps) {
  const [customGroup, setCustomGroup] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (face) {
      setCustomGroup(face.group ?? '')
      setError(null)
    }
  }, [face, open])

  if (!open || !face) return null

  const handleApply = async (newGroup: string | null) => {
    setSaving(true)
    setError(null)
    try {
      await updateFace(face.face_id, { group: newGroup })
      onAssigned()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update group')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Assign Face to Group">
      <div className="space-y-4">
        <div className="flex items-center gap-3 rounded-lg border border-line/50 bg-raised/40 p-2.5">
          {face.thumbnail_url ? (
            <img
              src={face.thumbnail_url}
              alt=""
              className="h-12 w-12 rounded-lg object-cover"
            />
          ) : (
            <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-raised text-muted">
              ?
            </div>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-semibold text-text">
              {face.display_name ?? face.face_id}
            </p>
            <p className="text-[11px] text-muted">
              Current group: <span className="font-semibold text-accent">{face.group || 'None (Ungrouped)'}</span>
            </p>
          </div>
        </div>

        {/* Existing groups quick pick */}
        {existingGroups.length > 0 && (
          <div>
            <span className="mb-1.5 block text-xs font-semibold text-muted">
              Choose from existing groups
            </span>
            <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto rounded-lg border border-line/40 bg-raised/20 p-2">
              {existingGroups.map((g) => (
                <button
                  key={g}
                  type="button"
                  onClick={() => void handleApply(g)}
                  disabled={saving}
                  className={`rounded-full px-2.5 py-1 text-xs font-medium transition-all ${
                    face.group === g
                      ? 'bg-accent font-bold text-bg ring-2 ring-accent'
                      : 'bg-card text-text border border-line hover:border-accent hover:text-accent'
                  }`}
                >
                  {g}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Or enter new group name */}
        <div>
          <label className="mb-1 block text-xs font-semibold text-muted">
            Or type new group name
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={customGroup}
              onChange={(e) => setCustomGroup(e.target.value)}
              placeholder="e.g. Person Name"
              maxLength={80}
              className="flex-1 rounded-lg border border-line bg-raised px-3 py-2 text-sm text-text focus:border-accent focus:outline-none"
            />
            <Button
              type="button"
              variant="primary"
              disabled={saving || !customGroup.trim()}
              onClick={() => void handleApply(customGroup.trim())}
            >
              Set
            </Button>
          </div>
        </div>

        {face.group && (
          <div className="pt-1">
            <button
              type="button"
              onClick={() => void handleApply(null)}
              disabled={saving}
              className="text-xs text-bad hover:underline font-semibold"
            >
              Remove from group "{face.group}"
            </button>
          </div>
        )}

        {error && <p className="text-xs text-bad">{error}</p>}

        <div className="flex justify-end pt-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={saving}
          >
            Cancel
          </Button>
        </div>
      </div>
    </Modal>
  )
}
