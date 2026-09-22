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
    <div className="space-y-4 p-4 pb-24">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-text">Source Face Library</h2>
          <p className="text-xs text-muted">Tap a face to make it this project's source</p>
        </div>
        <span className="rounded-full bg-raised px-2.5 py-1 text-xs font-medium text-muted">
          {faces.length} {faces.length === 1 ? 'face' : 'faces'}
        </span>
      </div>

      {/* Upload button container */}
      <div className="rounded-2xl border border-dashed border-line bg-card p-4 text-center">
        <label className="flex cursor-pointer flex-col items-center justify-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/15 text-accent">
            <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
          </div>
          <span className="mt-2 text-sm font-semibold text-text">
            {uploading ? 'Uploading face…' : 'Take Photo or Choose Face'}
          </span>
          <span className="text-[11px] text-muted">Supports camera, photo library or files</span>
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
        <p className="rounded-xl bg-bad/10 p-3 text-xs text-bad">{uploadError}</p>
      )}
      {loadError && (
        <p className="rounded-xl bg-bad/10 p-3 text-xs text-bad">{loadError}</p>
      )}

      {/* Face Grid (2 columns on mobile) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {faces.map((f) => {
          const isActive = f.face_id === sourceFaceId
          return (
            <div
              key={f.face_id}
              onClick={() => handleSelect(f.face_id)}
              className={`group relative flex flex-col overflow-hidden rounded-2xl border bg-card transition-all active:scale-[0.98] ${
                isActive
                  ? 'border-accent shadow-md shadow-accent/20 ring-1 ring-accent'
                  : 'border-line active:border-muted'
              }`}
            >
              {/* Aspect square thumbnail */}
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
                    <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                  </div>
                )}

                {/* Active checkmark pill */}
                {isActive && (
                  <div className="absolute top-2 left-2 flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-[10px] font-bold text-bg shadow-sm">
                    <svg className="h-3 w-3 fill-current" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                    <span>Active</span>
                  </div>
                )}

                {/* Delete button */}
                <button
                  type="button"
                  onClick={(e) => openDelete(f.face_id, e)}
                  title="Delete face"
                  className="absolute top-2 right-2 flex h-7 w-7 items-center justify-center rounded-full bg-black/60 text-muted backdrop-blur-sm active:bg-bad active:text-white"
                >
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>

              {/* Face title footer */}
              <div className="p-2.5">
                <span className="block truncate text-xs font-semibold text-text">
                  {f.display_name ?? 'Face Image'}
                </span>
                <span className="block text-[10px] text-muted">
                  {isActive ? 'Current Project Source' : 'Tap to assign'}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {faces.length === 0 && !uploading && (
        <div className="py-8 text-center text-sm text-muted">
          No faces uploaded yet. Tap the button above to upload a face.
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
