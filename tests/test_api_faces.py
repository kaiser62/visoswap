"""The machine-global face library: service and API contract tests (plan 05.1-02).

Service-level cases exercise ``backend.services.facestore`` directly against a
``DATA_DIR`` redirected into ``tmp_path`` (the ``tests/test_recorder.py``
idiom); router-level cases drive the real app through FastAPI's ``TestClient``
(the ``tests/test_api_settings.py`` idiom) with an isolated database.
"""

from __future__ import annotations

import io

import pytest

from backend.config import get_settings
from backend.services import facestore


def _png(colour: tuple[int, int, int]) -> bytes:
    """Real PNG bytes: thumbnails decode them, so fixtures must be real too."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buf, "PNG")
    return buf.getvalue()


PNG_BYTES = _png((200, 40, 40))
OTHER_BYTES = _png((40, 200, 40))


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Redirect DATA_DIR at tmp_path and clear the settings cache both sides."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# service level: backend.services.facestore
# ---------------------------------------------------------------------------


def test_face_id_for_is_a_stable_32_hex_content_digest(data_root):
    first = facestore.face_id_for(PNG_BYTES)
    second = facestore.face_id_for(PNG_BYTES)

    assert first == second
    assert len(first) == 32
    assert first == first.lower()
    assert all(c in "0123456789abcdef" for c in first)
    assert facestore.face_id_for(OTHER_BYTES) != first


def test_faces_dir_lives_outside_the_projects_tree(data_root):
    directory = facestore.faces_dir()

    assert directory.is_dir(), "faces_dir must be created on demand"
    assert directory == get_settings().data_dir / "faces"
    assert get_settings().projects_dir not in directory.parents
    assert not directory.is_relative_to(get_settings().projects_dir)


def test_store_writes_digest_named_pair_and_dedupes_identical_bytes(data_root):
    record = facestore.store(PNG_BYTES, "me.png")

    assert record["face_id"] == facestore.face_id_for(PNG_BYTES)
    assert record["display_name"]
    assert record["bytes"] == len(PNG_BYTES)
    assert record["url"] == f"/api/faces/{record['face_id']}/image"
    assert record["thumbnail_url"] == f"/api/faces/{record['face_id']}/thumbnail"

    entries = sorted(p.name for p in facestore.faces_dir().iterdir())
    assert len(entries) == 3, entries
    assert all(name.startswith(record["face_id"]) for name in entries)
    assert any(name.endswith(facestore.THUMB_SUFFIX) for name in entries)
    assert any(name.endswith(facestore.NAME_SUFFIX) for name in entries)
    # Every path segment is the digest; the caller's string is only ever the
    # sidecar's *content*.
    assert "me.png" not in entries
    assert facestore.read_name(record["face_id"]) == "me.png"

    again = facestore.store(PNG_BYTES, "copy.png")
    assert again["face_id"] == record["face_id"]
    assert len(list(facestore.faces_dir().iterdir())) == 3
    # First name wins: a duplicate must not rename an entry already on screen.
    assert again["display_name"] == "me.png"
    assert facestore.read_name(record["face_id"]) == "me.png"


def test_listing_shows_the_upload_name_and_falls_back_to_the_id(data_root):
    record = facestore.store(PNG_BYTES, "aunt_may.png")
    assert [f["display_name"] for f in facestore.list_faces()] == ["aunt_may.png"]

    # A face stored before sidecars existed has no name to show.
    facestore.name_path(record["face_id"]).unlink()
    listed = facestore.list_faces()
    assert [f["display_name"] for f in listed] == [record["face_id"]]
    # The sidecar itself is never a face in its own right.
    assert len(listed) == 1


def test_delete_removes_the_name_sidecar_too(data_root):
    record = facestore.store(PNG_BYTES, "gone.png")
    facestore.delete(record["face_id"])
    assert list(facestore.faces_dir().iterdir()) == []


def test_store_rejects_non_image_suffixes_naming_them(data_root):
    with pytest.raises(ValueError, match="mp4"):
        facestore.store(b"not an image", "clip.mp4")
    with pytest.raises(ValueError, match="extension"):
        facestore.store(b"no name", "")


@pytest.mark.parametrize(
    "bad", ["..", "../../etc/passwd", "a/b", "0" * 31], ids=["dotdot", "traversal", "separator", "short-hex"]
)
def test_face_path_refuses_anything_that_is_not_a_face_id(data_root, bad):
    with pytest.raises(facestore.UnsafeFaceId):
        facestore.face_path(bad)


def test_list_faces_orders_newest_first_and_never_serves_a_thumbnail(data_root):
    facestore.store(PNG_BYTES, "one.png")
    facestore.store(OTHER_BYTES, "two.jpg")

    records = facestore.list_faces()
    ids = [r["face_id"] for r in records]

    assert len(records) == 2
    assert set(ids) == {
        facestore.face_id_for(PNG_BYTES),
        facestore.face_id_for(OTHER_BYTES),
    }
    # The most recent upload sorts first.
    assert ids[0] == facestore.face_id_for(OTHER_BYTES)
    assert all(
        facestore.THUMB_SUFFIX not in r["url"] for r in records
    )
    assert all("display_name" in r and "bytes" in r for r in records)


def test_delete_removes_both_files_and_is_a_noop_on_an_unknown_id(data_root):
    record = facestore.store(PNG_BYTES, "gone.png")
    path = facestore.face_path(record["face_id"])
    thumb = facestore.thumb_path(record["face_id"])
    assert path.is_file() and thumb.is_file()

    facestore.delete(record["face_id"])
    assert not path.exists()
    assert not thumb.exists()

    # An unknown id is silently a no-op, never an error.
    facestore.delete(facestore.face_id_for(b"never-stored"))


# ---------------------------------------------------------------------------
# router level: /api/faces and the project activation endpoint
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.models.database import db  # noqa: E402


def _isolated_client(monkeypatch, tmp_path, extra_env=None):
    """The real app on an isolated temp database and temp DATA_DIR."""
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///{}".format(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    db._conn = None
    app = create_app()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        get_settings.cache_clear()


@pytest.fixture()
def client(monkeypatch, tmp_path):
    yield from _isolated_client(monkeypatch, tmp_path)


@pytest.fixture()
def small_cap_client(monkeypatch, tmp_path):
    """A client whose upload cap is 64 bytes, so the 413 is reachable."""
    yield from _isolated_client(monkeypatch, tmp_path, {"MAX_UPLOAD_BYTES": "64"})


def _upload(client, name="face.png", content=None):
    return client.post(
        "/api/faces",
        files={"file": (name, content if content is not None else PNG_BYTES, "image/png")},
    )


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def test_upload_returns_a_record_and_dedupes_identical_bytes(client):
    first = _upload(client)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["face_id"] == facestore.face_id_for(PNG_BYTES)
    assert body["url"] == f"/api/faces/{body['face_id']}/image"
    assert body["thumbnail_url"] == f"/api/faces/{body['face_id']}/thumbnail"
    assert body["display_name"]

    second = _upload(client, name="same-bytes-again.png")
    assert second.json()["face_id"] == body["face_id"]
    assert len(client.get("/api/faces").json()) == 1


def test_upload_past_the_cap_is_413_and_leaves_no_part(small_cap_client):
    resp = _upload(small_cap_client, content=_png((10, 200, 10)))
    assert resp.status_code == 413, resp.text
    leftovers = [p.name for p in facestore.faces_dir().iterdir()]
    assert not any(name.endswith(".part") for name in leftovers), leftovers


def test_upload_with_a_non_image_suffix_is_400_naming_it(client):
    resp = _upload(client, name="clip.mp4", content=b"not an image at all")
    assert resp.status_code == 400, resp.text
    assert "mp4" in resp.json()["detail"]


def test_traversal_filename_stores_under_its_digest_never_the_given_name(client):
    resp = _upload(client, name="../../evil.png")
    assert resp.status_code == 201, resp.text
    names = [p.name for p in facestore.faces_dir().iterdir()]
    assert not any("evil" in name for name in names), names
    stored = {p.name for p in facestore.faces_dir().iterdir()}
    assert any(name.startswith(resp.json()["face_id"]) for name in stored)


def test_listing_is_newest_first_and_every_thumbnail_resolves_as_an_image(client):
    _upload(client, content=PNG_BYTES)
    _upload(client, name="second.png", content=OTHER_BYTES)

    records = client.get("/api/faces").json()
    assert [r["face_id"] for r in records][0] == facestore.face_id_for(OTHER_BYTES)

    for record in records:
        thumb = client.get(record["thumbnail_url"])
        assert thumb.status_code == 200, record
        assert thumb.headers["content-type"].startswith("image/"), record
        full = client.get(record["url"])
        assert full.status_code == 200, record


def test_activation_binds_the_face_and_returns_one_assignment(client):
    project_id = _create_project(client)
    face_id = _upload(client).json()["face_id"]

    activated = client.post(
        f"/api/projects/{project_id}/face", json={"face_id": face_id}
    )
    assert activated.status_code == 200, activated.text
    payload = activated.json()
    assert list(payload["assignments"][0].keys()) >= [
        "target_index", "face_id", "thumbnail_url",
    ]
    assert len(payload["assignments"]) == 1
    assert payload["assignments"][0]["target_index"] == 0
    assert payload["assignments"][0]["face_id"] == face_id

    # The row points into the store; the payload reports only the id.
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["source_face_id"] == face_id


def test_activation_of_an_unknown_face_is_404(client):
    project_id = _create_project(client)
    resp = client.post(
        f"/api/projects/{project_id}/face", json={"face_id": "0" * 32}
    )
    assert resp.status_code == 404, resp.text


def test_face_and_project_responses_never_leak_absolute_paths(client, tmp_path):
    project_id = _create_project(client)
    face_id = _upload(client).json()["face_id"]
    client.post(f"/api/projects/{project_id}/face", json={"face_id": face_id})

    faces_body = _strings(client.get("/api/faces").json())
    project_body = _strings(client.get(f"/api/projects/{project_id}").json())
    for value in faces_body + project_body:
        assert str(tmp_path) not in value, value


# ---------------------------------------------------------------------------
# warn-and-cascade delete (D-05)
# ---------------------------------------------------------------------------


def _create_project(client, name="faces project") -> str:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _bind(client, project_id: str, content) -> str:
    face_id = _upload(client, content=content).json()["face_id"]
    resp = client.post(f"/api/projects/{project_id}/face", json={"face_id": face_id})
    assert resp.status_code == 200, resp.text
    return face_id


def _clip_bytes(tmp_path) -> bytes:
    """A small real mp4 so the source bind's ffprobe succeeds."""
    import subprocess

    dest = tmp_path / "start-clip.mp4"
    cmd = [
        get_settings().ffmpeg_bin, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest.read_bytes()


@pytest.fixture()
def stubbed_start(monkeypatch):
    """Keep any scheduler start away from the real engine in these tests.

    In RED (no guard yet) the flow reaches ``build_generator`` and the stub's
    validate raises, so the route answers 400 with the wrong detail -- a safe
    failure. In GREEN the no-source-face guard fires before it.
    """
    from backend.api import generation as generation_api

    class _ExplodingGenerator:
        name = "exploding-stub"
        decodes_own_source = True

        async def validate(self):
            raise ValueError("stub generator was reached")

        async def close(self):
            return None

    monkeypatch.setattr(
        generation_api, "build_generator", lambda *a, **k: _ExplodingGenerator()
    )


def test_usage_of_an_unused_or_unknown_face_is_empty(client):
    face_id = _upload(client).json()["face_id"]

    unused = client.get(f"/api/faces/{face_id}/usage")
    assert unused.status_code == 200, unused.text
    assert unused.json() == {"projects": []}

    unknown = client.get(f"/api/faces/{'0' * 32}/usage")
    assert unknown.status_code == 200, unknown.text
    assert unknown.json() == {"projects": []}


def test_usage_names_every_project_whose_source_is_the_face(client):
    project_a = _create_project(client, name="alpha")
    project_b = _create_project(client, name="beta")
    face_id = _bind(client, project_a, PNG_BYTES)
    assert (
        client.post(f"/api/projects/{project_b}/face", json={"face_id": face_id}).status_code
        == 200
    )

    body = client.get(f"/api/faces/{face_id}/usage").json()
    listed = {p["id"]: p["name"] for p in body["projects"]}
    assert listed == {project_a: "alpha", project_b: "beta"}


def test_deleting_an_unused_face_is_204_and_removes_both_files(client):
    record = facestore.store(PNG_BYTES, "unused.png")

    resp = client.delete(f"/api/faces/{record['face_id']}")
    assert resp.status_code == 204, resp.text
    assert not facestore.face_path(record["face_id"]).exists()
    assert not facestore.thumb_path(record["face_id"]).exists()


def test_unforced_delete_of_a_used_face_is_409_and_removes_nothing(client):
    project_a = _create_project(client, name="alpha")
    project_b = _create_project(client, name="beta")
    face_id = _bind(client, project_a, PNG_BYTES)
    client.post(f"/api/projects/{project_b}/face", json={"face_id": face_id})
    stored = facestore.face_path(face_id)
    thumb = facestore.thumb_path(face_id)

    resp = client.delete(f"/api/faces/{face_id}")
    assert resp.status_code == 409, resp.text

    conflict = resp.json()["detail"]
    listed = {p["id"]: p["name"] for p in conflict["projects"]}
    assert listed == {project_a: "alpha", project_b: "beta"}

    # Nothing was removed and no binding changed.
    assert stored.is_file()
    assert thumb.is_file()
    for pid in (project_a, project_b):
        assert client.get(f"/api/projects/{pid}").json()["source_face_id"] == face_id


def test_forced_delete_removes_files_and_clears_both_bindings(client):
    project_a = _create_project(client, name="alpha")
    project_b = _create_project(client, name="beta")
    face_id = _bind(client, project_a, PNG_BYTES)
    client.post(f"/api/projects/{project_b}/face", json={"face_id": face_id})

    resp = client.delete(f"/api/faces/{face_id}?force=true")
    assert resp.status_code == 204, resp.text
    assert not facestore.face_path(face_id).exists()
    assert not facestore.thumb_path(face_id).exists()

    # Each affected project reports the no-source state by id, never a path.
    for pid in (project_a, project_b):
        assert client.get(f"/api/projects/{pid}").json()["source_face_id"] is None


def test_scheduler_start_refuses_a_project_left_with_no_source_face(
    client, tmp_path, monkeypatch, stubbed_start
):
    project_id = _create_project(client, name="faceless")
    uploaded = client.post(
        f"/api/projects/{project_id}/source",
        files={"file": ("clip.mp4", _clip_bytes(tmp_path), "video/mp4")},
    )
    assert uploaded.status_code == 200, uploaded.text

    resp = client.post(f"/api/projects/{project_id}/scheduler/start", json={})
    assert resp.status_code == 400, resp.text
    assert "source face" in resp.json()["detail"], resp.text


def test_deleting_a_project_leaves_the_global_face_library_intact(client):
    """The store is machine-global (D-03): no project owns a face."""
    project_id = _create_project(client, name="mortal")
    face_id = _bind(client, project_id, PNG_BYTES)
    stored = facestore.face_path(face_id)
    thumb = facestore.thumb_path(face_id)

    deleted = client.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 204, deleted.text

    assert stored.is_file()
    assert thumb.is_file()
    remaining = client.get("/api/faces").json()
    assert [r["face_id"] for r in remaining] == [face_id]

