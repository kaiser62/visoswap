"""The compose queue surface: list jobs, add one, call one off.

The queue itself is documented in `services/compose_queue`. This router exists
so the panel in the UI has something to poll and something to press: a compose
is a long re-encode kicked off by a stop the user may not have thought of as
starting one, and work that runs unasked has to be visible and stoppable.

Job ids are generated here, never supplied, so no `{job_id}` in these paths ever
becomes a path segment on disk.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.services import cache
from backend.services.compose_queue import queue
from backend.services.scheduler import registry
from backend.api.deps import get_project

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["compose"])


@router.get("/compose/jobs")
async def list_jobs() -> dict[str, Any]:
    """Every job this process knows about, newest first.

    Global rather than per-project: the queue is serial across all projects, so
    a job of yours sitting behind someone else's is exactly the thing the panel
    has to be able to show.
    """
    return {"jobs": queue.jobs()}


@router.post("/projects/{project_id}/compose")
async def enqueue_compose(
    project: dict[str, Any] = Depends(get_project),
) -> dict[str, Any]:
    """Compose this project's generated frames into a take, on demand.

    Refused while a run is going: the frame directory is still being written,
    and a pass over it would compose a snapshot that is out of date before it
    finishes. Stop the run — which queues one of these anyway — and it will
    cover everything.
    """
    video_path = project.get("video_path")
    if not video_path:
        raise HTTPException(status_code=400, detail="project has no local video source")

    scheduler = registry.peek(project["id"])
    if scheduler is not None and scheduler.running:
        raise HTTPException(
            status_code=409, detail="stop the run before composing it"
        )

    face_source = str(project.get("source_face_path") or "")
    job = queue.enqueue(
        project["id"],
        cache.to_absolute(video_path),
        project_name=str(project.get("name") or ""),
        face=face_source.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0],
    )
    return job.snapshot()


@router.post("/compose/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Call off a job, whether it is waiting or already re-encoding."""
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such compose job")
    if not job.active:
        raise HTTPException(status_code=409, detail="that job has already finished")
    queue.cancel(job_id)
    return job.snapshot()


@router.delete("/compose/jobs/{job_id}", status_code=204, response_class=Response)
async def forget_job(job_id: str):
    """Drop a finished job from the list. The take it produced is untouched."""
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such compose job")
    if not queue.forget(job_id):
        raise HTTPException(
            status_code=409, detail="cancel the job before clearing it"
        )
