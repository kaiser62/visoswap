/** Apply-preset confirmation modal with the settings diff (D-04). */

import { useState } from 'react'
import { applyPreset } from '../lib/api'
import { diffPreset } from '../lib/diff'
import type { Preset, Values } from '../types'
import { Button, Modal } from './ui'

export function PresetDiffModal({
  preset,
  projectId,
  values,
  dirty,
  onClose,
  onApply,
}: {
  preset: Preset | null
  projectId: string | null
  values: Values | null
  dirty: Record<string, true>
  onClose: () => void
  onApply: (values: Values) => void
}) {
  const [error, setError] = useState<string | null>(null)

  if (!preset) return null
  const { changedKeys, differsFromUnsaved } = diffPreset(preset, values, dirty)

  const handleApply = async () => {
    setError(null)
    if (!projectId) {
      setError("Couldn't apply the preset. No project is selected.")
      return
    }
    try {
      const resp = await applyPreset(projectId, preset.id)
      onApply(resp.values)
    } catch {
      setError(
        "Couldn't apply the preset. The backend didn't respond — nothing was changed. Try again.",
      )
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`Apply preset ${preset.name}`}
    >
      <p className="mb-2 text-sm text-neutral-700">
        Changes {changedKeys.length} setting(s) to this preset's values.
      </p>
      {differsFromUnsaved.length > 0 && (
        <p className="mb-2 text-sm text-amber-700">
          Note: {differsFromUnsaved.length} of these differ from your unsaved
          edits.
        </p>
      )}
      <ul className="mb-4 max-h-48 overflow-y-auto rounded border border-neutral-200 p-2 text-sm">
        {changedKeys.map((key) => (
          <li key={key} className="flex items-center justify-between py-1">
            <span className="text-neutral-700">{key}</span>
            <span className="text-neutral-400">
              {String(values?.[key])} → {String(preset.project[key])}
            </span>
          </li>
        ))}
      </ul>
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={() => void handleApply()}>Apply</Button>
      </div>
    </Modal>
  )
}
