/** Types mirroring visoswap/schema/schema.json — the single source the UI renders from. */

export type SettingsType = 'toggle' | 'int' | 'float' | 'selection' | 'text'

export interface SchemaGate {
  mechanism: 'toggle' | 'selection'
  parents: string[]
  required_value: boolean | string
  rule: 'single' | 'all' | 'last'
}

export interface SchemaEntry {
  type: SettingsType
  tier: 'global' | 'project'
  group: string
  label: string
  help?: string
  level?: number
  default?: unknown
  minimum?: number
  maximum?: number
  step?: number
  decimals?: number
  options?: string[] | null
  options_from?: string | null
  gate?: SchemaGate | null
  max_length?: number
  min_length?: number
}

export type SettingValue = boolean | number | string
export type Values = Record<string, SettingValue>

export interface SchemaHeader {
  generated?: string
  licence?: string
  source?: string
  version?: string
}

export interface SchemaDocument {
  schema: SchemaHeader
  widgets: Record<string, SchemaEntry>
}

export interface Project {
  id: string
  name: string
}

export interface Preset {
  id: string
  name: string
  project: Record<string, SettingValue>
  global: Record<string, SettingValue>
  created_at: string
  updated_at: string
}

export interface SettingsResponse {
  values: Values
}

export interface PresetApplyResponse {
  report: {
    project_written: number
    project_cleared: number
    global_written: number
    global_cleared: number
    unavailable: string[]
  }
  values: Values
}

export type LoadStatus = 'loading' | 'ready' | 'error'
