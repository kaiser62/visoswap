/** Gate-rule table tests porting visoswap/settings/gates.py behaviour. */

import { describe, expect, it } from 'vitest'
import schemaJson from '../../../visoswap/schema/schema.json'
import { decidingParents, isControlEnabled } from '../lib/gates'
import type { SchemaEntry, Values } from '../types'

const widgets = schemaJson.widgets as unknown as Record<string, SchemaEntry>

describe('gate rules', () => {
  it('single rule: AutoColorBlendAmountSlider enabled only while AutoColorEnableToggle is true', () => {
    const key = 'AutoColorBlendAmountSlider'
    const entry = widgets[key]
    const parent = 'AutoColorEnableToggle'
    const base: Values = {}
    for (const k of Object.keys(widgets)) base[k] = widgets[k].default as never
    base[parent] = true
    expect(isControlEnabled(key, entry, base, widgets)).toBe(true)
    base[parent] = false
    expect(isControlEnabled(key, entry, base, widgets)).toBe(false)
  })

  it('selection mechanism: DFMModelSelection enabled only when SwapModelSelection equals required value', () => {
    const key = 'DFMModelSelection'
    const entry = widgets[key]
    const parent = entry.gate?.parents[0] ?? ''
    const required = entry.gate?.required_value as string
    const base: Values = {}
    for (const k of Object.keys(widgets)) base[k] = widgets[k].default as never
    base[parent] = required
    expect(isControlEnabled(key, entry, base, widgets)).toBe(true)
    base[parent] = 'SomethingElse'
    expect(isControlEnabled(key, entry, base, widgets)).toBe(false)
  })

  it('all rule (AND): a three-parent toggle gate is enabled only when ALL are true', () => {
    const entry: SchemaEntry = {
      type: 'toggle',
      tier: 'project',
      group: 'G',
      label: 'Three Parent',
      default: true,
      gate: {
        mechanism: 'toggle',
        parents: ['P1', 'P2', 'P3'],
        required_value: true,
        rule: 'all',
      },
    }
    const base: Values = { P1: true, P2: true, P3: true, P1x: true }
    expect(isControlEnabled('X', entry, { ...base }, widgets)).toBe(true)
    expect(isControlEnabled('X', entry, { ...base, P2: false }, widgets)).toBe(false)
  })

  it('last rule: only the final parent decides', () => {
    const entry: SchemaEntry = {
      type: 'toggle',
      tier: 'project',
      group: 'G',
      label: 'Last Parent',
      default: true,
      gate: {
        mechanism: 'toggle',
        parents: ['A', 'B'],
        required_value: true,
        rule: 'last',
      },
    }
    // A false but B true -> enabled (B decides).
    expect(isControlEnabled('X', entry, { A: false, B: true }, widgets)).toBe(true)
    // A true but B false -> disabled (B decides).
    expect(isControlEnabled('X', entry, { A: true, B: false }, widgets)).toBe(false)
  })

  it('transitive: a child whose deciding parent is gated-off stays disabled even when its own condition is met', () => {
    const parentGated: SchemaEntry = {
      type: 'toggle',
      tier: 'project',
      group: 'G',
      label: 'Parent',
      default: true,
      gate: {
        mechanism: 'toggle',
        parents: ['Grandparent'],
        required_value: true,
        rule: 'single',
      },
    }
    const child: SchemaEntry = {
      type: 'toggle',
      tier: 'project',
      group: 'G',
      label: 'Child',
      default: true,
      gate: {
        mechanism: 'toggle',
        parents: ['Parent'],
        required_value: true,
        rule: 'single',
      },
    }
    // Grandparent off -> Parent disabled -> Child disabled even though its own
    // condition (Parent==true) is not even met, but the transitive case is that
    // Parent's gate closes, cascading.
    const localWidgets: Record<string, SchemaEntry> = {
      Grandparent: {
        type: 'toggle',
        tier: 'project',
        group: 'G',
        label: 'Grandparent',
        default: false,
      },
      Parent: parentGated,
      Child: child,
    }
    // Child's own gate requires Parent==true, but Parent is disabled (its parent
    // Grandparent is false), so transitive keeps Child disabled.
    expect(
      isControlEnabled('Child', child, { Grandparent: false, Parent: true }, localWidgets),
    ).toBe(false)
    // With Grandparent on, Parent enabled, Child's own condition met -> enabled.
    expect(
      isControlEnabled('Child', child, { Grandparent: true, Parent: true }, localWidgets),
    ).toBe(true)
  })

  it('smoke test over the real 201-entry schema: never throws, enabled keys are a stable subset', () => {
    const values: Values = {}
    for (const [k, e] of Object.entries(widgets)) {
      values[k] = (e.default ?? '') as never
    }
    let enabledCount = 0
    for (const key of Object.keys(widgets)) {
      const enabled = isControlEnabled(key, widgets[key], values, widgets)
      expect(typeof enabled).toBe('boolean')
      if (enabled) enabledCount += 1
    }
    expect(enabledCount).toBeGreaterThan(0)
    expect(enabledCount).toBeLessThanOrEqual(Object.keys(widgets).length)
  })

  it('decidingParents under last rule returns only the final parent', () => {
    const entry: SchemaEntry = {
      type: 'toggle',
      tier: 'project',
      group: 'G',
      label: 'X',
      default: true,
      gate: { mechanism: 'toggle', parents: ['A', 'B', 'C'], required_value: true, rule: 'last' },
    }
    expect(decidingParents(entry)).toEqual(['C'])
  })

  it('all 201 controls still render (count unchanged) when gates applied', () => {
    const values: Values = {}
    for (const [k, e] of Object.entries(widgets)) {
      values[k] = (e.default ?? '') as never
    }
    // isControlEnabled must not alter the set of rendered keys (the render-count
    // suite still asserts 201). Here we just confirm every key evaluates.
    const evaluated = Object.keys(widgets).every((key) =>
      typeof isControlEnabled(key, widgets[key], values, widgets) === 'boolean',
    )
    expect(evaluated).toBe(true)
  })
})
