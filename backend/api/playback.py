"""Video streaming and generated-frame delivery.

Range support matters: without it the browser cannot seek a local file, and
seeking is central to this application.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from backend.api.deps import get_db, get_project
from backend.models.database import STATUS_COMPLETED, Database
from backend.services import cache, recorder
from backend.services.scheduler import (
    effective_interval,
    grid_rate,
    target_for_rate,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["playback"])

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
STREAM_CHUNK = 512 * 1024


@router.get("/{project_id}/video")
async def stream_video(
    request: Request, project: dict[str, Any] = Depends(get_project)
):
    """Serve the local source file with Range support.

    Remote direct URLs are redirected instead of proxied: the browser can range
    -request them itself, and proxying would waste the backend's bandwidth.
    """
    if project.get("video_url") and not project.get("video_path"):
        return RedirectResponse(project["video_url"])

    raw_path = project.get("video_path")
    if not raw_path:
        raise HTTPException(status_code=404, detail="project has no video")

    path = Path(raw_path)
    # The path came from our own upload/download code; verify anyway.
    try:
        path = cache.assert_within_project(path, project["id"])
    except cache.UnsafePathError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="video file is missing")

    file_size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
        )

    match = RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=400, detail="malformed Range header")

    start_raw, end_raw = match.groups()
    if start_raw:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1
    else:
        # Suffix range: last N bytes.
        length = int(end_raw or 0)
        start = max(0, file_size - length)
        end = file_size - 1

    if start >= file_size or start > end:
        return StreamingResponse(
            iter(()),
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    end = min(end, file_size - 1)

    async def body() -> AsyncIterator[bytes]:
        remaining = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(STREAM_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        body(),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/{project_id}/output")
async def download_output(project: dict[str, Any] = Depends(get_project)):
    """Serve the composed mp4, finished or still in progress.

    The in-progress `.part` is a valid fragmented mp4 at every instant, so it is
    served exactly like a finished one — that is the whole point of the recorder's
    muxing flags. `complete=0` on the partial lets the caller tell them apart.

    No-store rather than no-cache: the partial file grows underneath the client,
    and a cached copy of an earlier, shorter version is worse than no copy.
    """
    finished = recorder.output_path(project["id"])
    partial = recorder.partial_path(project["id"])
    path, complete = (finished, True) if finished.is_file() else (partial, False)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no recording for this project")

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{project.get('name') or 'output'}.mp4",
        headers={
            "Cache-Control": "no-store",
            "X-Recording-Complete": "1" if complete else "0",
        },
    )


@router.get("/{project_id}/frames")
async def list_frames(
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
    status: str | None = Query(default=None),
    start: float | None = Query(default=None, ge=0),
    end: float | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Frame index for the timeline. Only metadata — never image payloads."""
    rows = await db.list_frames(project["id"], status=status, start=start, end=end)
    return {
        "project_id": project["id"],
        "interval": effective_interval(project),
        "duration": project.get("duration"),
        "frames": [
            {
                "timestamp": row["timestamp"],
                "status": row["status"],
                "priority": row["priority"],
                "duration": row["generation_duration"],
                "attempts": row["attempts"],
                "error": row["error"],
                "url": (
                    f"/api/projects/{project['id']}/frame/"
                    f"{cache.timestamp_key(row['timestamp'])}"
                    if row["status"] == STATUS_COMPLETED
                    else None
                ),
            }
            for row in rows
        ],
    }


@router.get("/{project_id}/frame/{key}")
async def get_frame(
    key: str,
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
):
    """Serve one generated frame. `key` is the canonical `000123.450` form."""
    try:
        ts = cache.key_to_timestamp(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid timestamp key") from exc

    row = await db.get_frame(project["id"], ts)
    if row is None or row["status"] != STATUS_COMPLETED or not row["generated_frame_path"]:
        raise HTTPException(status_code=404, detail="no generated frame at timestamp")

    path = cache.to_absolute(row["generated_frame_path"])
    try:
        path = cache.assert_within_project(path, project["id"])
    except cache.UnsafePathError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="generated frame is missing")

    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "image/webp",
        # Content at a given timestamp is immutable once generated.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/{project_id}/frame-at")
async def frame_at(
    t: float = Query(..., ge=0),
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Resolve which generated frame should be shown at playback time `t`.

    Policy: the nearest *previous* target timestamp. A future frame is never
    returned, so the overlay cannot run ahead of the video.
    """
    target = target_for_rate(t, grid_rate(project))
    row = await db.get_frame(project["id"], target)
    if row is None or row["status"] != STATUS_COMPLETED:
        return {"timestamp": target, "available": False, "url": None}
    return {
        "timestamp": target,
        "available": True,
        "url": (
            f"/api/projects/{project['id']}/frame/{cache.timestamp_key(target)}"
        ),
    }
