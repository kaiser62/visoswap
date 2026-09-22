import { useSettings } from '../state/SettingsContext'
import { useMedia } from '../state/MediaContext'

interface MobileHeaderProps {
  onSwitchToDesktop: () => void
  onOpenProjects: () => void
}

export function MobileHeader({ onSwitchToDesktop, onOpenProjects }: MobileHeaderProps) {
  const { projects, projectId } = useSettings()
  const { running, counts } = useMedia()

  const activeProject = projects.find((p) => p.id === projectId) ?? null

  return (
    <header
      className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-card/90 px-4 py-2.5 backdrop-blur-md"
      style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.5rem)' }}
    >
      {/* Project Selector / Title */}
      <button
        type="button"
        onClick={onOpenProjects}
        className="flex max-w-[210px] items-center gap-1.5 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-left active:bg-active"
      >
        <div className="min-w-0">
          <span className="block truncate text-xs font-semibold text-text">
            {activeProject?.name ?? 'Untitled Project'}
          </span>
          <span className="block text-[10px] text-muted">
            Tap to switch project ({projects.length})
          </span>
        </div>
        <svg
          className="h-3.5 w-3.5 shrink-0 text-muted"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

        {/* Status indicator + Desktop Switch */}
        <div className="flex items-center gap-2">
          {running ? (
            <div className="flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-[11px] font-medium text-accent animate-pulse">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              <span>Live {counts.completed > 0 ? `(${counts.completed})` : ''}</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 rounded-full border border-line bg-raised px-2.5 py-1 text-[11px] font-medium text-muted">
              <span className="h-1.5 w-1.5 rounded-full bg-muted/60" />
              <span>Idle</span>
            </div>
          )}

          <button
            type="button"
            onClick={onSwitchToDesktop}
            title="Switch to Desktop Studio UI"
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-raised text-muted active:bg-active active:text-text"
          >
            {/* Monitor / Desktop icon */}
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </button>
        </div>
      </header>
  )
}
