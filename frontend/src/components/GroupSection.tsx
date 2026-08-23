/** One logical group as a card of schema controls, in schema order.
 *
 * Restyled to the Studio tokens (plan 05.1-04 Task 2): the heading gets the
 * raised surface webui2 gives card headers, and the control rows read as rows
 * via hairline separators. The id prop stays the scroll-spy anchor.
 */

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
    <section id={id} className="overflow-hidden rounded-lg border border-line bg-card">
      <h2 className="flex min-h-11 items-center border-b border-line bg-raised px-4 text-[15px] font-semibold text-text">
        {name}
      </h2>
      <div className="divide-y divide-line px-4 py-1">
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
      </div>
    </section>
  )
}
