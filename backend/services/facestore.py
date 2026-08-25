"""The machine-global face library store (plan 05.1-02, D-03).

One store shared by every project, deliberately outside ``projects_dir`` so
deleting a project cannot take the user's face library with it. Identity is
content-addressed: a face's id is the blake2b digest of its bytes, so the same
image uploaded twice is one library entry and the on-disk name is *derived*
rather than accepted -- no user-supplied string ever becomes a path segment
(the T-02-07 boundary for this surface).

The module follows ``backend.services.cache``'s shape: functions over a
settings-derived root, with its own exception type mirroring
``cache.UnsafePathError``. Nothing outside this module knows the on-disk
layout; everything outside speaks face ids.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image

from backend.config import get_settings
from backend.services import cache

log = logging.getLogger(__name__)

FACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
#: Companion thumbnail suffix; never listed as a face in its own right.
THUMB_SUFFIX = ".thumb.jpg"
#: Companion original-name sidecar. The id stays the only path segment the
#: caller influences -- the name they chose is file *content*, never a name --
#: so a library of a hundred digests can still be read by a human.
NAME_SUFFIX = ".name.txt"
#: A display name longer than this is stored truncated; the UI shows one line.
NAME_MAX = 120
THUMB_SIZE = (112, 112)
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class UnsafeFaceId(ValueError):
    """Raised when a requested face id could not name a file in the store."""


def face_id_for(data: bytes) -> str:
    """A stable 32-hex content digest: same bytes, same face."""
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def faces_dir() -> Path:
    """The global store root, created on demand."""
    directory = get_settings().faces_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def image_suffix(filename: str | None) -> str:
    """Normalised stored suffix for an upload name, or ValueError.

    The caller's filename only ever contributes an extension from the
    still-image allowlist (via ``cache.sanitize_filename``, which strips any
    directory components first). An absent or unrecognised suffix is refused,
    naming what was rejected.
    """
    safe = cache.sanitize_filename(filename or "", "face")
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported image extension: {ext or '(none)'}")
    return ".jpg" if ext == ".jpeg" else ext


def _validate(face_id: str) -> str:
    if not FACE_ID_RE.fullmatch(face_id or ""):
        raise UnsafeFaceId("invalid face id")
    return face_id


def _stored_file(face_id: str) -> Path | None:
    base = faces_dir()
    for ext in sorted(ALLOWED_SUFFIXES):
        candidate = base / f"{face_id}{ext}"
        if candidate.is_file():
            return candidate
    return None


def face_path(face_id: str) -> Path:
    """Locate a face's stored file by id.

    The id is validated against the 32-hex pattern before anything touches the
    filesystem. The stored extension varies with the upload, so the known
    suffixes are probed; when nothing matches, the canonical `.jpg` path is
    returned for callers to find missing (they 404 on `is_file`).
    """
    _validate(face_id)
    return _stored_file(face_id) or (faces_dir() / f"{face_id}.jpg")


def thumb_path(face_id: str) -> Path:
    """The companion 112x112 JPEG path under the same id rule."""
    _validate(face_id)
    return faces_dir() / f"{face_id}{THUMB_SUFFIX}"


def name_path(face_id: str) -> Path:
    """The companion original-name sidecar path under the same id rule."""
    _validate(face_id)
    return faces_dir() / f"{face_id}{NAME_SUFFIX}"


def read_name(face_id: str) -> str | None:
    """The stored original name, or None when no sidecar was ever written.

    Faces stored before sidecars existed have none, and a sidecar is never
    required: callers fall back to the id, which is always available.
    """
    path = name_path(face_id)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def write_name(face_id: str, filename: str | None) -> None:
    """Record an upload's original name beside its face, first name winning.

    Content addressing means the same picture uploaded twice under two names is
    one face; keeping the first keeps the library stable rather than letting a
    later duplicate rename an entry the user already recognises. Failure is
    never fatal -- the name is a convenience, the id is the identity.
    """
    safe = cache.sanitize_filename(filename or "", "face").strip()
    if not safe:
        return
    path = name_path(face_id)
    if path.exists():
        return
    try:
        path.write_text(safe[:NAME_MAX], encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 - names are never fatal
        log.warning("[FACES] name sidecar for %s failed: %s", face_id, exc)


def _make_thumbnail(data: bytes, dest: Path) -> None:
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail(THUMB_SIZE)
        img.save(dest, "JPEG")


def store(data: bytes, filename: str | None) -> dict[str, Any]:
    """Store image bytes under their content digest plus a 112x112 thumbnail.

    Storing the same bytes again leaves exactly one pair of files: identity is
    the digest, so the second upload finds its destination already present. A
    thumbnail failure must not fail an upload -- it logs a warning and records
    the face with a null thumbnail URL, the never-fatal posture of
    ``Recorder._export``.
    """
    ext = image_suffix(filename)
    face_id = face_id_for(data)
    root = faces_dir()
    dest = root / f"{face_id}{ext}"
    thumb = root / f"{face_id}{THUMB_SUFFIX}"

    tmp = root / f".{face_id}{ext}.part"
    try:
        tmp.write_bytes(data)
        if not dest.exists():
            tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)

    write_name(face_id, filename)

    thumbnail_url: str | None = f"/api/faces/{face_id}/thumbnail"
    if not thumb.exists():
        try:
            _make_thumbnail(data, thumb)
        except Exception as exc:  # noqa: BLE001 - thumbnails are never fatal
            log.warning("[FACES] thumbnail for %s failed: %s", face_id, exc)
            thumbnail_url = None

    return {
        "face_id": face_id,
        "display_name": read_name(face_id) or face_id,
        "bytes": len(data),
        "url": f"/api/faces/{face_id}/image",
        "thumbnail_url": thumbnail_url,
    }


def list_faces() -> list[dict[str, Any]]:
    """Every stored face, newest-first. Thumbnails are never faces."""
    records: list[dict[str, Any]] = []
    for path in sorted(faces_dir().glob("*")):
        if not path.is_file() or path.name.endswith((THUMB_SUFFIX, NAME_SUFFIX)):
            continue
        match = FACE_ID_RE.fullmatch(path.stem)
        if not match:
            continue
        face_id = match.group(0)
        thumb = thumb_path(face_id)
        # The upload name lives in a sidecar, which faces stored before
        # sidecars existed do not have; those still list, named by their id.
        records.append(
            {
                "face_id": face_id,
                "display_name": read_name(face_id) or face_id,
                "bytes": path.stat().st_size,
                "url": f"/api/faces/{face_id}/image",
                "thumbnail_url": (
                    f"/api/faces/{face_id}/thumbnail" if thumb.exists() else None
                ),
                "_mtime": path.stat().st_mtime,
            }
        )
    records.sort(key=lambda r: r["_mtime"], reverse=True)
    for record in records:
        del record["_mtime"]
    return records


def delete(face_id: str) -> None:
    """Remove the file, its thumbnail and its name sidecar; unknown id is a no-op."""
    face_path(face_id).unlink(missing_ok=True)
    thumb_path(face_id).unlink(missing_ok=True)
    name_path(face_id).unlink(missing_ok=True)


def face_id_for_path(path: str | Path) -> str | None:
    """The face id of a stored file path, or None when outside the store."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    if resolved.parent != faces_dir().resolve():
        return None
    match = FACE_ID_RE.fullmatch(resolved.stem)
    return match.group(0) if match else None


def usage(projects: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Map each face id to the `{id, name}` projects whose source points at it.

    Comparison is on resolved paths, not strings, so a differently-spelled but
    identical path is still a match.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        raw = project.get("source_face_path")
        if not raw:
            continue
        face_id = face_id_for_path(raw)
        if face_id is None:
            continue
        out.setdefault(face_id, []).append(
            {"id": project["id"], "name": project["name"]}
        )
    return out
