"""Scheduler control, playback reporting and queue status."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import get_db, get_project
from backend.api.schemas import PlaybackUpdate, SchedulerStart
from backend.models.database import Database
from backend.services import cache, recorder
from backend.services.ffmpeg import ffmpeg_available
from backend.services.generator import build_generator
from backend.services.scheduler import generation_window, registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["generation"])


@router.post("/{project_id}/scheduler/start")
async def start_scheduler(
    payload: SchedulerStart,
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    if not project.get("video_path") and not project.get("video_url"):
        raise HTTPException(status_code=400, detail="project has no video source")
    # Same fail-fast posture: a faceless run would raise from `generate_at` and
    # fail every frame rather than the run, so refuse here where the user can
    # act on the message (D-05's no-source state is refusable, not silent).
    if not project.get("source_face_path"):
        raise HTTPException(status_code=400, detail="project has no source face")
    if not ffmpeg_available():
        raise HTTPException(status_code=503, detail="ffmpeg/ffprobe not found on PATH")

    # Fail fast on unusable configuration rather than failing every frame.
    generator = build_generator(project, 0.0)
    try:
        await generator.validate()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await generator.close()

    scheduler = await registry.get(project["id"], db)
    # Starting is a restart: everything below deletes the previous run's state,
    # so that run has to be all the way down first. Two things go wrong
    # otherwise -- a still-running scheduler carries on against a queue and a
    # cache that have just been emptied under it, and a forced stop's encoder
    # still holds `output.mp4.part` open, which Windows will not let us unlink.
    if scheduler.running:
        await scheduler.stop()
    await scheduler.wait_closed()

    # Every run starts from an empty queue and an empty cache. A backlog was
    # planned against the old playhead, interval and mode; a completed frame
    # was made with whatever face and model were set at the time. Neither is
    # worth keeping, and reusing them silently would show stale output.
    rows = await db.delete_frames(project["id"])
    files = cache.clear_frames(project["id"])
    # The previous run's recording goes with them: leaving it would let this run
    # silently extend a video made with a different face, model or resolution.
    # Best-effort: a previous take that something still holds open (this app,
    # serving it to a player that is still pointed at it) is left where it is
    # and this run records under a free name instead. Being watched must never
    # be a reason a run refuses to start.
    files += recorder.clear_output(project["id"])
    if rows or files:
        log.info(
            "[SCHEDULER] project=%s cleared rows=%d files=%d",
            project["id"], rows, files,
        )

    target_range = None
    if payload.range_duration is not None:
        start = payload.range_start or 0.0
        end = start + payload.range_duration
        duration = project.get("duration")
        if duration is not None:
            end = min(end, float(duration))
        if end <= start:
            raise HTTPException(status_code=400, detail="range starts past the video")
        target_range = (start, end)

    # A range run ignores the playhead, so seed the window at its start rather
    # than wherever the player happens to be sitting.
    scheduler.current_time = payload.range_start or payload.current_time
    if payload.full_video is not None:
        await db.update_project(project["id"], full_video_mode=payload.full_video)
    await scheduler.start(full_video=payload.full_video, target_range=target_range)
    return await _status(project["id"], db)


@router.post("/{project_id}/scheduler/stop")
async def stop_scheduler(
    force: bool = False,
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Stop the run. `?force=true` returns without waiting for the cleanup.

    The ordinary stop waits for workers to unwind and for the recorder to
    drain, which is what keeps the recording whole — but a worker stuck inside
    a model call can hold that wait open for as long as the call takes. The
    forced stop is for exactly that case and trades the tail of the recording
    for a stop that always returns.
    """
    await registry.stop(project["id"], force=force)
    return await _status(project["id"], db)


@router.post("/{project_id}/recording/release")
async def release_recording(
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Delete this project's working recordings, and say what would not go.

    The caller's job is to let go FIRST: a `<video>` still pointed at
    `/output` keeps the file open through this app, and nothing the server can
    do will take that handle off it. Point the player elsewhere, then call
    this. Whatever remains is held by something outside the app -- a media
    player with the export open, a scanner mid-file -- and is named in `held`
    so the message can say which file and let the user close it.

    Refused while a run is going: those files are the run's own output.
    """
    scheduler = registry.peek(project["id"])
    if scheduler is not None and scheduler.running:
        raise HTTPException(
            status_code=409, detail="stop the run before releasing its recording"
        )
    removed = recorder.clear_output(project["id"])
    held = [path.name for path in recorder.recordings(project["id"])]
    return {"removed": removed, "held": held, "recording": _recording_info(project["id"])}


@router.post("/{project_id}/playback")
async def report_playback(
    payload: PlaybackUpdate,
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """The frontend reports its position; the scheduler moves its window.

    This is advisory only — the video never waits for this call to return.
    """
    scheduler = registry.peek(project["id"])
    if scheduler is None or not scheduler.running:
        return {"running": False, "current_time": payload.current_time}
    await scheduler.update_playback(payload.current_time, seeked=payload.seeked)
    start, end = generation_window(
        payload.current_time, project["lookahead"], project.get("duration")
    )
    return {
        "running": True,
        "current_time": payload.current_time,
        "window_start": start,
        "window_end": end,
    }


@router.get("/{project_id}/generation/status")
async def generation_status(
    project: dict[str, Any] = Depends(get_project), db: Database = Depends(get_db)
) -> dict[str, Any]:
    return await _status(project["id"], db)


@router.post("/{project_id}/generation/retry-failed")
async def retry_failed(
    project: dict[str, Any] = Depends(get_project), db: Database = Depends(get_db)
) -> dict[str, Any]:
    reset = await db.reset_failed(project["id"])
    scheduler = registry.peek(project["id"])
    if scheduler is not None:
        scheduler.notify_worker_progress()
    return {"requeued": reset, **await _status(project["id"], db)}


async def _status(project_id: str, db: Database) -> dict[str, Any]:
    scheduler = registry.peek(project_id)
    counts = await db.counts(project_id)
    return {
        "project_id": project_id,
        "running": bool(scheduler and scheduler.running),
        "full_video_mode": bool(scheduler and scheduler.full_video_mode),
        "current_time": scheduler.current_time if scheduler else 0.0,
        "counts": counts,
        "average_duration": await db.average_duration(project_id),
        "recording": _recording_info(project_id),
    }


def _recording_info(project_id: str) -> dict[str, Any]:
    """Whether a recording can be downloaded, and whether it is finished.

    Reported from the status poll rather than the project payload because the
    file grows during a run: the point of the recorder is that a partial
    download is available *while* generation is still going.
    """
    current = recorder.current_recording(project_id)
    if current is None:
        return {"available": False, "complete": False, "bytes": 0}
    path, complete = current
    return {"available": True, "complete": complete, "bytes": path.stat().st_size}
