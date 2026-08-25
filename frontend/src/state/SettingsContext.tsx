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
  createProject,
  deleteProject,
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
  refreshProjects: () => Promise<void>
  newProject: (name?: string) => Promise<string>
  removeProject: (projectId: string) => Promise<void>
  setValue: (key: string, value: SettingValue) => void
  save: () => Promise<void>
  discard: () => void
  applyPresetValues: (values: Values) => void
}

/** Exported for tests that need to inject a settings value identity change
 *  without booting the whole provider (schema + values fetches). */
export const SettingsContext = createContext<SettingsContextValue | null>(null)

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
      const fromUrl = new URLSearchParams(window.location.search).get('project')
      // No project in the URL: fall back to the most recently updated one, or
      // create the first. A settings surface with no selected project cannot
      // save (writes are per-project), so leaving projectId null would turn
      // every Save into a silent no-op — edits lost on reload with no error.
      // The list is fetched unconditionally. Fetching it only on the fallback
      // path left `projects` empty whenever the URL named a project, and the
      // header picker renders nothing when the list is empty — opening a
      // project by URL silently removed the only way to reach the others.
      // Failing to list projects is not failing to load: with a project named
      // in the URL the studio is fully usable, it just cannot offer the
      // picker. Only the fallback path below actually needs the list, and it
      // reports its own failure by throwing.
      let projects: Project[] = []
      try {
        projects = await listProjects()
      } catch {
        if (!fromUrl) throw new Error('cannot resolve a project without the list')
      }
      let target = fromUrl
      if (!target) {
        if (projects[0]) {
          target = projects[0].id
        } else {
          const created = await createProject('My project')
          target = created.id
          projects.unshift(created)
        }
      }
      dispatch({ type: 'SET_PROJECTS', projects })
      const values = (await getProjectSettings(target)).values
      dispatch({ type: 'SET_PROJECT', projectId: target })
      // Keep the URL in sync so a plain reload reopens the same project.
      if (fromUrl !== target) {
        window.history.replaceState({}, '', `/?project=${target}`)
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
      // Switching projects rewrites the URL too, so a reload reopens what is
      // on screen rather than whatever the address bar still remembers.
      window.history.replaceState({}, '', `/?project=${projectId}`)
      dispatch({ type: 'LOAD_OK', schema, values })
    } catch {
      dispatch({ type: 'LOAD_ERROR' })
    }
  }, [])

  const refreshProjects = useCallback(async () => {
    dispatch({ type: 'SET_PROJECTS', projects: await listProjects() })
  }, [])

  /** Create a project and open it. Returns the new id so a caller can react. */
  const newProject = useCallback(
    async (name?: string) => {
      const created = await createProject(name)
      await refreshProjects()
      await loadProject(created.id)
      return created.id
    },
    [loadProject, refreshProjects],
  )

  /** Delete a project. Deleting the open one falls through to whatever remains,
   *  and to a freshly created project when it was the last: the studio has no
   *  meaningful state with no project selected, and every Save would no-op. */
  const removeProject = useCallback(
    async (projectId: string) => {
      await deleteProject(projectId)
      const remaining = await listProjects()
      dispatch({ type: 'SET_PROJECTS', projects: remaining })
      if (state.projectId !== projectId) return
      if (remaining[0]) {
        await loadProject(remaining[0].id)
      } else {
        const created = await createProject('My project')
        dispatch({ type: 'SET_PROJECTS', projects: [created] })
        await loadProject(created.id)
      }
    },
    [loadProject, state.projectId],
  )

  const setProject = useCallback((projectId: string) => {
    void loadProject(projectId)
  }, [loadProject])

  const setValue = useCallback((key: string, value: SettingValue) => {
    dispatch({ type: 'SET_VALUE', key, value })
  }, [])

  const save = useCallback(async () => {
    const projectId = state.projectId
    // Unreachable since load() auto-selects a project — kept as a loud guard:
    // a silent no-op here is exactly how edits appear to save and do not.
    if (!projectId) {
      dispatch({
        type: 'SAVE_ERROR',
        message: "Couldn't save your changes. No project is selected — pick one in the header and try again.",
      })
      return
    }
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
      refreshProjects,
      newProject,
      removeProject,
      setValue,
      save,
      discard,
      applyPresetValues,
    }),
    [
      state,
      load,
      loadProject,
      setProject,
      refreshProjects,
      newProject,
      removeProject,
      setValue,
      save,
      discard,
      applyPresetValues,
    ],
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

/**
 * Nullable variant for media components that must render inside a bare
 * MediaProvider too (unit tests, the player action row). Outside a provider
 * there are no settings values, so auto-preview simply has one fewer trigger.
 */
export function useSettingsOptional(): SettingsContextValue | null {
  return useContext(SettingsContext)
}
