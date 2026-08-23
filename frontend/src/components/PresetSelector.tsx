/** Preset selector (D-04): a select in the header; choosing opens the diff modal. */

import { useEffect, useState } from 'react'
import { getPresets } from '../lib/api'
import type { Preset } from '../types'
import { PresetDiffModal } from './PresetDiffModal'

export function PresetSelector({
  projectId,
  values,
  dirty,
  onChangeValues,
}: {
  projectId: string | null
  values: Record<string, boolean | number | string> | null
  dirty: Record<string, true>
  onChangeValues: (values: Record<string, boolean | number | string>) => void
}) {
  const [presets, setPresets] = useState<Preset[]>([])
  const [selected, setSelected] = useState<Preset | null>(null)

  useEffect(() => {
    getPresets()
      .then((resp) => setPresets(resp.presets))
      .catch(() => setPresets([]))
  }, [])

  const handleChange = (id: string) => {
    const preset = presets.find((p) => p.id === id)
    if (preset) setSelected(preset)
  }

  return (
    <>
      {presets.length === 0 ? (
        <select
          disabled
          aria-label="Preset"
          className="h-8 rounded border border-line bg-raised px-2 text-sm text-muted"
        >
          <option>No presets</option>
        </select>
      ) : (
        <select
          value=""
          onChange={(e) => handleChange(e.target.value)}
          aria-label="Preset"
          className="h-8 rounded border border-line bg-raised px-2 text-sm text-text"
        >
          <option value="" disabled>
            Apply preset…
          </option>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      )}
      <PresetDiffModal
        preset={selected}
        projectId={projectId}
        values={values}
        dirty={dirty}
        onClose={() => setSelected(null)}
        onApply={(respValues) => {
          onChangeValues(respValues)
          setSelected(null)
        }}
      />
    </>
  )
}
