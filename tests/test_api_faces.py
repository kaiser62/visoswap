"""The machine-global face library: service and API contract tests (plan 05.1-02).

Service-level cases exercise ``backend.services.facestore`` directly against a
``DATA_DIR`` redirected into ``tmp_path`` (the ``tests/test_recorder.py``
idiom); router-level cases drive the real app through FastAPI's ``TestClient``
(the ``tests/test_api_settings.py`` idiom) with an isolated database.
"""

from __future__ import annotations

import pytest

from backend.config import get_settings
from backend.services import facestore


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
OTHER_BYTES = b"\x89PNG\r\n\x1a\ndifferent-image"


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
    assert len(entries) == 2, entries
    assert any(name.startswith(record["face_id"]) for name in entries)
    assert any(name.endswith(facestore.THUMB_SUFFIX) for name in entries)
    # The stored name is derived, never the caller's string.
    assert "me.png" not in entries

    again = facestore.store(PNG_BYTES, "copy.png")
    assert again["face_id"] == record["face_id"]
    assert len(list(facestore.faces_dir().iterdir())) == 2


def test_store_rejects_non_image_suffixes_naming_them(data_root):
    with pytest.raises(ValueError, match="mp4"):
        facestore.store(b"not an image", "clip.mp4")
    with pytest.raises(ValueError, match="extension"):
        facestore.store(b"no name", "")


def test_face_path_refuses_anything_that_is_not_a_face_id(data_root):
    for bad in ("..", "../../etc/passwd", "a/b", "0" * 31):
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
