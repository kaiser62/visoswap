"""URL ingest lands the source on disk, and `/video` never redirects.

The bug this pins: `/url` used to keep a direct URL remote and `/video` used to
307 the browser at it. Server-side that looks perfectly healthy -- the host
answers 200/206 with `Content-Type: video/mp4` -- but a cross-origin media
response carrying no CORS headers is discarded by Chromium's Opaque Response
Blocking, and the `<video>` element reports only `MEDIA_ELEMENT_ERROR: Format
error`. Nothing in the API surface said anything was wrong.

So the contract is now structural rather than advisory: after `/url`, the row
carries a local `video_path`, and `/video` answers with bytes, never with a
`Location`. `resolve_url` is stubbed -- the download mechanism has its own
coverage; what is asserted here is where the endpoint leaves the project.
"""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import db
from backend.services import cache
from backend.services.video import VideoInfo

REMOTE_URL = "https://cdn.example.com/2020/02/clip.mp4"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """The real app on an isolated temp database / DATA_DIR / OUTPUT_DIR."""
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


def _clip_bytes(tmp_path) -> bytes:
    """A small real mp4, so what the endpoint serves back is a decodable file."""
    dest = tmp_path / "url-clip.mp4"
    cmd = [
        get_settings().ffmpeg_bin, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest.read_bytes()


@pytest.fixture()
def downloads(monkeypatch, tmp_path):
    """Stand in for the fetcher: write the clip where a real download would.

    Records the `require_local` it was called with -- passing False is exactly
    the defect, and a stub that ignored the argument would let it back in.
    """
    from backend.api import projects as projects_api

    calls: list[dict] = []

    async def fake_resolve_url(project_id, url, *, require_local=False):
        calls.append({"url": url, "require_local": require_local})
        cache.ensure_project_dirs(project_id)
        dest = cache.source_dir(project_id) / "video.mp4"
        dest.write_bytes(_clip_bytes(tmp_path))
        return str(dest), VideoInfo(duration=2.0, width=160, height=120, fps=10.0)

    monkeypatch.setattr(projects_api.video, "resolve_url", fake_resolve_url)
    return calls


def _project(client) -> str:
    created = client.post("/api/projects", json={"name": "url project"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_url_ingest_binds_a_local_source(client, downloads):
    pid = _project(client)

    bound = client.post(f"/api/projects/{pid}/url", json={"url": REMOTE_URL})
    assert bound.status_code == 200, bound.text

    # The download is not optional: a remote source the browser cannot play is
    # the whole defect.
    assert downloads == [{"url": REMOTE_URL, "require_local": True}]

    body = bound.json()
    assert body["has_video"] is True
    assert body["video_src"] == f"/api/projects/{pid}/video"
    assert body["fps"] == pytest.approx(10.0)
    # The original URL survives as provenance, but nothing reads it to play.
    assert body["video_url"] == REMOTE_URL
    # Absolute paths never reach the browser.
    assert "video_path" not in body


def test_video_endpoint_serves_bytes_rather_than_a_redirect(client, downloads):
    pid = _project(client)
    assert client.post(f"/api/projects/{pid}/url", json={"url": REMOTE_URL}).status_code == 200

    served = client.get(f"/api/projects/{pid}/video", follow_redirects=False)
    assert served.status_code == 200, served.text
    assert "location" not in {k.lower() for k in served.headers}
    assert served.headers["content-type"].startswith("video/")
    # Real bytes of a real container, not a redirect body.
    assert served.content[4:8] == b"ftyp"


def test_a_row_with_only_a_url_does_not_claim_to_have_video(client):
    """Legacy rows bound before the download existed cannot play, and must say so.

    `has_video` drove the player's decision to render a `<video>` at all; a row
    reporting True while `/video` 404s is how a blank player with no error
    happens.
    """
    pid = _project(client)
    updated = await_sync(db.update_project(pid, video_url=REMOTE_URL, duration=2.0))
    assert updated is not None

    body = client.get(f"/api/projects/{pid}").json()
    assert body["has_video"] is False
    assert body["video_src"] is None
    assert client.get(f"/api/projects/{pid}/video").status_code == 404


def await_sync(coro):
    """Drive one coroutine on the loop the TestClient is not currently using."""
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)
