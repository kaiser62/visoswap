/** Header: title, project picker, preset selector, Save Changes (N) / Discard. */

import { useState } from 'react'
import { useSettings } from '../state/SettingsContext'
import { ConfirmModal } from './ConfirmModal'
import { PresetSelector } from './PresetSelector'
import { Button } from './ui'

export function Header() {
  const {
    projects,
    projectId,
    setProject,
    values,
    dirty,
    saving,
    saveError,
    save,
    discard,
    applyPresetValues,
  } = useSettings()
  const [showDiscard, setShowDiscard] = useState(false)
  const [justSaved, setJustSaved] = useState(false)

  const dirtyCount = Object.keys(dirty).length

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

        <PresetSelector
          projectId={projectId}
          values={values}
          dirty={dirty}
          onChangeValues={applyPresetValues}
        />

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
