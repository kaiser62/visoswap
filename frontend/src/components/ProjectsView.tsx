/** Projects overview: every project on the machine, at a glance.
 *
 * The header picker is a dropdown of names and nothing else, which is fine for
 * switching and useless for deciding — two projects called "Untitled project"
 * are indistinguishable there. This view shows what actually separates them:
 * the bound source, its size and length, and when it was last touched. It is a
 * sibling tab rather than a route, so opening it never unmounts the studio and
 * its controls (D-02).
 */

import { useEffect, useState } from 'react'
import { useSettings } from '../state/SettingsContext'
import type { Project } from '../types'
import { ProjectDeleteDialog } from './ProjectDeleteDialog'
import { Button, Card } from './ui'

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
  return new Date(epochSeconds * 1000).toLocaleString()
}

function ProjectRow({
  project,
  open,
  onOpen,
  onDelete,
}: {
  project: Project
  open: boolean
  onOpen: () => void
  onDelete: () => void
}) {
  const duration = formatDuration(project.duration)
  const updated = formatUpdated(project.updated_at)
  const size =
    project.width && project.height ? `${project.width}×${project.height}` : null
  // Everything the row knows about the source, skipping what is not bound yet
  // rather than printing "null" or a stack of em-dashes.
  const facts = [project.video_filename, size, duration].filter(
    (f): f is string => Boolean(f),
  )

  return (
    <Card
      className={`flex items-center gap-4 ${open ? 'border-accent' : ''}`}
      data-testid="project-row"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-text">
          {project.name}
          {open && (
            <span className="ml-2 rounded bg-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase text-bg">
              Open
            </span>
          )}
        </p>
        <p className="truncate text-xs text-muted">
          {project.has_video
            ? facts.join(' · ')
            : 'No video bound — load one in the Media card.'}
        </p>
        {updated && (
          <p className="text-xs text-muted">Last updated {updated}</p>
        )}
      </div>
      <Button variant="ghost" onClick={onOpen} disabled={open}>
        {open ? 'Open' : 'Open'}
      </Button>
      <Button variant="destructive" onClick={onDelete}>
        Delete
      </Button>
    </Card>
  )
}

export function ProjectsView({
  active,
  onOpenStudio,
}: {
  active: boolean
  onOpenStudio?: () => void
}) {
  const { projects, projectId, setProject, newProject, removeProject, refreshProjects } =
    useSettings()
  const [pending, setPending] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Refetch on every entry: the list goes stale as soon as a video is bound or
  // a project is created from anywhere else in the app.
  useEffect(() => {
    if (active) void refreshProjects()
  }, [active, refreshProjects])

  const confirmDelete = async () => {
    if (!pending) return
    setDeleting(true)
    setError(null)
    try {
      await removeProject(pending.id)
      setPending(null)
    } catch {
      setError("Couldn't delete that project. Check the backend and try again.")
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-3 p-6" data-testid="projects-view">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-text">Projects</h2>
        <span className="text-xs text-muted" data-testid="projects-count">
          {projects.length} total
        </span>
        <Button
          className="ml-auto"
          onClick={() => void newProject('Untitled project')}
          data-testid="btn-new-project-home"
        >
          New project
        </Button>
      </div>

      {projects.length === 0 ? (
        <Card className="text-center text-sm text-muted">
          No projects yet. Create one to bind a video and start generating.
        </Card>
      ) : (
        projects.map((p) => (
          <ProjectRow
            key={p.id}
            project={p}
            open={p.id === projectId}
            onOpen={() => {
              setProject(p.id)
              onOpenStudio?.()
            }}
            onDelete={() => {
              setError(null)
              setPending(p)
            }}
          />
        ))
      )}

      <ProjectDeleteDialog
        open={pending !== null}
        name={pending?.name ?? ''}
        busy={deleting}
        error={error}
        onCancel={() => setPending(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  )
}
