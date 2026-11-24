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
from backend.services import cache, frame_variants, framepack


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


# --------------------------------------------------------------------------- task 2: pack
def test_a_pack_round_trips_and_offsets_are_absolute_within_the_payload(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0, 1.0, 2.0])
    originals = {k: (cache.generated_dir(pid) / f"{k}.jpg").read_bytes() for k in keys}
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-visoswap-pack"
    header, frames = framepack.parse(r.content)
    assert [e["key"] for e in header["frames"]] == keys
    assert frames == originals
    # offsets are contiguous from zero, in request order
    offset = 0
    for entry in header["frames"]:
        assert entry["offset"] == offset
        offset += entry["length"]


def test_a_pack_can_carry_downscaled_variants_for_every_frame(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0, 1.0], width=1280)
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys, "w": 720, "q": 70})
    assert r.status_code == 200
    _, frames = framepack.parse(r.content)
    assert set(frames) == set(keys)
    for data in frames.values():
        assert _size(data)[0] == 720
        assert data[:2] == b"\xff\xd8"          # JPEG SOI


def test_a_pack_is_smaller_than_the_originals(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0, 1.0, 2.0], width=1280)
    big = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys}).content
    small = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys, "w": 720, "q": 70}).content
    assert len(small) < len(big) / 2


def test_a_missing_key_is_reported_without_failing_the_pack(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0])
    requested = [keys[0], "000999.000"]
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": requested})
    assert r.status_code == 200
    header, frames = framepack.parse(r.content)
    by_key = {e["key"]: e for e in header["frames"]}
    assert by_key[keys[0]]["status"] == 200
    assert by_key["000999.000"]["status"] == 404
    assert by_key["000999.000"]["length"] == 0
    assert set(frames) == {keys[0]}


def test_a_pending_frame_is_reported_as_404_not_served(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0])

    async def pend():
        await db.enqueue_frames(pid, [5.0], priority=1)

    asyncio.run(pend())
    pending_key = cache.timestamp_key(5.0)
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": [keys[0], pending_key]})
    header, _ = framepack.parse(r.content)
    by_key = {e["key"]: e for e in header["frames"]}
    assert by_key[pending_key]["status"] == 404


def test_a_malformed_key_is_reported_as_400_per_frame(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0])
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": [keys[0], "junk"]})
    assert r.status_code == 200
    header, frames = framepack.parse(r.content)
    by_key = {e["key"]: e for e in header["frames"]}
    assert by_key["junk"]["status"] == 400
    assert set(frames) == {keys[0]}


def test_an_empty_key_list_is_422_from_validation(client):
    pid = _pid(client)
    assert client.post(f"/api/projects/{pid}/frames/pack", json={"keys": []}).status_code == 422


def test_too_many_keys_is_refused(client):
    pid = _pid(client)
    keys = [cache.timestamp_key(float(i)) for i in range(framepack.MAX_KEYS + 1)]
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys})
    assert r.status_code == 422, "schema max_length rejects it before the handler"


def test_duplicate_keys_are_refused(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0])
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys * 2})
    assert r.status_code == 400
    assert "unique" in r.json()["detail"]


def test_the_size_cap_drops_the_tail_instead_of_failing(client, monkeypatch):
    pid = _pid(client)
    keys = _seed(pid, [0.0, 1.0, 2.0], width=1280)
    monkeypatch.setattr(framepack, "MAX_BYTES", 1)
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys})
    assert r.status_code == 200, "a size cap must not cost the client a round trip"
    header, frames = framepack.parse(r.content)
    assert frames == {}
    assert [e["status"] for e in header["frames"]] == [413, 413, 413]


def test_the_pack_rejects_an_unknown_project(client):
    r = client.post("/api/projects/00000000000000000000000000000000/frames/pack",
                    json={"keys": ["000000.000"]})
    assert r.status_code == 404


def test_pack_w_and_q_are_validated(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0])
    assert client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys, "w": 10}).status_code == 422
    assert client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys, "q": 5}).status_code == 422


def test_the_header_length_prefix_matches_the_header(client):
    pid = _pid(client)
    keys = _seed(pid, [0.0])
    body = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": keys}).content
    (declared,) = struct.unpack(">I", body[:4])
    parsed = json.loads(body[4 : 4 + declared])
    assert "frames" in parsed
    assert len(body) - 4 - declared == sum(e["length"] for e in parsed["frames"])


def test_a_truncated_pack_is_rejected_by_the_parser():
    good = framepack.build([framepack.PackedFrame("000000.000", 0, 0, 200, b"abc")])
    for bad in (b"", b"\x00", good[:6], good[:-1]):
        with pytest.raises(framepack.PackError):
            framepack.parse(bad)


def test_a_header_claiming_too_much_is_rejected():
    import struct as st

    blob = st.pack(">I", 10**6) + b"{}"
    with pytest.raises(framepack.PackError):
        framepack.parse(blob)


def test_a_pack_of_nothing_but_404s_is_still_a_valid_response(client):
    pid = _pid(client)
    r = client.post(f"/api/projects/{pid}/frames/pack", json={"keys": ["000999.000"]})
    assert r.status_code == 200
    header, frames = framepack.parse(r.content)
    assert frames == {} and header["frames"][0]["status"] == 404
