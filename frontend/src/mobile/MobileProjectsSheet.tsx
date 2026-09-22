import { useMemo, useState } from 'react'
import { useSettings } from '../state/SettingsContext'
import type { Project } from '../types'

interface MobileProjectsSheetProps {
  open: boolean
  onClose: () => void
  onProjectSelected: () => void
}

function formatDuration(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return null
  }
  const total = Math.round(seconds)
  const mins = Math.floor(total / 60)
  return `${mins}:${String(total % 60).padStart(2, '0')}`
}

function formatUpdated(epochSeconds: number | null | undefined): string | null {
  if (!epochSeconds) return null
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function MobileProjectsSheet({
  open,
  onClose,
  onProjectSelected,
}: MobileProjectsSheetProps) {
  const { projects, projectId, setProject, newProject, removeProject } = useSettings()

  const [searchQuery, setSearchQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const filteredProjects = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return projects
    return projects.filter((p) => {
      const nameMatch = p.name.toLowerCase().includes(q)
      const fileMatch = p.video_filename?.toLowerCase().includes(q)
      const idMatch = p.id.toLowerCase().includes(q)
      return nameMatch || fileMatch || idMatch
    })
  }, [projects, searchQuery])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    try {
      const name = newProjectName.trim() || undefined
      await newProject(name)
      setNewProjectName('')
      onProjectSelected()
      onClose()
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!window.confirm('Delete this project and all its generated frames?')) return
    setDeletingId(id)
    try {
      await removeProject(id)
    } finally {
      setDeletingId(null)
    }
  }

  const handleSelect = (id: string) => {
    setProject(id)
    onProjectSelected()
    onClose()
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end bg-black/70 backdrop-blur-sm animate-fade-in">
      {/* Backdrop */}
      <div className="fixed inset-0" onClick={onClose} />

      {/* Drawer */}
      <div
        className="relative z-10 flex max-h-[88vh] flex-col rounded-t-3xl border-t border-line bg-card p-4 shadow-2xl"
        style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 1.5rem)' }}
      >
        {/* Handle Bar */}
        <div className="mx-auto mb-3 h-1.5 w-12 rounded-full bg-line" />

        {/* Title & Close */}
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="text-base font-bold text-text">Select Project</h2>
            <p className="text-xs text-muted">
              {projects.length} project{projects.length === 1 ? '' : 's'} available
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full bg-raised p-1.5 text-muted active:text-text"
          >
            <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        {/* Search Bar */}
        <div className="relative mb-3">
          <input
            type="text"
            placeholder="Search projects by name or video..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full rounded-xl border border-line bg-raised py-2 pr-8 pl-9 text-sm text-text placeholder-muted focus:border-accent focus:outline-none"
          />
          <svg
            className="absolute top-2.5 left-3 h-4 w-4 text-muted"
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
              className="absolute top-2.5 right-3 text-muted hover:text-text"
            >
              ✕
            </button>
          )}
        </div>

        {/* New Project Form */}
        <form onSubmit={handleCreate} className="mb-3 flex gap-2">
          <input
            type="text"
            placeholder="New project name (optional)..."
            value={newProjectName}
            onChange={(e) => setNewProjectName(e.target.value)}
            className="flex-1 rounded-xl border border-line bg-raised px-3 py-2 text-sm text-text placeholder-muted focus:border-accent focus:outline-none"
          />
          <button
            type="submit"
            disabled={creating}
            className="rounded-xl bg-accent px-4 py-2 text-xs font-bold text-bg active:bg-accent/80 disabled:opacity-50"
          >
            {creating ? 'Creating…' : '+ New'}
          </button>
        </form>

        {/* Project List */}
        <div className="flex-1 space-y-2 overflow-y-auto pr-0.5">
          {filteredProjects.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted">
              {searchQuery ? 'No matching projects found.' : 'No projects yet.'}
            </div>
          ) : (
            filteredProjects.map((p: Project) => {
              const isSelected = p.id === projectId
              const duration = formatDuration(p.duration)
              const updated = formatUpdated(p.updated_at)
              const size = p.width && p.height ? `${p.width}×${p.height}` : null
              const metaParts = [p.has_video ? '🎬 Video' : 'No video', size, duration].filter(
                Boolean,
              )

              return (
                <div
                  key={p.id}
                  onClick={() => handleSelect(p.id)}
                  className={`flex cursor-pointer items-center justify-between rounded-2xl border p-3 transition-all active:scale-[0.99] ${
                    isSelected
                      ? 'border-accent bg-accent/10 shadow-sm'
                      : 'border-line/70 bg-raised/60 hover:bg-raised'
                  }`}
                >
                  <div className="flex flex-1 items-start gap-3">
                    <div
                      className={`mt-1 h-3 w-3 shrink-0 rounded-full ${
                        isSelected ? 'bg-accent ring-4 ring-accent/20' : 'bg-line'
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold text-text">
                          {p.name}
                        </span>
                        {isSelected && (
                          <span className="rounded bg-accent px-1.5 py-0.5 text-[9px] font-bold uppercase text-bg">
                            Active
                          </span>
                        )}
                      </div>

                      <p className="mt-0.5 text-xs text-muted">
                        {metaParts.join(' · ')}
                      </p>

                      {p.video_filename && (
                        <p className="mt-0.5 truncate text-[11px] text-muted/80">
                          {p.video_filename}
                        </p>
                      )}

                      {updated && (
                        <p className="mt-0.5 text-[10px] text-muted/60">
                          Updated {updated}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="ml-3 flex shrink-0 items-center gap-2">
                    {isSelected ? (
                      <span className="text-xs font-semibold text-accent">Opened</span>
                    ) : (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleSelect(p.id)
                        }}
                        className="rounded-lg bg-accent/20 px-2.5 py-1 text-xs font-semibold text-accent active:bg-accent/40"
                      >
                        Open
                      </button>
                    )}

                    {projects.length > 1 && (
                      <button
                        type="button"
                        disabled={deletingId === p.id}
                        onClick={(e) => handleDelete(p.id, e)}
                        className="rounded-lg p-1.5 text-muted hover:text-bad active:text-bad disabled:opacity-40"
                        title="Delete project"
                      >
                        <svg
                          className="h-4 w-4"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                          />
                        </svg>
                      </button>
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
