"""Face-embedding service (task 1 of the similarity plan).

Unit tests use the `VISOSWAP_FACE_EMBEDDER="module:func"` hook so no model files
are needed here. One real-model test at the bottom is skipped unless both ONNX
files are present -- see `tests/test_face_similarity_model.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from backend.config import get_settings
from backend.services import faceembed, facestore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """An empty face store and the fake embedder, per test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    (tmp_path / "faces").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "faces"
    get_settings.cache_clear()


def _store_face_bytes(store: Path, data: bytes, name: str = "x.png") -> str:
    """Put bytes into the store the way an upload would, without the HTTP layer."""
    from PIL import Image

    face_id = facestore.face_id_for(data)
    path = store / f"{face_id}{facestore.image_suffix(name)}"
    path.write_bytes(data)
    return face_id


def _png(color=(200, 180, 160)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- hook and cache
def test_the_hook_embedder_is_used_and_returns_a_unit_vector(store, monkeypatch):
    seen = []

    def fake(path):
        seen.append(Path(path).name)
        return np.arange(512, dtype=np.float32)

    monkeypatch.setattr(faceembed, "_hook", lambda: fake)
    face_id = _store_face_bytes(store, _png())
    vec = faceembed.embed_face_id(face_id)
    assert vec is not None and vec.shape == (512,)
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5
    assert seen == [Path(facestore.face_path(face_id)).name]


def test_an_embedding_is_computed_once_then_read_from_the_sidecar(store, monkeypatch):
    calls = []

    def fake(path):
        calls.append(path)
        return np.ones(512, dtype=np.float32)

    monkeypatch.setattr(faceembed, "_hook", lambda: fake)
    face_id = _store_face_bytes(store, _png())
    faceembed.embed_face_id(face_id)
    faceembed.embed_face_id(face_id)
    faceembed.embed_face_id(face_id)
    assert len(calls) == 1
    assert faceembed.has_embedding(face_id)
    assert faceembed.emb_path(face_id).is_file()


def test_a_face_without_a_detectable_face_is_marked_and_not_retried(store, monkeypatch):
    calls = []

    def fake(path):
        calls.append(path)
        return None

    monkeypatch.setattr(faceembed, "_hook", lambda: fake)
    face_id = _store_face_bytes(store, _png())
    assert faceembed.embed_face_id(face_id) is None
    assert faceembed.is_marked_none(face_id)
    assert not faceembed.has_embedding(face_id)
    assert faceembed.embed_face_id(face_id) is None
    assert len(calls) == 1, "a marked-none face must be skipped, not decoded again"


def test_force_recomputes_even_a_marked_none_face(store, monkeypatch):
    calls = []
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: (calls.append(p), None)[1]))
    face_id = _store_face_bytes(store, _png())
    faceembed.embed_face_id(face_id)
    faceembed.embed_face_id(face_id, force=True)
    assert len(calls) == 2


def test_a_malformed_sidecar_is_treated_as_absent_not_fatal(store, monkeypatch):
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: np.ones(512, np.float32)))
    face_id = _store_face_bytes(store, _png())
    faceembed.emb_path(face_id).write_bytes(b"not a numpy file")
    assert faceembed.read(face_id) is None
    vec = faceembed.embed_face_id(face_id)          # recomputed, not raised on
    assert vec is not None and abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5


def test_a_wrong_shape_sidecar_is_ignored(store):
    face_id = _store_face_bytes(store, _png())
    np.save(faceembed.emb_path(face_id), np.ones(128, dtype=np.float32))
    assert faceembed.read(face_id) is None


def test_write_none_clears_an_earlier_embedding_and_vice_versa(store):
    face_id = _store_face_bytes(store, _png())
    faceembed.write(face_id, np.ones(512, np.float32))
    assert faceembed.has_embedding(face_id) and not faceembed.is_marked_none(face_id)
    faceembed.write_none(face_id)
    assert faceembed.is_marked_none(face_id) and not faceembed.has_embedding(face_id)
    faceembed.write(face_id, np.ones(512, np.float32))
    assert faceembed.has_embedding(face_id) and not faceembed.is_marked_none(face_id)


