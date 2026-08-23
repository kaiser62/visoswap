"""Project CRUD and video source binding."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from backend.api.deps import get_db, get_project
from backend.api.schemas import ProjectCreate, ProjectUpdate, UrlSource
from backend.config import get_settings
from backend.models.database import Database
from backend.services import cache, video
from backend.services.ffmpeg import FFmpegError, probe
from backend.services.scheduler import effective_interval, registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])

CHUNK = 1024 * 1024


@router.post("", status_code=201)
async def create_project(
    payload: ProjectCreate, db: Database = Depends(get_db)
) -> dict[str, Any]:
    fields = payload.model_dump(exclude_none=True)
    project = await db.create_project(**fields)
    cache.ensure_project_dirs(project["id"])
    return _public(project)


@router.get("")
async def list_projects(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    projects = await db.list_projects()
    return [_public(p) for p in projects]


@router.get("/{project_id}")
async def read_project(
    project: dict[str, Any] = Depends(get_project), db: Database = Depends(get_db)
) -> dict[str, Any]:
    data = _public(project)
    data["counts"] = await db.counts(project["id"])
    return data


@router.patch("/{project_id}")
async def update_project(
    payload: ProjectUpdate,
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)

    updated = await db.update_project(project["id"], **updates)
    assert updated is not None

    # Changing the interval or the mode invalidates the target grid, so drop
    # queued work that no longer lands on a target. Completed frames are kept.
    regridded = (
        "interval" in updates
        and float(updates["interval"]) != float(project["interval"])
    ) or (
        "generation_mode" in updates
        and updates["generation_mode"] != project.get("generation_mode")
    )
    if regridded:
        scheduler = registry.peek(project["id"])
        if scheduler is not None:
            await scheduler.update_playback(scheduler.current_time, seeked=True)
    return _public(updated)


@router.delete("/{project_id}", status_code=204, response_class=Response)
async def delete_project(
    project: dict[str, Any] = Depends(get_project), db: Database = Depends(get_db)
):
    project_id = project["id"]
    await registry.stop(project_id)
    await db.delete_project(project_id)
    shutil.rmtree(cache.project_dir(project_id), ignore_errors=True)


@router.post("/{project_id}/source")
async def upload_source(
    file: UploadFile = File(...),
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Stream an uploaded video to disk, then probe it."""
    settings = get_settings()
    project_id = project["id"]
    try:
        safe_name = video.validate_upload_name(file.filename or "video.mp4")
    except video.VideoSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cache.ensure_project_dirs(project_id)
    dest = cache.source_dir(project_id) / safe_name
    cache.assert_within_project(dest, project_id)

    written = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with tmp.open("wb") as handle:
            while chunk := await file.read(CHUNK):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="file too large")
                handle.write(chunk)
        tmp.replace(dest)
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=507, detail=f"could not write file: {exc}") from exc
    finally:
        await file.close()

    return await _bind_source(db, project_id, str(dest), video_url=None)


@router.post("/{project_id}/url")
async def set_source_url(
    payload: UrlSource,
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    project_id = project["id"]
    try:
        source, info = await video.resolve_url(
            project_id,
            payload.url,
            require_local=False,
        )
    except (video.VideoSourceError, FFmpegError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    is_local = not source.lower().startswith(("http://", "https://"))
    updated = await db.update_project(
        project_id,
        video_path=source if is_local else None,
        video_url=payload.url if not is_local else None,
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        error=None,
    )
    assert updated is not None
    return _public(updated)


async def _bind_source(
    db: Database, project_id: str, path: str, video_url: str | None
) -> dict[str, Any]:
    try:
        info = await probe(path)
    except FFmpegError as exc:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"invalid video: {exc}") from exc

    updated = await db.update_project(
        project_id,
        video_path=path,
        video_url=video_url,
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        error=None,
    )
    assert updated is not None
    return _public(updated)


def _public(project: dict[str, Any]) -> dict[str, Any]:
    """Never leak absolute filesystem paths to the browser."""
    data = dict(project)
    local_path = data.pop("video_path", None)
    data.pop("source_face_path", None)
    data["has_video"] = bool(local_path or data.get("video_url"))
    data["video_filename"] = Path(local_path).name if local_path else None
    data["video_src"] = (
        f"/api/projects/{project['id']}/video" if data["has_video"] else None
    )
    # Stream mode's grid is the video's fps, not `interval`. The overlay picker
    # must use the same spacing the scheduler plans on.
    data["effective_interval"] = effective_interval(project)
    return data
