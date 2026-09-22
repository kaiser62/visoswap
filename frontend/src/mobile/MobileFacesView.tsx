import { useCallback, useEffect, useState } from 'react'
import { getFaceUsage, listFaces, uploadFace, type FaceUsageProject } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import FaceDeleteDialog from '../components/FaceDeleteDialog'

interface MobileFacesViewProps {
  onFaceSelected?: () => void
}

export function MobileFacesView({ onFaceSelected }: MobileFacesViewProps) {
  const { sourceFaceId, selectFace } = useMedia()
  const [faces, setFaces] = useState<
    { face_id: string; thumbnail_url: string | null; display_name: string | null }[]
  >([])
  const [loadError, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [deleteFaceId, setDeleteFaceId] = useState<string | null>(null)
  const [pendingUsage, setPendingUsage] = useState<FaceUsageProject[]>([])

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

  const handleSelect = async (faceId: string) => {
    try {
      await selectFace(faceId)
      onFaceSelected?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not activate face')
    }
  }

  return (
    <div className="space-y-3 p-3 pb-24">
      {/* Header with count and inline upload button */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-bold text-text">Face Library</h2>
          <p className="text-[11px] text-muted">
            {faces.length} face{faces.length === 1 ? '' : 's'} • Tap to swap
          </p>
        </div>
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

      {uploadError && (
        <p className="rounded-xl bg-bad/10 p-2 text-xs text-bad">{uploadError}</p>
      )}
      {loadError && (
        <p className="rounded-xl bg-bad/10 p-2 text-xs text-bad">{loadError}</p>
      )}

      {/* Compact Face Grid (3 columns on mobile, 4-5 on tablet) */}
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5">
        {faces.map((f) => {
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

                {/* Compact Delete button */}
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
              </div>

              {/* Compact title footer */}
              <div className="p-1 text-center">
                <span className={`block truncate text-[11px] ${isActive ? 'font-bold text-accent' : 'font-medium text-text'}`}>
                  {f.display_name ?? 'Face'}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {faces.length === 0 && !uploading && (
        <div className="py-12 text-center text-sm text-muted">
          No faces in library yet. Tap "+ Add Face" above to upload.
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
    </div>
  )
}
