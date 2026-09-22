import { useMemo, useState } from 'react'
import { buildHierarchy } from '../components/Sidebar'
import { SchemaControl } from '../components/SchemaControl'
import { PresetSelector } from '../components/PresetSelector'
import { isControlEnabled } from '../lib/gates'
import { useSettings } from '../state/SettingsContext'

export function MobileControlsView() {
  const {
    projectId,
    schema,
    values,
    setValue,
    dirty,
    saving,
    saveError,
    save,
    discard,
    applyPresetValues,
  } = useSettings()

  const [searchQuery, setSearchQuery] = useState('')
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    'Face Swapper': true,
  })
  const [justSaved, setJustSaved] = useState(false)

  const dirtyCount = Object.keys(dirty).length

  // Deciding gated keys
  const disabledKeys = useMemo(() => {
    if (!schema || !values) return new Set<string>()
    const disabled = new Set<string>()
    for (const key of Object.keys(schema.widgets)) {
      if (!isControlEnabled(key, schema.widgets[key], values, schema.widgets)) {
        disabled.add(key)
      }
    }
    return disabled
  }, [schema, values])

  const hierarchy = useMemo(
    () => (schema ? buildHierarchy(schema.widgets) : []),
    [schema],
  )

  const { allGroups, groupKeys } = useMemo(() => {
    const names: string[] = []
    const keysMap: Record<string, string[]> = {}
    for (const tier of hierarchy) {
      for (const group of tier.groups) {
        if (!names.includes(group.name)) names.push(group.name)
        keysMap[group.name] = group.keys
      }
    }
    return { allGroups: names, groupKeys: keysMap }
  }, [hierarchy])

  // Filter groups / keys by search query
  const filteredGroups = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q || !schema) return allGroups

    return allGroups.filter((groupName) => {
      if (groupName.toLowerCase().includes(q)) return true
      const keys = groupKeys[groupName] ?? []
      return keys.some((k) => {
        const widget = schema.widgets[k]
        return (
          k.toLowerCase().includes(q) ||
          (widget?.label && widget.label.toLowerCase().includes(q))
        )
      })
    })
  }, [searchQuery, allGroups, groupKeys, schema])

  const toggleGroup = (group: string) => {
    setExpandedGroups((prev) => ({
      ...prev,
      [group]: !prev[group],
    }))
  }

  const handleSave = async () => {
    await save()
    setJustSaved(true)
    setTimeout(() => setJustSaved(false), 2000)
  }

  if (!schema || !values) {
    return (
      <div className="p-8 text-center text-sm text-muted">
        Loading schema controls…
      </div>
    )
  }

  return (
    <div className="space-y-4 p-4 pb-32">
      {/* Search & Preset Row */}
      <div className="space-y-2">
        <div className="relative">
          <input
            type="text"
            placeholder="Search 200+ controls..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full rounded-xl border border-line bg-card py-2.5 pr-8 pl-9 text-sm text-text placeholder-muted focus:border-accent focus:outline-none"
          />
          <svg
            className="absolute top-3 left-3 h-4 w-4 text-muted"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute top-2.5 right-2.5 rounded-full p-1 text-muted hover:text-text"
            >
              ✕
            </button>
          )}
        </div>

        {/* Preset selector */}
        <div className="flex items-center justify-between rounded-xl border border-line bg-card px-3 py-2">
          <span className="text-xs font-medium text-muted">Preset:</span>
          <div className="max-w-[200px]">
            <PresetSelector
              projectId={projectId}
              values={values}
              dirty={dirty}
              onChangeValues={applyPresetValues}
            />
          </div>
        </div>
      </div>

      {saveError && (
        <p className="rounded-xl bg-bad/10 p-3 text-xs text-bad">{saveError}</p>
      )}
      {justSaved && (
        <p className="rounded-xl bg-good/10 p-3 text-xs text-good font-medium">Changes saved successfully!</p>
      )}

      {/* Accordion groups */}
      <div className="space-y-3">
        {filteredGroups.map((group) => {
          const keys = groupKeys[group] ?? []
          const isExpanded = expandedGroups[group] ?? (searchQuery.trim().length > 0)
          const q = searchQuery.trim().toLowerCase()

          // Filter keys within group if searching
          const visibleKeys = q
            ? keys.filter((k) => {
                const widget = schema.widgets[k]
                return (
                  group.toLowerCase().includes(q) ||
                  k.toLowerCase().includes(q) ||
                  (widget?.label && widget.label.toLowerCase().includes(q))
                )
              })
            : keys

          return (
            <div
              key={group}
              className="overflow-hidden rounded-2xl border border-line bg-card shadow-sm"
            >
              <button
                type="button"
                onClick={() => toggleGroup(group)}
                className="flex w-full items-center justify-between bg-raised px-4 py-3 text-left active:bg-active"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-text">{group}</span>
                  <span className="rounded-full bg-line/80 px-2 py-0.5 text-[10px] font-medium text-muted">
                    {visibleKeys.length}
                  </span>
                </div>
                <svg
                  className={`h-4 w-4 text-muted transition-transform duration-200 ${
                    isExpanded ? 'rotate-180' : ''
                  }`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 9l-7 7-7-7"
                  />
                </svg>
              </button>

              {isExpanded && (
                <div className="divide-y divide-line/40 px-3 py-1">
                  {visibleKeys.map((k) => {
                    const entry = schema.widgets[k]
                    if (!entry) return null
                    return (
                      <div key={k} className="py-2">
                        <SchemaControl
                          keyName={k}
                          entry={entry}
                          value={values[k]}
                          onChange={(v) => setValue(k, v)}
                          disabled={disabledKeys.has(k)}
                        />
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Floating Save / Discard Bar (when changes are dirty) */}
      {dirtyCount > 0 && (
        <div
          className="fixed right-3 left-3 z-40 flex items-center justify-between rounded-2xl border border-accent/40 bg-card/95 p-3 shadow-2xl backdrop-blur-md"
          style={{ bottom: 'calc(env(safe-area-inset-bottom, 0px) + 4.5rem)' }}
        >
          <div>
            <span className="block text-xs font-semibold text-text">
              {dirtyCount} unsaved {dirtyCount === 1 ? 'change' : 'changes'}
            </span>
            <span className="block text-[10px] text-muted">Remember to save project settings</span>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void discard()}
              disabled={saving}
              className="rounded-xl border border-line bg-raised px-3 py-2 text-xs font-semibold text-text active:bg-active disabled:opacity-50"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-xl bg-accent px-4 py-2 text-xs font-bold text-bg shadow-md shadow-accent/20 active:scale-95 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
