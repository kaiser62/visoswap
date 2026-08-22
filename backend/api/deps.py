"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Path

from backend.models.database import Database, db
from backend.services.cache import UnsafePathError, validate_project_id


def get_db() -> Database:
    return db


async def get_project(
    project_id: str = Path(..., min_length=32, max_length=32),
    database: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        validate_project_id(project_id)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project = await database.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project
