"""Takes API contract tests (plan 05.1-03, D-10/D-12).

The gallery surface lists the output folder newest-first and serves its files
with Range support, under the same validate-then-join rule every other
file-touching route uses. Takes are small real mp4s (the `lavfi testsrc`
helper from tests/test_recorder.py) so the Range assertions run against
genuine byte ranges.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import db


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """The real app with DATA_DIR and OUTPUT_DIR under tmp_path."""
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///{}".format(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    get_settings.cache_clear()
    db._conn = None
    app = create_app()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        get_settings.cache_clear()


def _mp4(name: str) -> None:
    """Write one small real mp4 into the output folder."""
    out_dir = get_settings().output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_settings().ffmpeg_bin, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(out_dir / name),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _age(name: str, seconds: float) -> None:
    """Push a take's mtime into the past, deterministically ordering the list."""
    path = get_settings().output_dir / name
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


# ---------------------------------------------------------------------------
# GET /api/takes
# ---------------------------------------------------------------------------


def test_list_is_newest_first_with_name_bytes_modified_partial_url(client):
    _mp4("alpha.mp4")
    _mp4("beta.mp4")
    _age("alpha.mp4", 60)

    resp = client.get("/api/takes")
    assert resp.status_code == 200, resp.text
    takes = resp.json()
    assert [t["name"] for t in takes] == ["beta.mp4", "alpha.mp4"]
    for take in takes:
        assert set(take) == {"name", "bytes", "modified", "partial", "url"}, take
        assert isinstance(take["bytes"], int) and take["bytes"] > 0
        assert isinstance(take["modified"], float)
        assert take["url"] == f"/api/takes/{take['name']}"
    # Newest first: beta's mtime is now, alpha's is a minute back.
    assert takes[0]["modified"] > takes[1]["modified"]


def test_partial_flag_is_true_exactly_for_partial_mp4_names(client):
    _mp4("movie.mp4")
    _mp4("movie.partial.mp4")

    takes = {t["name"]: t for t in client.get("/api/takes").json()}
    assert takes["movie.mp4"]["partial"] is False
    assert takes["movie.partial.mp4"]["partial"] is True


def test_absent_or_empty_output_folder_lists_empty(client):
    # The folder does not exist until the first export: an empty Gallery is a
    # normal state, not an error.
    resp = client.get("/api/takes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    get_settings().output_dir.mkdir(parents=True, exist_ok=True)
    resp = client.get("/api/takes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/takes/{name}
# ---------------------------------------------------------------------------


def test_take_serves_whole_file_and_range_requests_answer_206(client):
    _mp4("clip.mp4")
    path = get_settings().output_dir / "clip.mp4"
    size = path.stat().st_size

    whole = client.get("/api/takes/clip.mp4")
    assert whole.status_code == 200, whole.text
    assert whole.headers["content-type"].startswith("video/")
    assert whole.content == path.read_bytes()

    ranged = client.get("/api/takes/clip.mp4", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206, ranged.text
    assert ranged.headers["content-range"] == f"bytes 0-99/{size}"
    assert ranged.headers["content-length"] == "100"
    assert ranged.content == path.read_bytes()[:100]


def test_dotdot_traversal_is_400(client):
    # Dot segments ride Windows separators: forward slashes are decoded into
    # real path separators by every HTTP client/server pair before routing,
    # so a backslash-carrying `..` is the shape that actually reaches the
    # handler. It must die in the validator, never at the filesystem.
    _mp4("keep.mp4")
    resp = client.get(r"/api/takes/..\..\app.db")
    assert resp.status_code == 400, resp.text


def test_encoded_dotdot_traversal_never_reaches_the_take_route(client):
    # `%2F` decodes to `/` before route matching on any ASGI server, so the
    # encoded traversal cannot arrive as a `{name}` segment at all — it is
    # refused by routing itself (404), one gate earlier than 400. Either way
    # app.db is never served.
    _mp4("keep.mp4")
    resp = client.get("/api/takes/..%2F..%2Fapp.db")
    assert resp.status_code == 404, resp.text


def test_absolute_path_is_400(client):
    _mp4("keep.mp4")
    resp = client.get("/api/takes/D%3A%5Celsewhere%5Capp.db")
    assert resp.status_code == 400, resp.text


def test_backslash_name_is_400(client):
    _mp4("keep.mp4")
    resp = client.get(r"/api/takes/..\app.db")
    assert resp.status_code == 400, resp.text


def test_unknown_take_is_404(client):
    _mp4("keep.mp4")
    resp = client.get("/api/takes/nope.mp4")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# DELETE /api/takes/{name}
# ---------------------------------------------------------------------------


def test_delete_removes_the_take_then_404s(client):
    _mp4("gone.mp4")
    path = get_settings().output_dir / "gone.mp4"
    assert path.is_file()

    first = client.delete("/api/takes/gone.mp4")
    assert first.status_code == 204, first.text
    assert not path.exists()

    second = client.delete("/api/takes/gone.mp4")
    assert second.status_code == 404, second.text
