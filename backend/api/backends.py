"""Generation-backend metadata.

The predecessor's optional remote routes were removed. A future backend joins
through ``FrameGenerator`` rather than registering a parallel scheduling path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.config import get_settings

router = APIRouter(prefix="/api", tags=["backends"])


@router.get("/backends")
async def list_backends() -> dict[str, Any]:
    settings = get_settings()
    return {
        "default": settings.default_backend,
        "backends": [
            {
                "id": "engine",
                "label": "VisoSwap engine",
                "config": "source face + typed setting overrides",
            }
        ],
    }
