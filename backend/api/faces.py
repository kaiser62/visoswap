"""The machine-global face library HTTP surface (plan 05.1-02, D-03).

Upload, list and serve the store ``backend.services.facestore`` owns. The
upload boundary is copied from ``projects.upload_source``: size-capped chunked
stream into a `.part`, atomic move, unlink on every failure arm. Because a
face's identity is its content digest, the digest is only known once the bytes
are on disk -- stream first, then let the service derive the final name.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from backend.api.deps import get_db
from backend.api.schemas import (
    AffectedProject,
    FaceClusterOut,
    FaceGroupsApply,
    FaceGroupsOut,
    FaceGroupRename,
    FaceOut,
    FaceUpdate,
    FaceUsageConflict,
    SimilarFace,
    SimilarFaces,
)
from backend.config import get_settings
from backend.models.database import Database
from backend.services import facecluster, faceembed, facegroup, facestore
from backend.services.scheduler import registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/faces", tags=["faces"])

CHUNK = 1024 * 1024

# Content at a content-addressed id never changes.
_IMMUTABLE = {"Cache-Control": "private, max-age=31536000, immutable"}


@router.post("", status_code=201, response_model=FaceOut)
async def upload_face(file: UploadFile = File(...)) -> dict[str, Any]:
    """Stream an uploaded image into the global store under its digest."""
    settings = get_settings()
    try:
        facestore.image_suffix(file.filename)
    except ValueError as exc:
        await file.close()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tmp = facestore.faces_dir() / f".upload-{uuid.uuid4().hex}.part"
    try:
        written = 0
        with tmp.open("wb") as handle:
            while chunk := await file.read(CHUNK):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="file too large")
                handle.write(chunk)
        record = facestore.store(tmp.read_bytes(), file.filename)
    except OSError as exc:
        raise HTTPException(
            status_code=507, detail=f"could not write file: {exc}"
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
        await file.close()
    return record


@router.get("", response_model=list[FaceOut])
async def list_faces() -> list[dict[str, Any]]:
    """The whole library, newest-first."""
    return facestore.list_faces()


def _serve(resolve) -> FileResponse:
    """Resolve a path through the store's id rule, then serve it."""
    try:
        path = resolve()
    except facestore.UnsafeFaceId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such face asset")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers=_IMMUTABLE,
    )


@router.get("/{face_id}/image")
async def read_face_image(face_id: str):
    """The full stored image at its content address."""
    return _serve(lambda: facestore.face_path(face_id))


@router.get("/{face_id}/thumbnail")
async def read_thumbnail(face_id: str):
    """The 112x112 card thumbnail."""
    return _serve(lambda: facestore.thumb_path(face_id))


@router.get("/{face_id}/usage")
async def face_usage(
    face_id: str, db: Database = Depends(get_db)
) -> dict[str, Any]:
    """Which projects point at this face -- what the confirm dialog reads
    before it shows anything. Safe on an unused face and on an id that has no
    file at all."""
    try:
        facestore.face_path(face_id)
    except facestore.UnsafeFaceId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    used = facestore.usage(await db.list_projects()).get(face_id, [])
    return {"projects": used}


@router.delete("/{face_id}", status_code=204, response_class=Response)
async def delete_face(
    face_id: str,
    force: bool = Query(default=False),
    db: Database = Depends(get_db),
):
    """Refuse silently-destructive deletes; cascade only when forced (D-05).

    Without `force`, a face projects still point at answers 409 carrying the
    same machine-readable project list `face_usage` returns -- the dialog
    renders it, so it is structured data, not prose. With `force` the order is
    the whole point: dependent runs stop, each affected row releases the file,
    and only then does the file go, so a crash between the steps leaves a
    project pointing at nothing rather than at a file no project admits to.
    """
    try:
        facestore.face_path(face_id)
    except facestore.UnsafeFaceId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected = facestore.usage(await db.list_projects()).get(face_id, [])
    if affected and not force:
        conflict = FaceUsageConflict(
            message=f"face is used by {len(affected)} project(s)",
            projects=[AffectedProject(**entry) for entry in affected],
        )
        raise HTTPException(status_code=409, detail=conflict.model_dump())

    for entry in affected:
        await registry.stop(entry["id"])
    for entry in affected:
        await db.update_project(entry["id"], source_face_path=None)
    facestore.delete(face_id)
    return Response(status_code=204)


@router.patch("/{face_id}", response_model=FaceOut)
async def update_face(face_id: str, payload: FaceUpdate) -> dict[str, Any]:
    """Update face display name and/or group."""
    try:
        path = facestore.face_path(face_id)
    except facestore.UnsafeFaceId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such face asset")

    if payload.display_name is not None:
        facestore.write_name(face_id, payload.display_name, overwrite=True)
    if payload.group is not None:
        facestore.write_group(face_id, payload.group)

    return facestore.get_face(face_id)


@router.post("/group/rename")
async def rename_face_group(payload: FaceGroupRename) -> dict[str, Any]:
    """Rename a face group across all member faces."""
    count = facestore.rename_group(payload.old_name, payload.new_name)
    return {
        "renamed": count,
        "old_name": payload.old_name,
        "new_name": payload.new_name,
    }


