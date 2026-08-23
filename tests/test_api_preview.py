"""One-shot preview endpoint contract tests (plan 05.1-03, D-09).

The preview must render exactly one swapped frame at a requested timestamp
without starting a scheduler, without enqueueing a frame row and without
writing anywhere the recorder composes from. `build_generator` is stubbed, so
every case here tests the endpoint's refusals and its storage discipline, not
the swap itself.
"""

from __future__ import annotations

import io
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import db
from backend.services import cache
from backend.services.generator import GenerationResult
from backend.services.scheduler import registry


def _jpeg(colour: tuple[int, int, int]) -> bytes:
    """Real JPEG bytes: the served response is checked to be an image."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buf, "JPEG")
    return buf.getvalue()


JPEG_ONE = _jpeg((200, 40, 40))
JPEG_TWO = _jpeg((40, 200, 40))


class StubGenerator:
    """Stands in for the engine: records calls, returns fixed JPEG bytes."""

    name = "stub"
    decodes_own_source = True

    def __init__(self) -> None:
        self.bound: str | None = None
        self.calls: list[tuple[float, str]] = []
        self.closed = False
        self.payload = JPEG_ONE

    async def validate(self) -> None:
        return None

    async def bind(self, project: dict) -> None:
        self.bound = project["id"]

    async def generate_at(self, timestamp: float, filename: str) -> GenerationResult:
        self.calls.append((timestamp, filename))
        return GenerationResult(self.payload, None, 0.001, filename)

    async def close(self) -> None:
        self.closed = True


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


@pytest.fixture()
def stub_generator(monkeypatch):
    """Swap build_generator for the stub where the preview router uses it."""
    from backend.api import preview as preview_api

    holder: dict[str, StubGenerator] = {}

    def factory(project, timeout, worker_index=0):
        # One shared instance across requests so a test can reconfigure it
        # between two previews.
        if "stub" not in holder:
            holder["stub"] = StubGenerator()
        return holder["stub"]

    monkeypatch.setattr(preview_api, "build_generator", factory)
    return holder


def _entries(path) -> list[str]:
    """Names inside a cache directory; absent directory reads as empty."""
    return sorted(p.name for p in path.iterdir()) if path.is_dir() else []


def _clip_bytes(tmp_path) -> bytes:
    """A small real mp4 so the source bind's ffprobe succeeds (fps=10)."""
    dest = tmp_path / "start-clip.mp4"
    cmd = [
        get_settings().ffmpeg_bin, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest.read_bytes()


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 10, 200)).save(buf, "PNG")
    return buf.getvalue()


def _ready_project(client, tmp_path) -> str:
    """A project with a bound video (fps=10) and an activated face."""
    created = client.post("/api/projects", json={"name": "preview project"})
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    uploaded = client.post(
        f"/api/projects/{pid}/source",
        files={"file": ("clip.mp4", _clip_bytes(tmp_path), "video/mp4")},
    )
    assert uploaded.status_code == 200, uploaded.text

    face = client.post(
        "/api/faces",
        files={"file": ("face.png", _png_bytes(), "image/png")},
    )
    assert face.status_code == 201, face.text
    activated = client.post(
        f"/api/projects/{pid}/face", json={"face_id": face.json()["face_id"]}
    )
    assert activated.status_code == 200, activated.text
    return pid


# ---------------------------------------------------------------------------
# behaviour block, one test per bullet
# ---------------------------------------------------------------------------


