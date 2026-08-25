"""`ProjectScheduler.wait_closed` -- the handle-back guarantee.

A forced stop answers immediately and finishes its cleanup detached, which is
the point of it. But the next start DELETES what that cleanup is still holding:
the encoder keeps `output.mp4.part` open, and Windows refuses to unlink an open
file with `WinError 32`. `wait_closed` is how a restart waits for the handle.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services.scheduler import ProjectScheduler


def _bare() -> ProjectScheduler:
    """A scheduler with only the reaper slot -- no database, no workers."""
    scheduler = ProjectScheduler.__new__(ProjectScheduler)
    scheduler.project_id = "p"
    scheduler._reaper = None
    return scheduler


@pytest.mark.asyncio
async def test_waiting_on_a_scheduler_that_never_forced_a_stop_returns_at_once():
    await _bare().wait_closed()


@pytest.mark.asyncio
async def test_waiting_blocks_until_the_detached_cleanup_has_finished():
    scheduler = _bare()
    released = False

    async def cleanup() -> None:
        nonlocal released
        await asyncio.sleep(0.05)
        released = True

    scheduler._reaper = asyncio.create_task(cleanup())
    await scheduler.wait_closed()
    assert released, "restart was let through while the encoder still held the file"


@pytest.mark.asyncio
async def test_a_cleanup_that_failed_does_not_take_the_restart_down_with_it():
    """The file may still be locked, but that is the caller's error to report
    -- a raise here would surface as a 500 from a perfectly ordinary restart."""
    scheduler = _bare()

    async def cleanup() -> None:
        raise RuntimeError("encoder would not drain")

    scheduler._reaper = asyncio.create_task(cleanup())
    await scheduler.wait_closed()
