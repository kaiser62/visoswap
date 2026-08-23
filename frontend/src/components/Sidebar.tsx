/** Category sidebar (D-01): tier sections -> logical groups with counts. */

import type { SchemaEntry } from '../types'

export interface GroupDef {
  name: string
  keys: string[]
}

export interface TierDef {
  name: 'Global' | 'Project'
  groups: GroupDef[]
}

export function buildHierarchy(
  widgets: Record<string, SchemaEntry>,
): TierDef[] {
  const tiers: { name: 'Global' | 'Project'; groups: Map<string, string[]> }[] = [
    { name: 'Global', groups: new Map() },
    { name: 'Project', groups: new Map() },
  ]
  for (const [key, entry] of Object.entries(widgets)) {
    const tier = entry.tier === 'global' ? tiers[0] : tiers[1]
    const groupName = entry.group === '' ? 'Other' : entry.group
    const list = tier.groups.get(groupName)
    if (list) list.push(key)
    else tier.groups.set(groupName, [key])
  }
  return tiers.map((t) => ({
    name: t.name,
    groups: [...t.groups.entries()].map(([name, keys]) => ({ name, keys })),
  }))
}

export function Sidebar({
  hierarchy,
  activeGroup,
  onSelect,
  loading,
}: {
  hierarchy: TierDef[]
  activeGroup: string | null
  onSelect: (group: string) => void
  loading: boolean
}) {
  return (
    <nav
      className="sticky top-0 h-full overflow-y-auto border-r border-neutral-200 bg-white p-4"
      aria-label="Settings sections"
    >
      {loading && hierarchy.length === 0 ? (
        <>
          <div className="mb-2 h-4 w-24 animate-pulse rounded bg-neutral-100" />
          <div className="mb-2 h-4 w-32 animate-pulse rounded bg-neutral-100" />
          <div className="mb-2 h-4 w-20 animate-pulse rounded bg-neutral-100" />
        </>
      ) : (
        hierarchy.map((tier) => (
          <div key={tier.name} className="mb-4">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">
              {tier.name}
            </div>
            <ul className="space-y-0.5">
              {tier.groups.map((group) => {
                const active = activeGroup === group.name
                return (
                  <li key={group.name}>
                    <button
                      type="button"
                      onClick={() => onSelect(group.name)}
                      aria-current={active ? 'true' : undefined}
                      className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm ${
                        active
                          ? 'bg-blue-600 font-semibold text-white'
                          : 'text-neutral-700 hover:bg-neutral-100'
                      }`}
                    >
                      <span className="truncate">{group.name}</span>
                      <span
                        className={`ml-2 text-xs ${active ? 'text-white/80' : 'text-neutral-400'}`}
                      >
                        {group.keys.length}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        ))
      )}
    </nav>
  )
}
