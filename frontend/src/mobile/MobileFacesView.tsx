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
import FaceDeleteDialog from '../components/FaceDeleteDialog'
import {
  AssignFaceGroupModal,
  RenameGroupModal,
} from '../components/FaceGroupModals'

interface MobileFacesViewProps {
  onFaceSelected?: () => void
}

export function MobileFacesView({ onFaceSelected }: MobileFacesViewProps) {
  const { sourceFaceId, selectFace } = useMedia()
  const [faces, setFaces] = useState<Face[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [loadError, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [autoGrouping, setAutoGrouping] = useState(false)
  const [infoToast, setInfoToast] = useState<string | null>(null)

  // Dialog states
  const [deleteFaceId, setDeleteFaceId] = useState<string | null>(null)
  const [pendingUsage, setPendingUsage] = useState<FaceUsageProject[]>([])
  const [renamingGroup, setRenamingGroup] = useState<string | null>(null)
  const [assigningFace, setAssigningFace] = useState<Face | null>(null)

  // Collapsed groups tracking (default all expanded)
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
    setInfoToast(msg)
    setTimeout(() => setInfoToast(null), 3500)
  }

  const handleUpload = async (file: File) => {
    setUploading(true)
    setUploadError(null)
    try {
      await uploadFace(file)
      await reload()
      showToast('Face uploaded')
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
      showToast(`Auto-grouped into ${res.total_groups} person groups!`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Auto-group failed')
    } finally {
      setAutoGrouping(false)
    }
  }

  const openDelete = async (faceId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setDeleteFaceId(faceId)
    setPendingUsage([])
    try {
      setPendingUsage(await getFaceUsage(faceId))
    } catch {
      /* the dialog re-fetches usage */
    }
  }

  const openAssign = (face: Face, e: React.MouseEvent) => {
    e.stopPropagation()
    setAssigningFace(face)
  }

  const handleSelect = async (faceId: string) => {
    try {
      await selectFace(faceId)
      onFaceSelected?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not activate face')
    }
  }

  const toggleGroupCollapse = (groupName: string) => {
    setCollapsedGroups((prev) => ({
      ...prev,
      [groupName]: !prev[groupName],
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

    // Sort group names: alphabetical, with "Ungrouped" strictly at the end
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
    <div className="space-y-3 p-3 pb-24">
      {/* Toast Notification */}
      {infoToast && (
        <div className="fixed top-14 left-1/2 z-50 -translate-x-1/2 rounded-full bg-accent px-4 py-1.5 text-xs font-bold text-bg shadow-lg animate-in fade-in slide-in-from-top-2">
          {infoToast}
        </div>
      )}

      {/* Top Search Bar */}
      <div className="relative">
        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-muted">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>
        <input
          type="search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search faces or groups…"
          className="w-full rounded-xl border border-line bg-card py-2 pr-9 pl-9 text-xs text-text placeholder-muted shadow-inner focus:border-accent focus:outline-none"
        />
        {searchQuery && (
          <button
            type="button"
            onClick={() => setSearchQuery('')}
            className="absolute inset-y-0 right-0 flex items-center pr-3 text-muted hover:text-text"
          >
            <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
            </svg>
          </button>
        )}
      </div>

      {/* Action Toolbar */}
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-text truncate">Face Library</h2>
          <p className="text-[11px] text-muted truncate">
            {filteredFaces.length} face{filteredFaces.length === 1 ? '' : 's'}
            {existingGroupNames.length > 0 && ` • ${existingGroupNames.length} groups`}
            {searchQuery && ' (filtered)'}
          </p>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {/* Auto-Group Button */}
          <button
            type="button"
            onClick={() => void handleAutoGroup()}
            disabled={autoGrouping}
            title="Auto-group faces of the same person"
            className="flex items-center gap-1 rounded-xl border border-line bg-card px-2.5 py-1.5 text-xs font-semibold text-text shadow-sm active:bg-raised disabled:opacity-50"
          >
            <svg className="h-3.5 w-3.5 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            <span>{autoGrouping ? 'Grouping…' : 'Auto-Group'}</span>
          </button>

          {/* Add Face Button */}
          <label className="flex cursor-pointer items-center gap-1.5 rounded-xl bg-accent px-3 py-1.5 text-xs font-bold text-bg shadow-sm active:bg-accent/80">
            <svg className="h-3.5 w-3.5 stroke-[2.5]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            <span>{uploading ? 'Adding…' : '+ Add Face'}</span>
            <input
              type="file"
              accept="image/*"
              disabled={uploading}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleUpload(file)
                e.target.value = ''
              }}
              className="hidden"
            />
          </label>
        </div>
      </div>

      {uploadError && (
        <p className="rounded-xl bg-bad/10 p-2 text-xs text-bad">{uploadError}</p>
      )}
      {loadError && (
        <p className="rounded-xl bg-bad/10 p-2 text-xs text-bad">{loadError}</p>
      )}

      {/* Grouped Face Accordions */}
      <div className="space-y-3">
        {groupedFaces.map(({ name, isUngrouped, faces: groupList }) => {
          const isCollapsed = Boolean(collapsedGroups[name]) && !searchQuery
          return (
            <div
              key={name}
              className="overflow-hidden rounded-xl border border-line/70 bg-card/60 shadow-sm"
            >
              {/* Group Accordion Header */}
              <div
                onClick={() => toggleGroupCollapse(name)}
                className="flex cursor-pointer select-none items-center justify-between border-b border-line/40 bg-raised/40 px-3 py-2 transition-colors hover:bg-raised/70"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <svg
                    className={`h-3.5 w-3.5 text-muted transition-transform duration-200 ${
                      isCollapsed ? '-rotate-90' : 'rotate-0'
                    }`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                  <span className={`text-xs font-bold truncate ${isUngrouped ? 'text-muted' : 'text-text'}`}>
                    {name}
                  </span>
                  <span className="rounded-full bg-raised px-2 py-0.5 text-[10px] font-semibold text-muted">
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
                    title="Rename this group"
                    className="flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-semibold text-accent hover:bg-card active:bg-raised"
                  >
                    <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                    </svg>
                    <span>Rename</span>
                  </button>
                )}
              </div>

              {/* Group Body: Compact Face Grid */}
              {!isCollapsed && (
                <div className="grid grid-cols-3 gap-2 p-2 sm:grid-cols-4 md:grid-cols-5">
                  {groupList.map((f) => {
                    const isActive = f.face_id === sourceFaceId
                    return (
                      <div
                        key={f.face_id}
                        onClick={() => handleSelect(f.face_id)}
                        className={`group relative flex flex-col overflow-hidden rounded-xl border bg-card transition-all active:scale-[0.97] ${
                          isActive
                            ? 'border-accent shadow-sm shadow-accent/30 ring-2 ring-accent'
                            : 'border-line/70 hover:border-muted active:border-text'
                        }`}
                      >
                        {/* Compact aspect-square thumbnail */}
                        <div className="relative aspect-square w-full bg-raised">
                          {f.thumbnail_url ? (
                            <img
                              src={f.thumbnail_url}
                              alt={f.display_name ?? 'Face'}
                              className="h-full w-full object-cover"
                              loading="lazy"
                            />
                          ) : (
                            <div className="flex h-full w-full items-center justify-center text-muted">
                              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                              </svg>
                            </div>
                          )}

                          {/* Active check indicator */}
                          {isActive && (
                            <div className="absolute top-1.5 left-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-accent text-bg shadow-sm">
                              <svg className="h-2.5 w-2.5 fill-current" viewBox="0 0 20 20">
                                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                              </svg>
                            </div>
                          )}

                          {/* Delete button */}
                          <button
                            type="button"
                            onClick={(e) => openDelete(f.face_id, e)}
                            title="Delete face"
                            className="absolute top-1.5 right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-muted backdrop-blur-sm active:bg-bad active:text-white"
                          >
                            <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>

                          {/* Change/Assign Group Button */}
                          <button
                            type="button"
                            onClick={(e) => openAssign(f, e)}
                            title="Set person/group"
                            className="absolute bottom-1.5 right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-muted backdrop-blur-sm active:bg-accent active:text-bg"
                          >
                            <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
                            </svg>
                          </button>
                        </div>

                        {/* Title footer */}
                        <div className="p-1 text-center">
                          <span className={`block truncate text-[11px] ${isActive ? 'font-bold text-accent' : 'font-medium text-text'}`}>
                            {f.display_name ?? 'Face'}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {filteredFaces.length === 0 && !uploading && (
        <div className="py-12 text-center text-sm text-muted">
          {searchQuery ? `No faces matching "${searchQuery}"` : 'No faces in library yet.'}
        </div>
      )}

      {/* Delete dialog modal */}
      {deleteFaceId && (
        <FaceDeleteDialog
          faceId={deleteFaceId}
          initialUsage={pendingUsage}
          onClose={() => setDeleteFaceId(null)}
          onDeleted={async () => {
            setDeleteFaceId(null)
            await reload()
          }}
        />
      )}

      {/* Rename Group Modal */}
      {renamingGroup && (
        <RenameGroupModal
          open={Boolean(renamingGroup)}
          oldName={renamingGroup}
          onClose={() => setRenamingGroup(null)}
          onRenamed={async () => {
            await reload()
            showToast('Group renamed')
          }}
        />
      )}

      {/* Assign Face to Group Modal */}
      {assigningFace && (
        <AssignFaceGroupModal
          open={Boolean(assigningFace)}
          face={assigningFace}
          existingGroups={existingGroupNames}
          onClose={() => setAssigningFace(null)}
          onAssigned={async () => {
            await reload()
            showToast('Face group updated')
          }}
        />
      )}
    </div>
  )
}
