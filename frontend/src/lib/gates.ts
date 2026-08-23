/** Port of visoswap/settings/gates.py semantics (frontend).
 *
 * Which controls are enabled, given the full resolved values map. Gating is
 * presentational only: disabling a control here never changes what is saved or
 * resolved — the engine reads all 201 parameters unconditionally.
 *
 * Rules (matching gates.py exactly):
 *  - mechanism "toggle": satisfied when the combined checked state of the
 *    deciding parents equals required_value. Rule "single": one parent.
 *    Rule "all": AND (starts true, any unchecked parent clears). Rule "last":
 *    only the FINAL parent decides.
 *  - mechanism "selection": satisfied when the single parent's value equals
 *    required_value exactly.
 *  - deciding parents = gate.parents, except under "last" where only the last
 *    parent decides.
 *  - Transitive: a key is enabled only when its own gate is satisfied AND every
 *    deciding parent is itself enabled.
 */

import type { SchemaEntry, Values } from '../types'

export function decidingParents(entry: SchemaEntry): string[] {
  const gate = entry.gate
  if (!gate) return []
  if (gate.rule === 'last') return gate.parents.slice(-1)
  return [...gate.parents]
}

function ownGateSatisfied(entry: SchemaEntry, values: Values): boolean {
  const gate = entry.gate
  if (!gate) return true
  const parents = decidingParents(entry)
  if (gate.mechanism === 'selection') {
    return values[parents[parents.length - 1]] === gate.required_value
  }
  // Toggle. single/last reduce to one parent; all is the AND its pipe disguises.
  let combined = true
  for (const parent of parents) {
    if (!values[parent]) combined = false
  }
  return combined === gate.required_value
}

export function isControlEnabled(
  key: string,
  entry: SchemaEntry,
  values: Values | null,
  widgets: Record<string, SchemaEntry> = {},
  chain: string[] = [],
): boolean {
  const vals = values ?? {}
  if (chain.includes(key)) {
    // Cycle guard (defensive — the committed schema is acyclic).
    return false
  }
  if (!entry.gate) return true
  if (!ownGateSatisfied(entry, vals)) return false
  const onward = [...chain, key]
  return decidingParents(entry).every((parent) => {
    // A deciding parent absent from the schema has no gate — enabled. Never
    // fall back to the child's own entry: that would re-enter the same gate
    // and trip the cycle guard.
    const parentEntry = widgets[parent]
    if (!parentEntry) return true
    return isControlEnabled(parent, parentEntry, vals, widgets, onward)
  })
}
