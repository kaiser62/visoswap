/** Studio shell: one page at `/` (D-01). Header, then a two-column Studio
 * grid whose right column holds the Controls card containing exactly what the
 * settings body rendered before (D-02: mini-sidebar + scrolling schema groups,
 * all controls always mounted). The left column holds the player card (plan
 * 05); Media stays a placeholder until plans 06–07.
 */

import { useEffect, useMemo, useState } from 'react'
import { Header } from './components/Header'
import { GalleryView } from './components/GalleryView'
import { GroupSection } from './components/GroupSection'
import { JobsCard } from './components/JobsCard'
import { MediaCard } from './components/MediaCard'
import { PlayerCard } from './components/PlayerCard'
import { ResultsCard } from './components/ResultsCard'
import { Sidebar, buildHierarchy } from './components/Sidebar'
import { StudioCard, StudioGrid } from './components/StudioLayout'
import { Button, Card, Skeleton } from './components/ui'
import { isControlEnabled } from './lib/gates'
import { MediaProvider } from './state/MediaContext'
import { SettingsProvider, useSettings } from './state/SettingsContext'

function StudioBody() {
  const { status, schema, values, setValue, load } = useSettings()
  const [activeGroup, setActiveGroup] = useState<string | null>(null)

  // Which keys are gated-off, recomputed as values change (flipping a deciding
  // parent re-enables/disables its children immediately).
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

  // Flat list of every group in schema order, plus a key map, so the sidebar
  // can scroll to any section and all controls are always rendered.
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

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (status === 'error') {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8">
        <Card className="max-w-md text-center">
          <p className="mb-2 text-base font-semibold text-text">
            Couldn't load settings
          </p>
          <p className="mb-4 text-sm text-muted">
            The backend didn't respond. Check that the server is running, then
            retry.
          </p>
          <Button onClick={() => void load()}>Retry</Button>
        </Card>
      </div>
    )
  }

  if (status === 'loading' || !schema) {
    return (
      <div className="flex min-h-0 flex-1 gap-6 p-6">
        <div className="w-56 shrink-0 space-y-2">
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
        </div>
        <div className="flex-1 space-y-4">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      </div>
    )
  }

  const widgetKeys = Object.keys(schema.widgets)
  if (widgetKeys.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8">
        <Card className="max-w-md text-center">
          <p className="mb-2 text-base font-semibold text-text">
            No settings available
          </p>
          <p className="text-sm text-muted">
            The schema returned no controls. Check that the backend is running
            and serving `/api/schema`, then reload.
          </p>
        </Card>
      </div>
    )
  }

  // Clicking a sidebar group scrolls its section into view; the sidebar stays
  // in sync with whichever section is nearest the viewport top.
  const scrollToGroup = (group: string) => {
    setActiveGroup(group)
    document
      .getElementById(`group-${group}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <StudioGrid
      left={
        <>
          <PlayerCard />
          <MediaCard />
          <ResultsCard />
        </>
      }
      right={
        <>
        <StudioCard title="Controls" defaultOpen>
          <div className="flex gap-4 p-4">
            <div className="w-56 shrink-0">
              <Sidebar
                hierarchy={hierarchy}
                activeGroup={activeGroup}
                onSelect={scrollToGroup}
                loading={false}
              />
            </div>
            {/* Bounded lane with its own vertical scroll, the way webui2 bounds
              its tab bodies; the scroll-spy reads scrollTop off this element. */}
            <div
              className="min-h-0 min-w-0 max-h-[calc(100vh-198px)] flex-1 space-y-4 overflow-y-auto max-[969px]:max-h-none"
              onScroll={(e) => {
                const target = e.currentTarget
                let current: string | null = null
                for (const group of allGroups) {
                  const el = document.getElementById(`group-${group}`)
                  if (el && el.offsetTop - target.scrollTop <= 80) current = group
                }
                if (current && current !== activeGroup) setActiveGroup(current)
              }}
            >
              {allGroups.map((group) => (
                <GroupSection
                  key={group}
                  id={`group-${group}`}
                  name={group}
                  keys={groupKeys[group] ?? []}
                  widgets={schema.widgets}
                  values={values}
                  onChange={setValue}
                  disabledKeys={disabledKeys}
                />
              ))}
            </div>
          </div>
        </StudioCard>
        {/* Beneath the controls: what the backend is doing right now, and how
          well the overlay is actually keeping up. */}
        <JobsCard />
        </>
      }
    />
  )
}

/** MediaProvider sits INSIDE SettingsProvider and takes the already-resolved
 * project id from it — there is exactly one place that decides which project
 * is open, never a second resolution path. */
function MediaBoundary() {
  const { projectId } = useSettings()
  const [view, setView] = useState<'studio' | 'gallery'>('studio')
  return (
    <MediaProvider projectId={projectId}>
      <div className="flex items-center gap-2 border-b border-line px-4 py-2">
        <Button
          variant={view === 'studio' ? 'primary' : 'ghost'}
          onClick={() => setView('studio')}
          data-testid="view-studio"
        >
          Studio
        </Button>
        <Button
          variant={view === 'gallery' ? 'primary' : 'ghost'}
          onClick={() => setView('gallery')}
          data-testid="view-gallery"
        >
          Takes
        </Button>
      </div>
      {/* Both views stay mounted and the inactive one is hidden with CSS, the
        same rule StudioCard applies to a collapsed body — at page scale. The
        studio holds every gated control, and unmounting it to look at takes
        would empty the control set the moment the user switched. */}
      <div
        hidden={view !== 'studio'}
        className={view === 'studio' ? 'contents' : ''}
        data-testid="studio-view"
      >
        <StudioBody />
      </div>
      <div
        hidden={view !== 'gallery'}
        className={view === 'gallery' ? 'min-h-0 flex-1 overflow-y-auto' : ''}
      >
        <GalleryView />
      </div>
    </MediaProvider>
  )
}

export default function App() {
  return (
    <SettingsProvider>
      <div className="flex h-screen flex-col bg-bg text-text">
        <Header />
        <MediaBoundary />
      </div>
    </SettingsProvider>
  )
}
