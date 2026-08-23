/** React Context + useReducer state container (D-06).
 *
 * Holds schema, resolved values, projects, the selected project, load status,
 * a dirty map tracking which keys the user has edited since last save, the
 * last-persisted baseline (for Discard), and save state (saving/saveError).
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  type ReactNode,
} from 'react'
import {
  getProjectSettings,
  getSchema,
  listProjects,
  saveProjectSettings,
} from '../lib/api'
import type {
  LoadStatus,
  Project,
  SchemaDocument,
  SettingValue,
  Values,
} from '../types'

export interface SettingsState {
  status: LoadStatus
  schema: SchemaDocument | null
  values: Values | null
  projects: Project[]
  projectId: string | null
  dirty: Record<string, true>
  baselineValues: Values | null
  saving: boolean
  saveError: string | null
}

type Action =
  | { type: 'LOAD_START' }
  | { type: 'LOAD_OK'; schema: SchemaDocument; values: Values }
  | { type: 'LOAD_ERROR' }
  | { type: 'SET_PROJECTS'; projects: Project[] }
  | { type: 'SET_PROJECT'; projectId: string }
  | { type: 'SET_VALUE'; key: string; value: SettingValue }
  | { type: 'RESET_DIRTY' }
  | { type: 'SAVE_START' }
  | { type: 'SAVE_OK'; values: Values }
  | { type: 'SAVE_ERROR'; message: string }
  | { type: 'DISCARD'; values: Values }
  | { type: 'APPLY_PRESET'; values: Values }

const initialState: SettingsState = {
  status: 'loading',
  schema: null,
  values: null,
  projects: [],
  projectId: null,
  dirty: {},
  baselineValues: null,
  saving: false,
  saveError: null,
}

function reducer(state: SettingsState, action: Action): SettingsState {
  switch (action.type) {
    case 'LOAD_START':
      return { ...state, status: 'loading', saveError: null }
    case 'LOAD_OK':
      return {
        ...state,
        status: 'ready',
        schema: action.schema,
        values: action.values,
        baselineValues: action.values,
      }
    case 'LOAD_ERROR':
      return { ...state, status: 'error' }
    case 'SET_PROJECTS':
      return { ...state, projects: action.projects }
    case 'SET_PROJECT':
      return { ...state, projectId: action.projectId }
    case 'SET_VALUE':
      return {
        ...state,
        values: { ...(state.values ?? {}), [action.key]: action.value },
        dirty: { ...state.dirty, [action.key]: true },
        saveError: null,
      }
    case 'RESET_DIRTY':
      return { ...state, dirty: {} }
    case 'SAVE_START':
      return { ...state, saving: true, saveError: null }
    case 'SAVE_OK':
      return {
        ...state,
        saving: false,
        values: action.values,
        baselineValues: action.values,
        dirty: {},
      }
    case 'SAVE_ERROR':
      return { ...state, saving: false, saveError: action.message }
    case 'DISCARD':
      return {
        ...state,
        values: action.values,
        dirty: {},
        saveError: null,
      }
    case 'APPLY_PRESET':
      return {
        ...state,
        values: action.values,
        baselineValues: action.values,
        dirty: {},
        saveError: null,
      }
    default:
      return state
  }
}

interface SettingsContextValue extends SettingsState {
  load: () => Promise<void>
  loadProject: (projectId: string) => Promise<void>
  setProject: (projectId: string) => void
  setValue: (key: string, value: SettingValue) => void
  save: () => Promise<void>
  discard: () => void
  applyPresetValues: (values: Values) => void
}

const SettingsContext = createContext<SettingsContextValue | null>(null)

export function SettingsProvider({
  children,
}: {
  children: ReactNode
}) {
  const [state, dispatch] = useReducer(reducer, initialState)

  const load = useCallback(async () => {
    dispatch({ type: 'LOAD_START' })
    try {
      const schema = await getSchema()
      const projectId = new URLSearchParams(window.location.search).get('project')
      let values: Values = {}
      if (projectId) {
        values = (await getProjectSettings(projectId)).values
        dispatch({ type: 'SET_PROJECT', projectId })
      } else {
        const projects = await listProjects()
        dispatch({ type: 'SET_PROJECTS', projects })
      }
      dispatch({ type: 'LOAD_OK', schema, values })
    } catch {
      dispatch({ type: 'LOAD_ERROR' })
    }
  }, [])

  const loadProject = useCallback(async (projectId: string) => {
    dispatch({ type: 'LOAD_START' })
    try {
      const schema = await getSchema()
      const values = (await getProjectSettings(projectId)).values
      dispatch({ type: 'SET_PROJECT', projectId })
      dispatch({ type: 'LOAD_OK', schema, values })
    } catch {
      dispatch({ type: 'LOAD_ERROR' })
    }
  }, [])

  const setProject = useCallback((projectId: string) => {
    void loadProject(projectId)
  }, [loadProject])

  const setValue = useCallback((key: string, value: SettingValue) => {
    dispatch({ type: 'SET_VALUE', key, value })
  }, [])

  const save = useCallback(async () => {
    const projectId = state.projectId
    if (!projectId) return
    const overrides: Values = {}
    for (const key of Object.keys(state.dirty)) {
      if (state.values && state.values[key] !== undefined) {
        overrides[key] = state.values[key]
      }
    }
    dispatch({ type: 'SAVE_START' })
    try {
      const resp = await saveProjectSettings(projectId, overrides)
      dispatch({ type: 'SAVE_OK', values: resp.values })
    } catch (err) {
      dispatch({
        type: 'SAVE_ERROR',
        message: "Couldn't save your changes. They're still in the editor — check the backend and try again.",
      })
      void err
    }
  }, [state.projectId, state.dirty, state.values])

  const discard = useCallback(() => {
    if (!state.baselineValues) return
    dispatch({ type: 'DISCARD', values: state.baselineValues })
  }, [state.baselineValues])

  const applyPresetValues = useCallback((values: Values) => {
    dispatch({ type: 'APPLY_PRESET', values })
  }, [])

  const value = useMemo<SettingsContextValue>(
    () => ({
      ...state,
      load,
      loadProject,
      setProject,
      setValue,
      save,
      discard,
      applyPresetValues,
    }),
    [state, load, loadProject, setProject, setValue, save, discard, applyPresetValues],
  )

  return (
    <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>
  )
}

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used within SettingsProvider')
  return ctx
}
