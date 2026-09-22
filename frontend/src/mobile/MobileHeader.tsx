import { useState } from 'react'
import { useSettings } from '../state/SettingsContext'
import { useMedia } from '../state/MediaContext'

interface MobileHeaderProps {
  onSwitchToDesktop: () => void
}

export function MobileHeader({ onSwitchToDesktop }: MobileHeaderProps) {
  const {
    projects,
    projectId,
    setProject,
    newProject,
    removeProject,
  } = useSettings()
  const { running, counts } = useMedia()

  const [showProjectsSheet, setShowProjectsSheet] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const activeProject = projects.find((p) => p.id === projectId) ?? null

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newProjectName.trim()) return
    setCreating(true)
    try {
      await newProject(newProjectName.trim())
      setNewProjectName('')
      setShowProjectsSheet(false)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    try {
      await removeProject(id)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <>
      <header
        className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-card/90 px-4 py-2.5 backdrop-blur-md"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.5rem)' }}
      >
        {/* Project Selector / Title */}
        <button
          type="button"
          onClick={() => setShowProjectsSheet(true)}
          className="flex max-w-[210px] items-center gap-1.5 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-left active:bg-active"
        >
          <div className="min-w-0">
            <span className="block truncate text-xs font-semibold text-text">
              {activeProject?.name ?? 'Untitled Project'}
            </span>
            <span className="block text-[10px] text-muted">
              Tap to switch project
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

      {/* Projects Bottom Sheet / Drawer */}
      {showProjectsSheet && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end bg-black/60 backdrop-blur-sm animate-fade-in">
          <div
            className="fixed inset-0"
            onClick={() => setShowProjectsSheet(false)}
          />
          <div
            className="relative z-10 max-h-[85vh] overflow-y-auto rounded-t-2xl border-t border-line bg-card p-4 pb-8 shadow-2xl"
            style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 2rem)' }}
          >
            {/* Handle bar */}
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-line" />

            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-text">Select Project</h2>
              <button
                type="button"
                onClick={() => setShowProjectsSheet(false)}
                className="rounded-full bg-raised p-1 text-muted active:text-text"
              >
                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </button>
            </div>

            {/* New project form */}
            <form onSubmit={handleCreate} className="mb-4 flex gap-2">
              <input
                type="text"
                placeholder="New project name..."
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                className="flex-1 rounded-xl border border-line bg-raised px-3 py-2 text-sm text-text placeholder-muted focus:border-accent focus:outline-none"
              />
              <button
                type="submit"
                disabled={creating || !newProjectName.trim()}
                className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50 active:bg-accent/80"
              >
                {creating ? 'Creating…' : 'Create'}
              </button>
            </form>

            {/* Project List */}
            <div className="space-y-1.5 divide-y divide-line/40">
              {projects.map((p) => {
                const isSelected = p.id === projectId
                return (
                  <div
                    key={p.id}
                    className={`flex items-center justify-between rounded-xl px-3 py-2.5 transition-colors ${
                      isSelected ? 'bg-active border border-accent/30' : 'hover:bg-raised active:bg-raised'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setProject(p.id)
                        setShowProjectsSheet(false)
                      }}
                      className="flex flex-1 items-center gap-2.5 text-left"
                    >
                      <div className={`h-2.5 w-2.5 rounded-full ${isSelected ? 'bg-accent' : 'bg-line'}`} />
                      <div className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-text">
                          {p.name}
                        </span>
                        <span className="block text-xs text-muted">
                          {p.has_video ? (p.video_filename ?? 'Video attached') : 'No video'}
                        </span>
                      </div>
                    </button>

                    {projects.length > 1 && (
                      <button
                        type="button"
                        disabled={deletingId === p.id}
                        onClick={() => handleDelete(p.id)}
                        className="ml-2 rounded-lg p-1.5 text-muted hover:text-bad active:text-bad disabled:opacity-50"
                        title="Delete Project"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
