/** The global face library strip (plan 05.1-06 Task 2, D-03).
 *
 * The list request deliberately carries no project id — the store is
 * machine-global and every project shares it. Which face is ACTIVE is per
 * project (it comes from the open project's payload), which is why switching
 * projects moves the active mark without changing the list.
 */

import { useCallback, useEffect, useState } from 'react'
import { getFaceUsage, listFaces, uploadFace, type FaceUsageProject } from '../lib/api'
import { useMedia } from '../state/MediaContext'
import FaceDeleteDialog from './FaceDeleteDialog'

export function FaceLibrary() {
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
      // Content-addressed: uploading the same bytes again resolves to the id
      // already in the store, so a reload still shows one entry for it.
      await uploadFace(file)
      await reload()
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  /** The dialog fetches usage itself before it offers any confirm control;
   *  this prefetch hands the already-known answer to the opened dialog. */
  const openDelete = async (faceId: string) => {
    setDeleteFaceId(faceId)
    setPendingUsage([])
    try {
      setPendingUsage(await getFaceUsage(faceId))
    } catch {
      /* the dialog re-fetches and surfaces the error itself */
    }
  }

  return (
    <section className="space-y-2" data-testid="face-library">
      <h3 className="text-sm font-semibold text-text">Source faces</h3>
      <p className="text-xs text-muted">
        One library shared by every project — click a face to make it this
        project's source.
      </p>

      <label className="block text-sm">
        <span className="mb-1 block text-xs text-muted">Add a face image</span>
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
          className="w-full max-w-xs cursor-pointer rounded border border-line bg-raised px-3 py-1.5 text-xs text-text"
        />
      </label>
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

      <div className="flex flex-wrap gap-3" data-testid="face-strip">
        {faces.map((face) => {
          const active = face.face_id === sourceFaceId
          return (
            <div key={face.face_id} className="flex w-[88px] flex-col items-center gap-1">
              <button
                type="button"
                data-testid={`face-${face.face_id}`}
                data-active={active || undefined}
                aria-pressed={active}
                aria-label={`Activate face ${face.display_name ?? face.face_id}`}
                onClick={() => void selectFace(face.face_id)}
                className={`overflow-hidden rounded border-2 ${active ? 'border-accent' : 'border-line'}`}
              >
                {face.thumbnail_url ? (
                  <img src={face.thumbnail_url} alt="" className="h-[72px] w-[84px] object-cover" />
                ) : (
                  <span className="flex h-[72px] w-[84px] items-center justify-center bg-raised text-[10px] text-muted">
                    no thumb
                  </span>
                )}
              </button>
              {active && (
                <span className="rounded-full bg-accent px-1.5 py-0.5 text-[9px] font-semibold text-bg">
                  active
                </span>
              )}
              <button
                type="button"
                aria-label={`Delete face ${face.display_name ?? face.face_id}`}
                data-testid={`face-delete-${face.face_id}`}
                onClick={() => void openDelete(face.face_id)}
                className="text-[10px] font-semibold text-bad hover:underline"
              >
                Delete
              </button>
            </div>
          )
        })}
        {faces.length === 0 && !loadError && (
          <p className="text-xs text-muted">No faces in the library yet.</p>
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
    </section>
  )
}
