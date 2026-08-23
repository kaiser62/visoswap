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

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from backend.api.deps import get_db
from backend.api.schemas import FaceOut
from backend.config import get_settings
from backend.models.database import Database
from backend.services import facestore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/faces", tags=["faces"])

CHUNK = 1024 * 1024

# Content at a content-addressed id never changes.
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


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
