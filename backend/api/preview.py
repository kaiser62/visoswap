"""One-shot preview: render a single swapped frame without a scheduler (D-09).

The only way to see a swapped frame used to be to start a run. Preview costs
one frame instead: it builds a throwaway generator exactly like
`start_scheduler`'s validation block, one step further — bind, `generate_at`,
close in a `finally` whether or not anything succeeded. The generator is never
registered, the registry is never touched and no frame row is written, so a
preview can never contend with playback generation.

Storage discipline: frames land in `cache.preview_dir`, never in
`cache.generated_dir`, because `Recorder._generated_for` bisects that listing
to compose the recording — a preview frame there would be muxed into the video.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.api.deps import get_db, get_project
from backend.api.schemas import PreviewRequest, PreviewResponse
from backend.models.database import Database
from backend.services import cache
from backend.services.generator import build_generator
from backend.services.scheduler import registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["preview"])

# Fixed name on purpose: a project holds at most one preview image, and a new
# preview overwrites it.
PREVIEW_NAME = "frame.jpg"


@router.post("/{project_id}/preview", response_model=PreviewResponse)
async def render_preview(
    payload: PreviewRequest,
    project: dict[str, Any] = Depends(get_project),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Render one swapped frame at `t`. Refuses first, works second."""
    running = registry.peek(project["id"])
    if running is not None and running.running:
        raise HTTPException(
            status_code=409,
            detail="generation is running; stop it before previewing",
        )
    if not project.get("video_path") and not project.get("video_url"):
        raise HTTPException(status_code=400, detail="project has no video source")
    if not project.get("source_face_path"):
        raise HTTPException(status_code=400, detail="project has no source face")

    # Floor to a real frame exactly like EngineFrameGenerator.generate_at does,
    # so the preview and the overlay never disagree about which frame a time
    # means. Without a usable fps there is nothing to floor against.
    fps = float(project.get("fps") or 0.0)
    stamped = float(payload.t)
    if fps > 0:
        stamped = round(int(payload.t * fps) / fps, 3)

    # The start_scheduler validation block, one step further: validate, bind,
    # generate, always close. A failure here is caller-visible configuration —
    # a missing model, an unreadable face image, an out-of-range timestamp —
    # and this endpoint exists precisely so the user finds out before a run.
    generator = build_generator(project, 0.0)
    try:
        await generator.validate()
        await generator.bind(project)
        result = await generator.generate_at(payload.t, PREVIEW_NAME)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await generator.close()

    # Atomic write (the upload boundary's pattern): a concurrent GET must never
    # observe a half-written image.
    dest = cache.preview_dir(project["id"]) / PREVIEW_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(result.data)
        tmp.replace(dest)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"could not store preview: {exc}"
        ) from exc

    log.info("[PREVIEW] project=%s t=%.3f bytes=%d", project["id"], stamped, len(result.data))
    return {"timestamp": stamped, "url": f"/api/projects/{project['id']}/preview"}


@router.get("/{project_id}/preview")
async def read_preview(project: dict[str, Any] = Depends(get_project)):
    """Serve the last rendered preview, or 404 before any was rendered.

    No-store rather than immutable: unlike a generated frame at a fixed
    timestamp the preview is overwritten in place — the same reasoning
    `download_output` records for the growing `.part`.
    """
    path = cache.preview_dir(project["id"]) / PREVIEW_NAME
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no preview rendered yet")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
