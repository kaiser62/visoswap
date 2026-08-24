"""The takes surface: list and serve the output folder (D-10, D-12).

Every run close exports a take into `settings.output_dir`; this router is how
a Gallery page sees them. The folder sits outside `data/projects`, so an
untrusted `{name}` here gets the same validate-then-join treatment as
`facestore.face_path` gives ids: reject anything that is not exactly its own
basename, that would not survive `cache.sanitize_filename` unchanged, or that
does not end `.mp4` — then join, resolve and confirm containment against
`output_dir` itself before any file is opened.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response

from backend.api.playback import range_response
from backend.api.schemas import TakeOut
from backend.config import get_settings
from backend.services import cache

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/takes", tags=["takes"])


def _400(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _validate_take_name(name: str) -> str:
    """Return the name unchanged if it is provably safe, else raise 400.

    T-02-07's rule applied to a filename instead of an id. The basename check,
    the sanitize round-trip and the `.mp4` suffix each reject a different
    shape; the resolved-parent check afterwards is the belt to those braces.
    """
    if not name or Path(name).name != name:
        raise _400("invalid take name")
    if name != cache.sanitize_filename(name, ""):
        raise _400("invalid take name")
    if not name.endswith(".mp4"):
        raise _400("takes are mp4 files")
    return name


def _resolve_within_output_dir(name: str) -> Path:
    """Validate first, then join — never the other way around."""
    out_dir = get_settings().output_dir
    resolved = (out_dir / name).resolve()
    # The same containment assertion `cache.assert_within_project` makes,
    # against a different root: the resolved parent must be the output dir.
    if resolved.parent != out_dir.resolve():
        raise _400("take resolves outside the output folder")
    return resolved


@router.get("", response_model=list[TakeOut])
async def list_takes() -> list[dict[str, Any]]:
    """Everything ever exported, newest-first.

    A missing directory yields an empty list — the output folder does not
    exist until the first export, and an empty Gallery is a normal state, not
    an error.
    """
    out_dir = get_settings().output_dir
    if not out_dir.is_dir():
        return []
    takes = [
        p for p in out_dir.iterdir()
        if p.is_file() and p.name.endswith(".mp4")
    ]
    takes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": path.name,
            "bytes": stat.st_size,
            "modified": stat.st_mtime,
            "partial": path.name.endswith(".partial.mp4"),
            "url": f"/api/takes/{quote(path.name)}",
        }
        for path in takes
        for stat in (path.stat(),)
    ]


@router.get("/{name}")
async def read_take(request: Request, name: str):
    """Stream one take with Range support so a video element can seek."""
    _validate_take_name(name)
    path = _resolve_within_output_dir(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such take")

    # no-store rather than immutable: a take never changes once exported, but
    # deletion would make cached copies actively wrong — the same reasoning
    # download_output records for the growing `.part`.
    return range_response(request, path, "video/mp4", cache_control="no-store")


@router.delete("/{name}", status_code=204, response_class=Response)
async def delete_take(name: str):
    """Remove a take. 404 when it is already gone."""
    _validate_take_name(name)
    path = _resolve_within_output_dir(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such take")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"could not delete take: {exc}"
        ) from exc
    log.info("[TAKES] deleted=%s", name)
    return Response(status_code=204)