def test_embeddings_are_stored_normalised_and_deterministic(store, monkeypatch):
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: np.linspace(-2, 2, 512, dtype=np.float32)))
    face_id = _store_face_bytes(store, _png())
    a = faceembed.embed_face_id(face_id)
    b = faceembed.read(face_id)
    assert np.allclose(a, b) and abs(float(np.linalg.norm(a)) - 1.0) < 1e-5


def test_a_face_id_that_is_not_hex_is_refused(store):
    with pytest.raises(facestore.UnsafeFaceId):
        faceembed.emb_path("../../etc/passwd")


def test_a_missing_face_file_yields_no_embedding(store, monkeypatch):
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: np.ones(512, np.float32)))
    assert faceembed.embed_face_id("a" * 32) is None


def test_embedder_unavailable_is_raised_without_models_or_a_hook(store, monkeypatch):
    monkeypatch.delenv("VISOSWAP_FACE_EMBEDDER", raising=False)
    monkeypatch.setattr(faceembed, "models_present", lambda: False)
    monkeypatch.setattr(faceembed, "_hook", lambda: None)
    with pytest.raises(faceembed.EmbedderUnavailable) as err:
        faceembed.embedder()
    assert "MODELS_DIR" in str(err.value)


# --------------------------------------------------------------------------- facestore wiring
def test_list_faces_does_not_list_sidecars_as_faces(store, monkeypatch):
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: np.ones(512, np.float32)))
    face_id = _store_face_bytes(store, _png())
    faceembed.embed_face_id(face_id)                       # writes .emb.npy
    ids = {f["face_id"] for f in facestore.list_faces()}
    assert ids == {face_id}


def test_list_faces_ignores_the_none_marker_too(store, monkeypatch):
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: None))
    face_id = _store_face_bytes(store, _png())
    faceembed.embed_face_id(face_id)                       # writes .emb.none
    assert {f["face_id"] for f in facestore.list_faces()} == {face_id}


def test_delete_removes_both_embedding_sidecars(store, monkeypatch):
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: np.ones(512, np.float32)))
    keep = _store_face_bytes(store, _png((200, 180, 160)))
    drop = _store_face_bytes(store, _png((10, 20, 30)))
    faceembed.embed_face_id(drop)
    assert faceembed.has_embedding(drop)
    facestore.delete(drop)
    assert not faceembed.emb_path(drop).exists()
    assert not faceembed.none_path(drop).exists()
    assert not facestore.face_path(drop).exists()
    # a neighbouring face is untouched
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: np.ones(512, np.float32)))
    faceembed.embed_face_id(keep)
    assert faceembed.has_embedding(keep)


def test_delete_removes_a_none_marker(store, monkeypatch):
    monkeypatch.setattr(faceembed, "_hook", lambda: (lambda p: None))
    face_id = _store_face_bytes(store, _png())
    faceembed.embed_face_id(face_id)
    assert faceembed.is_marked_none(face_id)
    facestore.delete(face_id)
    assert not faceembed.none_path(face_id).exists()


def test_cosine_is_one_for_identical_and_lower_for_different():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.6, 0.8], dtype=np.float32)
    assert faceembed.cosine(a, a) == pytest.approx(1.0)
    assert faceembed.cosine(a, b) == pytest.approx(0.6)
    assert faceembed.cosine(a, -a) == pytest.approx(-1.0)
    assert faceembed.cosine(np.zeros(2, np.float32), a) == 0.0


def test_l2_normalize_refuses_a_zero_vector():
    with pytest.raises(ValueError):
        faceembed.l2_normalize(np.zeros(512, dtype=np.float32))


def test_the_embedder_never_binds_the_swap_engine():
    """CPU-only and engine-free is the requirement, not an implementation detail:
    this runs on machines where the engine's CUDA path does not exist at all."""
    src = Path(faceembed.__file__).read_text()
    assert "import torch" not in src and "visoswap.processors" not in src.split('"""', 2)[2]
    assert 'providers=["CPUExecutionProvider"]' in src