def test_preview_on_an_idle_project_returns_the_envelope_and_serves_the_image(
    client, tmp_path, stub_generator
):
    pid = _ready_project(client, tmp_path)

    resp = client.post(f"/api/projects/{pid}/preview", json={"t": 1.5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == f"/api/projects/{pid}/preview"

    served = client.get(body["url"])
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("image/"), served.headers
    assert served.content == JPEG_ONE


def test_preview_timestamp_is_floored_to_a_real_frame_like_generate_at(
    client, tmp_path, stub_generator
):
    pid = _ready_project(client, tmp_path)

    resp = client.post(f"/api/projects/{pid}/preview", json={"t": 1.57})
    assert resp.status_code == 200, resp.text

    # The clip is 10 fps: int(1.57 * 10) = 15 -> 1.5, exactly the floor
    # EngineFrameGenerator.generate_at applies against media fps.
    assert resp.json()["timestamp"] == pytest.approx(1.5)


def test_a_preview_leaves_no_frame_rows_and_no_scheduler_behind(
    client, tmp_path, stub_generator
):
    pid = _ready_project(client, tmp_path)

    resp = client.post(f"/api/projects/{pid}/preview", json={"t": 1.5})
    assert resp.status_code == 200, resp.text

    # Three separate assertions: no rows were enqueued (read through the
    # frames index)...
    index = client.get(f"/api/projects/{pid}/frames")
    assert index.status_code == 200, index.text
    assert index.json()["frames"] == []
    # ...the registry never saw a scheduler for this project...
    assert registry.peek(pid) is None
    # ...and the generated-frame directory the recorder bisects is untouched.
    assert _entries(cache.generated_dir(pid)) == []


def test_preview_writes_neither_generated_nor_frames_directories(
    client, tmp_path, stub_generator
):
    pid = _ready_project(client, tmp_path)

    resp = client.post(f"/api/projects/{pid}/preview", json={"t": 1.5})
    assert resp.status_code == 200, resp.text

    assert _entries(cache.generated_dir(pid)) == []
    assert _entries(cache.frames_dir(pid)) == []
    # The one permitted location holds exactly the rendered frame.
    assert _entries(cache.preview_dir(pid)) == ["frame.jpg"]


def test_preview_while_a_run_is_active_is_refused_with_409(
    client, tmp_path, stub_generator
):
    pid = _ready_project(client, tmp_path)
    registry._schedulers[pid] = type("R", (), {"running": True})()
    try:
        resp = client.post(f"/api/projects/{pid}/preview", json={"t": 1.5})
        assert resp.status_code == 409, resp.text
    finally:
        registry._schedulers.pop(pid, None)


def test_preview_without_a_source_face_is_400_naming_it(client, tmp_path, stub_generator):
    created = client.post("/api/projects", json={"name": "faceless"})
    pid = created.json()["id"]
    uploaded = client.post(
        f"/api/projects/{pid}/source",
        files={"file": ("clip.mp4", _clip_bytes(tmp_path), "video/mp4")},
    )
    assert uploaded.status_code == 200, uploaded.text

    resp = client.post(f"/api/projects/{pid}/preview", json={"t": 1.5})
    assert resp.status_code == 400, resp.text
    assert "source face" in resp.json()["detail"], resp.text


def test_preview_without_any_video_source_is_400(client, tmp_path, stub_generator):
    created = client.post("/api/projects", json={"name": "empty"})
    pid = created.json()["id"]

    resp = client.post(f"/api/projects/{pid}/preview", json={"t": 1.5})
    assert resp.status_code == 400, resp.text
    assert "video" in resp.json()["detail"], resp.text


def test_second_preview_overwrites_the_first_single_image_remains(
    client, tmp_path, stub_generator
):
    pid = _ready_project(client, tmp_path)

    first = client.post(f"/api/projects/{pid}/preview", json={"t": 0.5})
    assert first.status_code == 200, first.text
    stub_generator["stub"].payload = JPEG_TWO
    second = client.post(f"/api/projects/{pid}/preview", json={"t": 1.2})
    assert second.status_code == 200, second.text

    assert _entries(cache.preview_dir(pid)) == ["frame.jpg"], (
        "a project holds at most one preview image"
    )
    served = client.get(f"/api/projects/{pid}/preview")
    assert served.content == JPEG_TWO


def test_get_before_any_preview_is_404(client, tmp_path, stub_generator):
    pid = _ready_project(client, tmp_path)

    resp = client.get(f"/api/projects/{pid}/preview")
    assert resp.status_code == 404, resp.text
