"""Settings API over the Phase 3 store (plan 05-01/05-02).

These endpoints wrap ``visoswap.settings.store``/``presets`` on a synchronous
``sqlite3`` connection to the same database file the async backend uses, run in
``asyncio.to_thread``. The Phase 3 store is synchronous; the Phase 4 backend is
asynchronous (aiosqlite). This module is the bridge, and it is the only place
that opens a synchronous connection to ``get_settings().db_path``.

The settings tables and seeded presets are ensured by the API itself on a bare
database, keyed by resolved db path, so the endpoints work whether or not Phase 4
already applied them.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import get_project
from backend.config import get_settings
from visoswap import schema
from visoswap.settings import presets, store

router = APIRouter(prefix="/api", tags=["settings"])

#: db paths whose settings schema + presets have already been ensured. Re-run
#: when the path differs (tests repoint the database), hence a set, not a flag.
_ready_paths: set[str] = set()


def _settings_error(exc: Exception) -> HTTPException:
    """Map a store/preset error to the HTTP status the frontend expects.

    The three store validation errors are caller mistakes -> 400 with the message.
    A raw sqlite3.Error is a server fault -> 500.
    """
    if isinstance(
        exc,
        (store.UnknownSettingsKey, store.InvalidSettingValue, store.WrongTier, presets.UnknownPreset),
    ):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, sqlite3.Error):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@contextmanager
def _sync_conn() -> Iterator[sqlite3.Connection]:
    """Open a synchronous connection to the app DB and yield it, closing after.

    Deliberately not the async Database singleton — the Phase 3 store needs a
    synchronous sqlite3.Connection to the same file. WAL journal mode is
    persistent in the file, so a concurrent async connection is safe.
    """
    conn = sqlite3.connect(str(get_settings().db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


def _ensure_ready(conn: sqlite3.Connection) -> None:
    """Apply the settings DDL and seed presets once per resolved db path.

    Idempotent: ``apply_settings_schema`` is all ``IF NOT EXISTS`` and
    ``seed_presets`` reports inserted/updated/unchanged. ``apply_settings_schema``
    uses ``executescript`` which commits an open transaction, so this runs before
    any store write that opens one.
    """
    path = str(get_settings().db_path)
    if path in _ready_paths:
        return
    from visoswap.settings.db import apply_settings_schema

    apply_settings_schema(conn)
    presets.seed_presets(conn)
    conn.commit()
    _ready_paths.add(path)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


@router.get("/schema")
async def get_schema() -> dict[str, Any]:
    """The resolved 201-entry settings schema, DFM options filled from disk."""
    return {"schema": dict(schema.HEADER), "widgets": schema.load()}


@router.get("/projects/{project_id}/settings")
async def get_project_settings(
    project: dict[str, Any] = Depends(get_project),
) -> dict[str, Any]:
    """All 201 settings values resolved for a project (face->project->global->default)."""

    def _read() -> dict[str, Any]:
        with _sync_conn() as conn:
            _ensure_ready(conn)
            return store.resolve_all(conn, project_id=project["id"])

    try:
        values = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 - mapped to the right status
        raise _settings_error(exc) from exc
    return {"values": values}


@router.get("/presets")
async def get_presets() -> dict[str, Any]:
    """The migrated presets."""

    def _read() -> list[dict[str, Any]]:
        with _sync_conn() as conn:
            _ensure_ready(conn)
            return presets.list_presets(conn)

    try:
        result = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001
        raise _settings_error(exc) from exc
    return {"presets": result}
