"""In-process pub/sub feeding the WebSocket endpoint.

Subscribers are per-connection bounded queues: a slow or dead browser can never
apply backpressure to generation — its events are dropped instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_MAXSIZE = 256


class EventHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, project_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.setdefault(project_id, set()).add(queue)
        return queue

    async def unsubscribe(
        self, project_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        async with self._lock:
            subs = self._subscribers.get(project_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(project_id, None)

    def publish(self, project_id: str, event: dict[str, Any]) -> None:
        """Non-blocking fan-out. Safe to call from anywhere in the event loop."""
        for queue in list(self._subscribers.get(project_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event and retry once; never block the producer.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    log.debug("dropping event for saturated subscriber")

    def subscriber_count(self, project_id: str) -> int:
        return len(self._subscribers.get(project_id, ()))


hub = EventHub()
