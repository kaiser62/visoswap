"""The generated-frame URL must change when the picture behind it changes.

`/api/projects/{id}/frame/{key}` is served `immutable, max-age=1y`, which is
right only while the URL identifies one picture forever. It does not on its
own: a run started with a different face regenerates the same timestamps to
the same path. The browser then keeps handing back the images it already had,
the ones it evicted come back fresh, and the overlay shows two faces in one
take. `cache.frame_url` carries the frame row's own write time so the URL moves
exactly when, and only when, the picture does.
"""

from __future__ import annotations

from backend.services import cache


def test_the_url_carries_the_row_version():
    url = cache.frame_url("p1", 1.5, 1700000000.123)
    assert url.startswith("/api/projects/p1/frame/000001.500?v=")
    assert url.endswith("=1700000000123")


def test_regenerating_a_timestamp_moves_the_url():
    first = cache.frame_url("p1", 1.5, 1700000000.0)
    second = cache.frame_url("p1", 1.5, 1700000600.0)
    assert first != second


def test_the_same_frame_keeps_one_url():
    """Otherwise the year-long immutable caching buys nothing."""
    assert cache.frame_url("p1", 1.5, 1700000000.0) == cache.frame_url(
        "p1", 1.5, 1700000000.0
    )


def test_a_row_with_no_write_time_still_yields_a_usable_url():
    assert cache.frame_url("p1", 1.5, None) == "/api/projects/p1/frame/000001.500?v=0"
