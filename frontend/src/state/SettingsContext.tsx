/** React Context + useReducer state container (D-06).
 *
 * Holds schema, resolved values, projects, the selected project, load status,
 * and a dirty map tracking which keys the user has edited since last save.
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
}

type Action =
  | { type: 'LOAD_START' }
  | { type: 'LOAD_OK'; schema: SchemaDocument; values: Values }
  | { type: 'LOAD_ERROR' }
  | { type: 'SET_PROJECTS'; projects: Project[] }
  | { type: 'SET_PROJECT'; projectId: string }
  | { type: 'SET_VALUE'; key: string; value: SettingValue }
  | { type: 'RESET_DIRTY' }

const initialState: SettingsState = {
  status: 'loading',
  schema: null,
  values: null,
  projects: [],
  projectId: null,
  dirty: {},
}

function reducer(state: SettingsState, action: Action): SettingsState {
  switch (action.type) {
    case 'LOAD_START':
      return { ...state, status: 'loading' }
    case 'LOAD_OK':
      return {
        ...state,
        status: 'ready',
        schema: action.schema,
        values: action.values,
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
      }
    case 'RESET_DIRTY':
      return { ...state, dirty: {} }
    default:
      return state
  }
}

interface SettingsContextValue extends SettingsState {
  load: () => Promise<void>
  loadProject: (projectId: string) => Promise<void>
  setProject: (projectId: string) => void
  setValue: (key: string, value: SettingValue) => void
  resetDirty: () => void
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

  const resetDirty = useCallback(() => {
    dispatch({ type: 'RESET_DIRTY' })
  }, [])

  const value = useMemo<SettingsContextValue>(
    () => ({ ...state, load, loadProject, setProject, setValue, resetDirty }),
    [state, load, loadProject, setProject, setValue, resetDirty],
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
