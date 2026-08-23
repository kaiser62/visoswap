/** One logical group as a Card of schema controls, in schema order. */

import type { SchemaEntry, SettingValue } from '../types'
import { SchemaControl } from './SchemaControl'

interface GroupSectionProps {
  name: string
  keys: string[]
  widgets: Record<string, SchemaEntry>
  values: Record<string, SettingValue> | null
  onChange?: (key: string, value: SettingValue) => void
  disabled?: boolean
  disabledKeys?: Set<string>
  id?: string
}

export function GroupSection({
  name,
  keys,
  widgets,
  values,
  onChange,
  disabled = false,
  disabledKeys,
  id,
}: GroupSectionProps) {
  return (
    <section id={id} className="rounded-lg border border-neutral-200 bg-white p-4">
      <h2 className="mb-2 text-base font-semibold text-neutral-900">{name}</h2>
      {keys.map((key) => {
        const entry = widgets[key]
        if (!entry) return null
        return (
          <SchemaControl
            key={key}
            keyName={key}
            entry={entry}
            value={values?.[key]}
            onChange={onChange ? (v) => onChange(key, v) : undefined}
            disabled={disabled || disabledKeys?.has(key) === true}
          />
        )
      })}
    </section>
  )
}
