/** The global face library strip (plan 05.1-06 Task 2, D-03).
 *
 * The list request deliberately carries no project id — the store is
 * machine-global and every project shares it. Which face is ACTIVE is per
 * project (it comes from the open project's payload), which is why switching
 * projects moves the active mark without changing the list.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  autoGroupFaces,
  getFaceUsage,
  listFaces,
  uploadFace,
  type FaceUsageProject,
} from '../lib/api'
import type { Face } from '../types'
import { useMedia } from '../state/MediaContext'
import FaceDeleteDialog from './FaceDeleteDialog'
import { AssignFaceGroupModal, RenameGroupModal } from './FaceGroupModals'

export function FaceLibrary() {
  const { sourceFaceId, selectFace } = useMedia()
  const [faces, setFaces] = useState<Face[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [loadError, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [autoGrouping, setAutoGrouping] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const [deleteFaceId, setDeleteFaceId] = useState<string | null>(null)
  const [pendingUsage, setPendingUsage] = useState<FaceUsageProject[]>([])
  const [renamingGroup, setRenamingGroup] = useState<string | null>(null)
  const [assigningFace, setAssigningFace] = useState<Face | null>(null)

  // Collapsed groups tracking
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({})

  const reload = useCallback(async () => {
    try {
      setFaces(await listFaces())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load faces')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  const handleUpload = async (file: File) => {
    setUploading(true)
    setUploadError(null)
    try {
      await uploadFace(file)
      await reload()
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const handleAutoGroup = async () => {
    setAutoGrouping(true)
    try {
      const res = await autoGroupFaces(false)
      await reload()
      showToast(`Grouped into ${res.total_groups} person groups`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Auto-group failed')
    } finally {
      setAutoGrouping(false)
    }
  }

  const openDelete = async (faceId: string) => {
    setDeleteFaceId(faceId)
    setPendingUsage([])
    try {
      setPendingUsage(await getFaceUsage(faceId))
    } catch {
      /* the dialog re-fetches and surfaces the error itself */
    }
  }

  const toggleGroup = (name: string) => {
    setCollapsedGroups((prev) => ({
      ...prev,
      [name]: !prev[name],
    }))
  }

  // Filter faces by search query
  const queryClean = searchQuery.trim().toLowerCase()
  const filteredFaces = useMemo(() => {
    if (!queryClean) return faces
    return faces.filter((f) => {
      const name = (f.display_name || '').toLowerCase()
      const group = (f.group || '').toLowerCase()
      const id = f.face_id.toLowerCase()
      return (
        name.includes(queryClean) ||
        group.includes(queryClean) ||
        id.includes(queryClean)
      )
    })
  }, [faces, queryClean])

  // Partition into groups
  const { groupedFaces, existingGroupNames } = useMemo(() => {
    const groupsMap = new Map<string, Face[]>()
    const allGroups = new Set<string>()

    for (const f of faces) {
      if (f.group) allGroups.add(f.group)
    }

    for (const f of filteredFaces) {
      const key = f.group ? f.group : 'Ungrouped'
      const list = groupsMap.get(key) ?? []
      list.push(f)
      groupsMap.set(key, list)
    }

    const sortedKeys = Array.from(groupsMap.keys()).sort((a, b) => {
      if (a === 'Ungrouped') return 1
      if (b === 'Ungrouped') return -1
      return a.localeCompare(b)
    })

    const sortedMap = sortedKeys.map((key) => ({
      name: key,
      isUngrouped: key === 'Ungrouped',
      faces: groupsMap.get(key)!,
    }))

    return {
      groupedFaces: sortedMap,
      existingGroupNames: Array.from(allGroups).sort(),
    }
  }, [faces, filteredFaces])

  return (
    <section className="space-y-2" data-testid="face-library">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-text">Source faces</h3>
          <p className="text-xs text-muted">
            One library shared by every project — click a face to make it this
            project's source.
          </p>
        </div>
        {toast && (
          <span className="rounded-full bg-accent/20 px-2.5 py-0.5 text-xs font-semibold text-accent">
            {toast}
          </span>
        )}
      </div>

      {/* Search and Action Row */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative flex-1 max-w-xs">
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search faces or groups…"
            className="w-full rounded border border-line bg-raised px-2.5 py-1 text-xs text-text placeholder-muted focus:border-accent focus:outline-none"
            data-testid="face-search-input"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute inset-y-0 right-0 flex items-center pr-2 text-muted hover:text-text text-xs"
            >
              ×
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleAutoGroup()}
            disabled={autoGrouping}
            title="Auto-group faces of the same person"
            className="inline-flex h-7 items-center gap-1 rounded border border-line bg-card px-2.5 text-xs font-semibold text-text hover:border-accent hover:text-accent disabled:opacity-50"
          >
            <span className="text-accent">✦</span>
            <span>{autoGrouping ? 'Grouping…' : 'Auto-Group'}</span>
          </button>

          <label className="block text-sm">
            <span className="sr-only">Add a face image</span>
            <input
              data-testid="face-upload-input"
              aria-label="Choose a face image"
              type="file"
              accept="image/*"
              disabled={uploading}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleUpload(file)
                e.target.value = ''
              }}
              className="w-full max-w-[140px] cursor-pointer rounded border border-line bg-raised px-2 py-1 text-xs text-text file:mr-2 file:cursor-pointer file:rounded file:border-0 file:bg-accent file:px-2 file:py-0.5 file:text-xs file:font-semibold file:text-bg"
            />
          </label>
        </div>
      </div>

      {uploading && (
        <p role="status" className="text-xs text-muted">
          Uploading face…
        </p>
      )}
      {uploadError && (
        <p role="alert" className="text-xs text-bad" data-testid="face-upload-error">
          {uploadError}
        </p>
      )}
      {loadError && <p role="alert" className="text-xs text-bad">{loadError}</p>}

      {faces.length > 0 && (
        <p className="text-xs text-muted" data-testid="face-count">
          {faces.length} {faces.length === 1 ? 'face' : 'faces'} — scroll for more
        </p>
      )}

      {/* Main Face Strip Container */}
      <div
        className="max-h-[380px] space-y-3 overflow-y-auto rounded border border-line bg-raised/30 p-2"
        data-testid="face-strip"
      >
        {groupedFaces.map(({ name, isUngrouped, faces: groupList }) => {
          const isCollapsed = Boolean(collapsedGroups[name]) && !searchQuery
          return (
            <div
              key={name}
              className="rounded-lg border border-line/60 bg-card/70 overflow-hidden"
            >
              {/* Group Header */}
              <div
                onClick={() => toggleGroup(name)}
                className="flex cursor-pointer select-none items-center justify-between border-b border-line/40 bg-raised/50 px-2.5 py-1.5 transition-colors hover:bg-raised/80"
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="text-[10px] text-muted">
                    {isCollapsed ? '▶' : '▼'}
                  </span>
                  <span className={`text-xs font-bold truncate ${isUngrouped ? 'text-muted italic' : 'text-text'}`}>
                    {name}
                  </span>
                  <span className="rounded-full bg-raised px-1.5 py-0.2 text-[9px] font-semibold text-muted">
                    {groupList.length}
                  </span>
                </div>

                {!isUngrouped && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      setRenamingGroup(name)
                    }}
                    title="Rename group"
                    className="rounded px-1.5 py-0.5 text-[10px] font-semibold text-accent hover:bg-raised"
                  >
                    Rename
                  </button>
                )}
              </div>

              {/* Group Body: Face Grid */}
              {!isCollapsed && (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(84px,1fr))] gap-2.5 p-2">
                  {groupList.map((face) => {
                    const active = face.face_id === sourceFaceId
                    const label = face.display_name ?? face.face_id
                    return (
                      <div
                        key={face.face_id}
                        className="flex w-full flex-col items-center gap-1"
                      >
                        <div className="relative group">
                          <button
                            type="button"
                            data-testid={`face-${face.face_id}`}
                            data-active={active || undefined}
                            aria-pressed={active}
                            aria-label={`Activate face ${label}`}
                            title={label}
                            onClick={() => void selectFace(face.face_id)}
                            className={`overflow-hidden rounded border-2 transition-all ${
                              active ? 'border-accent ring-2 ring-accent/40' : 'border-line hover:border-muted'
                            }`}
                          >
                            {face.thumbnail_url ? (
                              <img
                                src={face.thumbnail_url}
                                alt=""
                                className="h-[72px] w-[80px] object-cover"
                              />
                            ) : (
                              <span className="flex h-[72px] w-[80px] items-center justify-center bg-raised text-[10px] text-muted">
                                no thumb
                              </span>
                            )}
                          </button>

                          {/* Group assign button on hover */}
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              setAssigningFace(face)
                            }}
                            title="Assign to group"
                            className="absolute bottom-1 right-1 flex h-4 w-4 items-center justify-center rounded bg-black/70 text-[9px] text-muted hover:text-accent"
                          >
                            🏷
                          </button>
                        </div>

                        <span
                          className="w-full truncate text-center text-[10px] text-muted"
                          title={label}
                          data-testid={`face-name-${face.face_id}`}
                        >
                          {label}
                        </span>

                        {active && (
                          <span className="rounded-full bg-accent px-1.5 py-0.2 text-[9px] font-semibold text-bg">
                            active
                          </span>
                        )}

                        <button
                          type="button"
                          aria-label={`Delete face ${label}`}
                          data-testid={`face-delete-${face.face_id}`}
                          onClick={() => void openDelete(face.face_id)}
                          className="text-[10px] font-semibold text-bad hover:underline"
                        >
                          Delete
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}

        {faces.length === 0 && !loadError && (
          <p className="text-xs text-muted">No faces in the library yet.</p>
        )}

        {filteredFaces.length === 0 && faces.length > 0 && (
          <p className="text-xs text-muted py-4 text-center">
            No faces matching "{searchQuery}"
          </p>
        )}
      </div>

      {deleteFaceId !== null && (
        <FaceDeleteDialog
          faceId={deleteFaceId}
          initialUsage={pendingUsage}
          onClose={() => setDeleteFaceId(null)}
          onDeleted={() => void reload()}
        />
      )}

      {/* Rename Group Modal */}
      {renamingGroup && (
        <RenameGroupModal
          open={Boolean(renamingGroup)}
          oldName={renamingGroup}
          onClose={() => setRenamingGroup(null)}
          onRenamed={() => void reload()}
        />
      )}

      {/* Assign Face Group Modal */}
      {assigningFace && (
        <AssignFaceGroupModal
          open={Boolean(assigningFace)}
          face={assigningFace}
          existingGroups={existingGroupNames}
          onClose={() => setAssigningFace(null)}
          onAssigned={() => void reload()}
        />
      )}
    </section>
  )
}
