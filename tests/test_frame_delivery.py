"""Preview-size frames, batched delivery, and the feature advertisement.

A generated frame is ~93 KB and a tunnelled round trip costs ~330 ms, so playback
is bandwidth- and latency-bound at once. The two levers are smaller frames and
fewer round trips. These tests pin down that neither lever touches the original
bytes, the recording, or the older-backend fallback.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import STATUS_COMPLETED, db
from backend.services import cache, frame_variants


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("VISOSWAP_API_KEY", raising=False)
    get_settings.cache_clear()
    db._conn = None
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _jpeg(width: int = 1280, height: int = 720) -> bytes:
    """A frame-sized JPEG of noise.

    Noise on purpose: a flat image compresses to a few kilobytes, which would make
    every size comparison in this file meaningless.
    """
    rng = np.random.default_rng(7)
    arr = (rng.random((height, width, 3)) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _seed(pid: str, timestamps: list[float], width: int = 1280) -> list[str]:
    """Write JPEGs, queue rows, mark them completed. Returns the keys."""
    cache.ensure_project_dirs(pid)
    keys = []
    for ts in timestamps:
        key = cache.timestamp_key(ts)
        path = cache.generated_dir(pid) / f"{key}.jpg"
        path.write_bytes(_jpeg(width=width))
        keys.append(key)

    async def run():
        await db.enqueue_frames(pid, timestamps, priority=1)
        for ts, key in zip(timestamps, keys):
            await db.set_frame_status(
                pid, ts, STATUS_COMPLETED,
                generated_frame_path=cache.to_relative(
                    cache.generated_dir(pid) / f"{key}.jpg"
                ),
            )

    asyncio.run(run())
    return keys


def _pid(client) -> str:
    return client.post("/api/projects", json={"name": "p", "source": "x.mp4"}).json()["id"]


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


# --------------------------------------------------------------------------- task 1
def test_no_parameters_serves_the_original_bytes(client):
    """Existing clients must be bit-for-bit unaffected."""
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    original = (cache.generated_dir(pid) / f"{key}.jpg").read_bytes()
    r = client.get(f"/api/projects/{pid}/frame/{key}")
    assert r.status_code == 200
    assert r.content == original
    assert r.headers["content-type"].startswith("image/")


def test_a_width_shrinks_the_frame_and_preserves_aspect(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0], width=1280)          # 1280x720
    original = client.get(f"/api/projects/{pid}/frame/{key}").content
    small = client.get(f"/api/projects/{pid}/frame/{key}?w=720&q=70")
    assert small.status_code == 200
    assert small.headers["content-type"] == "image/jpeg"
    width, height = _size(small.content)
    assert width == 720 and height == 405          # 720 * 720/1280
    assert len(small.content) < len(original) / 2, "a 720px preview must be much smaller"


def test_a_width_never_upscales(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0], width=640)
    r = client.get(f"/api/projects/{pid}/frame/{key}?w=1920&q=70")
    assert r.status_code == 200
    assert _size(r.content)[0] == 640, "upscaling adds bytes and no detail"


def test_quality_alone_is_accepted(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    low = client.get(f"/api/projects/{pid}/frame/{key}?q=30").content
    high = client.get(f"/api/projects/{pid}/frame/{key}?q=95").content
    assert len(low) < len(high)


def test_the_variant_is_cached_and_reused(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    first = client.get(f"/api/projects/{pid}/frame/{key}?w=720&q=70")
    assert first.status_code == 200
    path = frame_variants.variant_path(pid, key, 720, 70)
    assert path.is_file()
    mtime = path.stat().st_mtime_ns
    second = client.get(f"/api/projects/{pid}/frame/{key}?w=720&q=70")
    assert second.content == first.content
    assert path.stat().st_mtime_ns == mtime, "the second request re-encoded instead of reusing"


def test_a_variant_is_versioned_like_the_original(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    r = client.get(f"/api/projects/{pid}/frame/{key}?w=720&q=70&v=123")
    assert "immutable" in r.headers["cache-control"]


def test_params_outside_the_clamp_are_422(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    for query in ("w=100", "w=4000", "q=10", "q=99", "w=abc"):
        assert client.get(f"/api/projects/{pid}/frame/{key}?{query}").status_code == 422, query


def test_variants_are_not_written_into_generated_dir(client):
    """The recorder bisects that directory; a variant there would be muxed."""
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    client.get(f"/api/projects/{pid}/frame/{key}?w=720&q=70")
    names = [p.name for p in cache.generated_dir(pid).iterdir()]
    assert names == [f"{key}.jpg"], names
    assert frame_variants.variant_path(pid, key, 720, 70).parent == cache.variant_dir(pid)


def test_a_variant_sorting_next_to_its_original_is_why_it_lives_elsewhere():
    """Documents the hazard directly: this is the bug the separate directory avoids."""
    from bisect import bisect_right
    from pathlib import Path

    names = sorted(["000000.000.jpg", "000000.000.w720q70.jpg", "000001.000.jpg"])
    stems = [Path(n).stem for n in names]
    picked = names[bisect_right(stems, "000000.500") - 1]
    assert picked.endswith(".w720q70.jpg"), "if this ever fails, revisit the separate directory"


def test_clearing_a_project_removes_variants_too(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    client.get(f"/api/projects/{pid}/frame/{key}?w=720&q=70")
    assert frame_variants.variant_path(pid, key, 720, 70).is_file()
    cache.clear_frames(pid)
    assert not frame_variants.variant_path(pid, key, 720, 70).is_file()


def test_delete_variants_drops_every_size_of_one_frame(client):
    pid = _pid(client)
    [key] = _seed(pid, [0.0])
    client.get(f"/api/projects/{pid}/frame/{key}?w=320&q=70")
    client.get(f"/api/projects/{pid}/frame/{key}?w=720&q=70")
    assert frame_variants.delete_variants(pid, key) == 2
    assert not any(cache.variant_dir(pid).iterdir())


def test_clamp_helpers():
    assert frame_variants.clamp(100, 10) == (160, 30)
    assert frame_variants.clamp(4000, 99) == (1920, 95)
    assert frame_variants.clamp(720, 70) == (720, 70)


def test_a_variant_of_a_missing_source_returns_none(client, tmp_path):
    """An unreadable source degrades the request, it does not raise: the caller
    falls back to the original rather than failing playback."""
    pid = _pid(client)
    assert frame_variants.get_or_create(pid, "000000.000", tmp_path / "nope.jpg", 720, 70) is None


def test_an_unknown_frame_key_is_still_a_404(client):
    pid = _pid(client)
    r = client.get(f"/api/projects/{pid}/frame/000999.000?w=720&q=70")
    assert r.status_code == 404


def test_a_malformed_key_is_still_a_400(client):
    pid = _pid(client)
    assert client.get(f"/api/projects/{pid}/frame/not-a-key?w=720&q=70").status_code == 400
