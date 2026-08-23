/** Pure preset-vs-current-values diff (D-04).
 *
 * changedKeys are the preset's project-tier keys whose value differs from the
 * current values; differsFromUnsaved are those also present in the dirty map
 * (so the modal can note keys the user has unsaved edits on).
 */

import type { Preset, Values } from '../types'

export interface PresetDiff {
  changedKeys: string[]
  differsFromUnsaved: string[]
}

export function diffPreset(
  preset: Preset,
  values: Values | null,
  dirty: Record<string, true>,
): PresetDiff {
  const current = values ?? {}
  const changedKeys: string[] = []
  for (const [key, value] of Object.entries(preset.project)) {
    if (!(key in current) || current[key] !== value) {
      changedKeys.push(key)
    }
  }
  const differsFromUnsaved = changedKeys.filter((k) => dirty[k] === true)
  return { changedKeys, differsFromUnsaved }
}
