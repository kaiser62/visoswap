/** Header: title, project picker, preset selector, Save Changes (N) / Discard. */

import { useState } from 'react'
import { useSettings } from '../state/SettingsContext'
import { ConfirmModal } from './ConfirmModal'
import { PresetSelector } from './PresetSelector'
import { ProjectDeleteDialog } from './ProjectDeleteDialog'
import { Button } from './ui'

export function Header({ onSwitchToMobile }: { onSwitchToMobile?: () => void } = {}) {
  const {
    projects,
    projectId,
    setProject,
    newProject,
    removeProject,
    values,
    dirty,
    saving,
    saveError,
    save,
    discard,
    applyPresetValues,
  } = useSettings()
  const [showDiscard, setShowDiscard] = useState(false)
  const [showDelete, setShowDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)

  const dirtyCount = Object.keys(dirty).length
  const openProject = projects.find((p) => p.id === projectId) ?? null

  const handleDelete = async () => {
    if (!projectId) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await removeProject(projectId)
      setShowDelete(false)
    } catch {
      setDeleteError("Couldn't delete that project. Check the backend and try again.")
    } finally {
      setDeleting(false)
    }
  }

  const handleSave = async () => {
    await save()
    setJustSaved(true)
    setTimeout(() => setJustSaved(false), 2000)
  }

  return (
    <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center justify-between gap-4 border-b border-line bg-card px-6">
      <h1 className="text-xl font-semibold text-text">
        Predictive Video Frame Transformer
      </h1>

      <div className="flex items-center gap-2">
        {projects.length > 0 && (
          <select
            value={projectId ?? ''}
            onChange={(e) => setProject(e.target.value)}
            aria-label="Project"
            className="h-8 rounded border border-line bg-raised px-2 text-sm text-text"
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}

        <Button
          variant="ghost"
          onClick={() => void newProject('Untitled project')}
          data-testid="btn-new-project"
        >
          New
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setDeleteError(null)
            setShowDelete(true)
          }}
          disabled={!projectId}
          data-testid="btn-delete-project"
        >
          Delete
        </Button>

        <PresetSelector
          projectId={projectId}
          values={values}
          dirty={dirty}
          onChangeValues={applyPresetValues}
        />

        {onSwitchToMobile && (
          <Button
            variant="ghost"
            onClick={onSwitchToMobile}
            title="Open Mobile Web App (/mobile)"
            className="flex items-center gap-1.5 text-accent"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
            <span>Mobile</span>
          </Button>
        )}

        {justSaved && (
          <span className="text-sm text-good" aria-live="polite">
            Saved
          </span>
        )}

        {dirtyCount > 0 && (
          <Button variant="destructive" onClick={() => setShowDiscard(true)}>
            Discard
          </Button>
        )}

        <Button
          disabled={dirtyCount === 0 || saving}
          onClick={() => void handleSave()}
        >
          {saving ? 'Saving…' : `Save Changes (${dirtyCount})`}
        </Button>
      </div>

      {saveError && (
        <div className="absolute top-16 left-0 right-0 border-b border-bad/40 bg-bad/10 px-6 py-2 text-sm text-bad">
          {saveError}
        </div>
      )}

      {/* The accent underline webui2 gives its chrome. */}
      <div className="absolute inset-x-0 bottom-0 h-0.5 bg-accent" aria-hidden />

      <ProjectDeleteDialog
        open={showDelete}
        name={openProject?.name ?? 'this project'}
        busy={deleting}
        error={deleteError}
        onCancel={() => setShowDelete(false)}
        onConfirm={() => void handleDelete()}
      />

      <ConfirmModal
        open={showDiscard}
        count={dirtyCount}
        onCancel={() => setShowDiscard(false)}
        onConfirm={() => {
          discard()
          setShowDiscard(false)
        }}
      />
    </header>
  )
}