@router.post("/auto-group")
async def auto_group_faces(
    response: Response,
    overwrite: bool = Query(default=False),
    mode: str = Query(default="name"),
    threshold: float | None = Query(default=None),
):
    """Group faces: by detected filename (`mode=name`, the default) or by identity.

    `mode=name` is the original behaviour and stays the default so existing
    clients do not change. `mode=similarity` returns a proposal and writes
    nothing -- applying it is a separate, explicit call. Past `SYNC_LIMIT`
    uncached faces the embedding moves to a job and this answers 202, because a
    CPU embedding run is far too slow to hold a request open for.
    """
    if mode not in ("name", "similarity"):
        raise HTTPException(status_code=400, detail="mode must be 'name' or 'similarity'")
    if mode == "name":
        groups = facestore.auto_group_all(overwrite=overwrite)
        return {"groups": groups, "total_groups": len(groups)}
    return await _similarity_proposal(threshold, response)


async def _similarity_proposal(threshold: float | None, response: Response) -> dict[str, Any]:
    """Compute (or start computing) the similarity proposal."""
    faces = facestore.list_faces()
    if not faces:
        return FaceGroupsOut().model_dump()
    ids = [f["face_id"] for f in faces]
    pending = facegroup.uncached(ids)
    if len(pending) > facegroup.SYNC_LIMIT:
        try:
            job = facegroup.start(pending)
        except facegroup.EmbeddingBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # 202: the clusters are not in this body. The client polls
        # /api/faces/group/{job_id} (and may listen on the WS topic).
        response.status_code = 202
        return {**FaceGroupsOut(computed=0).model_dump(), "job": job.id}
    return _proposal_for(faces, threshold)


def _proposal_for(faces: list[dict[str, Any]], threshold: float | None) -> dict[str, Any]:
    """Embed whatever is missing, then cluster. Writes embeddings, no groups."""
    vectors, usable = [], []
    skipped: list[str] = []
    for face in faces:
        face_id = face["face_id"]
        vec = faceembed.embed_face_id(face_id)
        if vec is None:
            skipped.append(face_id)
            continue
        vectors.append(vec)
        usable.append(
            {
                "face_id": face_id,
                "group": face.get("group"),
                "detected": facestore.detect_group_name(face.get("display_name")),
            }
        )
    if len(usable) < facecluster.MIN_CLUSTER_SIZE:
        return {"clusters": [], "skipped_no_face": skipped, "computed": len(usable), "job": None}
    import numpy as np

    clusters = facecluster.build_clusters(usable, np.stack(vectors), facecluster.clamp_threshold(threshold))
    return {
        "clusters": [c.to_dict() for c in clusters],
        "skipped_no_face": skipped,
        "computed": len(usable),
        "job": None,
    }


@router.get("/group/{job_id}")
async def face_group_job(job_id: str) -> dict[str, Any]:
    """Progress of an embedding job, and its clusters once it finishes."""
    job = facegroup.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    body: dict[str, Any] = job.snapshot()
    if job.state == "done" and job.active is False and job.result is not None:
        # Rebuild the proposal from the now-cached embeddings: the job itself
        # only guarantees that they exist, and the caller may pass any threshold.
        faces = facestore.list_faces()
        body = {**body, **_proposal_for(faces, None)}
    return body


@router.post("/auto-group/apply")
async def apply_face_groups(payload: FaceGroupsApply) -> dict[str, Any]:
    """Write the groups the user confirmed. Nothing else does.

    A face that already sits in a *different* group is only moved when
    `overwrite` is set; otherwise it is reported back so the client can ask. The
    proposal endpoint reports the same conflicts, so a well-behaved client sends
    `overwrite: true` only for the clusters the user accepted.
    """
    written = 0
    skipped: list[dict[str, str]] = []
    unknown: list[str] = []
    for assignment in payload.assignments:
        for face_id in assignment.face_ids:
            try:
                path = facestore.face_path(face_id)
            except facestore.UnsafeFaceId:
                unknown.append(face_id)
                continue
            if not path.is_file():
                unknown.append(face_id)
                continue
            current = facestore.read_group(face_id)
            if current and current != assignment.group and not payload.overwrite:
                skipped.append({"face_id": face_id, "current_group": current})
                continue
            facestore.write_group(face_id, assignment.group)
            written += 1
    return {"written": written, "skipped": skipped, "unknown": unknown}


@router.get("/{face_id}/similar", response_model=SimilarFaces)
async def similar_faces(face_id: str, limit: int = Query(default=8, ge=1, le=50)) -> dict[str, Any]:
    """Library faces most like this one, best first. Scores only, never vectors."""
    try:
        path = facestore.face_path(face_id)
    except facestore.UnsafeFaceId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such face asset")
    target = faceembed.embed_face_id(face_id)
    if target is None:
        raise HTTPException(status_code=422, detail="no face was detected in this image")
    # Embedding the target does not embed the library: every candidate needs a
    # vector too, and the first call for a library computes them here.
    import numpy as np

    ids, vectors, kept = [], [], []
    for face in facestore.list_faces():
        vec = faceembed.embed_face_id(face["face_id"])
        if vec is None:
            continue
        ids.append(face["face_id"])
        vectors.append(vec)
        kept.append(face)
    if face_id not in ids:
        ids.insert(0, face_id)
        vectors.insert(0, target)
        kept.insert(0, facestore.get_face(face_id))
    ranked = facecluster.similar_to(face_id, ids, np.stack(vectors), limit=limit)
    by_id = {f["face_id"]: f for f in kept}
    return {
        "face_id": face_id,
        "similar": [
            SimilarFace(
                face_id=fid,
                display_name=by_id[fid].get("display_name"),
                group=by_id[fid].get("group"),
                thumbnail_url=by_id[fid].get("thumbnail_url"),
                score=round(score, 4),
            ).model_dump()
            for fid, score in ranked
        ],
    }

