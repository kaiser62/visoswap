/** Generic schema-driven control (D-03).
 *
 * Dispatches exclusively on `entry.type` — never on the settings key string.
 * No per-key mapping, no key-name-substring inference. Every control carries
 * `data-key` and `data-control-type` so the count/type tests can read them.
 */

import type { SchemaEntry, SettingValue } from '../types'

interface SchemaControlProps {
  keyName: string
  entry: SchemaEntry
  value: SettingValue | undefined
  onChange?: (v: SettingValue) => void
  disabled?: boolean
}

export function SchemaControl({
  keyName,
  entry,
  value,
  onChange,
  disabled = false,
}: SchemaControlProps) {
  const stepFor = () => {
    if (entry.step !== undefined) return entry.step
    return entry.type === 'float' ? 0.01 : 1
  }
  const isNumeric = entry.type === 'int' || entry.type === 'float'
  const min = entry.minimum ?? (isNumeric ? 0 : undefined)
  const max = entry.maximum ?? (isNumeric ? 100 : undefined)

  /** Parse a numeric input, or null when it is not a usable number.
   *
   * A cleared field parses to NaN and JSON.stringify would ship `null`, which
   * the API rejects (422); a hand-typed value bypasses the input's min/max, so
   * the parsed number is clamped into the schema bounds — the same contract
   * store.validate enforces server-side. The change simply doesn't fire when
   * there is nothing valid to send.
   */
  const toValue = (raw: string): SettingValue | null => {
    const parsed = entry.type === 'int' ? parseInt(raw, 10) : parseFloat(raw)
    if (!Number.isFinite(parsed)) return null
    let n = parsed
    if (min !== undefined) n = Math.max(min, n)
    if (max !== undefined) n = Math.min(max, n)
    if (entry.decimals !== undefined) {
      n = parseFloat(n.toFixed(entry.decimals))
    }
    return n
  }

  const emitNumber = (raw: string) => {
    const v = toValue(raw)
    if (v !== null) onChange?.(v)
  }

  const ctl = { 'data-key': keyName, 'data-control-type': entry.type }

  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1 text-sm font-semibold text-neutral-900">
          {disabled && (
            <span title="Locked: enable the deciding control to edit this" aria-hidden>
              🔒
            </span>
          )}
          <span>{entry.label}</span>
        </div>
        {entry.help ? (
          <div
            className="mt-0.5 line-clamp-2 text-sm text-neutral-500"
            title={entry.help}
          >
            {entry.help}
          </div>
        ) : null}
      </div>

      <div className="flex h-8 shrink-0 items-center gap-2">{renderControl()}</div>
    </div>
  )

  function renderControl() {
    switch (entry.type) {
      case 'toggle':
        return (
          <input
            type="checkbox"
            {...ctl}
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(e) => onChange?.(e.target.checked)}
            className="h-4 w-4 accent-blue-600"
            aria-label={entry.label}
          />
        )
      case 'int':
      case 'float':
        return (
          <div className="flex items-center gap-2">
            <input
              type="range"
              {...ctl}
              min={min}
              max={max}
              step={stepFor()}
              value={Number(value ?? 0)}
              disabled={disabled}
              onChange={(e) => emitNumber(e.target.value)}
              aria-label={entry.label}
              className="w-40 accent-blue-600"
            />
            <input
              type="number"
              min={min}
              max={max}
              step={stepFor()}
              value={Number(value ?? 0)}
              disabled={disabled}
              onChange={(e) => emitNumber(e.target.value)}
              aria-label={entry.label}
              className="h-8 w-20 rounded border border-neutral-200 px-2 text-sm text-neutral-900"
            />
          </div>
        )
      case 'selection': {
        const options = entry.options ?? []
        if (options.length === 0) {
          return (
            <select
              {...ctl}
              disabled
              aria-label={entry.label}
              className="h-8 w-40 rounded border border-neutral-200 bg-neutral-100 px-2 text-sm text-neutral-400"
            >
              <option>Unavailable</option>
            </select>
          )
        }
        return (
          <select
            {...ctl}
            value={String(value ?? options[0])}
            disabled={disabled}
            onChange={(e) => onChange?.(e.target.value)}
            aria-label={entry.label}
            className="h-8 w-40 rounded border border-neutral-200 px-2 text-sm text-neutral-900"
          >
            {options.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        )
      }
      case 'text':
        return (
          <input
            type="text"
            {...ctl}
            value={String(value ?? '')}
            maxLength={entry.max_length}
            disabled={disabled}
            onChange={(e) => onChange?.(e.target.value)}
            aria-label={entry.label}
            className="h-8 w-40 rounded border border-neutral-200 px-2 text-sm text-neutral-900"
          />
        )
      default:
        return null
    }
  }
}
