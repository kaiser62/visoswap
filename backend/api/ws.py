"""WebSocket: live generation events for one project.

The socket is purely informational. If it drops, playback and generation both
continue; the frontend refetches the frame index on reconnect.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.models.database import db
from backend.services.cache import UnsafePathError, validate_project_id
from backend.services.events import hub
from backend.services.scheduler import registry

log = logging.getLogger(__name__)
router = APIRouter()

HEARTBEAT_SECONDS = 20.0


@router.websocket("/ws/projects/{project_id}")
async def project_events(websocket: WebSocket, project_id: str) -> None:
    try:
        validate_project_id(project_id)
    except UnsafePathError:
        await websocket.close(code=1008)
        return

    project = await db.get_project(project_id)
    if project is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = await hub.subscribe(project_id)

    scheduler = registry.peek(project_id)
    counts = await db.counts(project_id)
    await websocket.send_json(
        {
            "type": "hello",
            "project_id": project_id,
            "running": bool(scheduler and scheduler.running),
            "counts": counts,
        }
    )

    reader = asyncio.create_task(_drain_client(websocket))
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        log.exception("websocket loop failed for project=%s", project_id)
    finally:
        reader.cancel()
        await hub.unsubscribe(project_id, queue)


async def _drain_client(websocket: WebSocket) -> None:
    """Consume inbound messages so the peer's close frame is noticed promptly."""
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        return
