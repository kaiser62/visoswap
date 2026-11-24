"""One real-model test for the embedding service.

Skipped unless both ONNX files are in MODELS_DIR. It proves the plumbing -- the
detector finds a face, the aligner produces a crop, ArcFace returns 512 dims, the
vector is normalised, and a re-encoded copy of the same image lands close to it --
and nothing about identity accuracy.

The input is `skimage.data.astronaut()`: a NASA photograph of Eileen Collins
(public domain, "no known copyright"), bundled with scikit-image. Two reasons it
is not a face drawn with PIL: SCRFD does not detect a cartoon well enough to be
worth the false confidence, and no image file is committed here. No real face is
stored in this repository and no biometric record is added to it.

What this does NOT establish: that two different people land below the threshold,
or that one person under different lighting lands above it. That needs a labelled
set of real faces (plan work split item 4), which the owner has to supply or
approve. The 0.40 default is therefore still unvalidated, and the report says so.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.config import get_settings
from backend.services import faceembed

pytestmark = pytest.mark.skipif(
    not faceembed.models_present(),
    reason="det_10g.onnx / w600k_r50.onnx are not in MODELS_DIR",
)


@pytest.fixture()
def real_models(monkeypatch, tmp_path):
    """Settings pointed at the real models and an empty store."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VISOSWAP_FACE_EMBEDDER", raising=False)
    get_settings.cache_clear()
    (tmp_path / "faces").mkdir(parents=True, exist_ok=True)
    faceembed.SESSIONS.reset()
    yield tmp_path
    faceembed.SESSIONS.reset()
    get_settings.cache_clear()


def _face_like(path: Path, brightness: float = 1.0, scale: float = 1.0) -> Path:
    """The bundled NASA portrait, optionally resized and brightened.

    Returns None-equivalent (raises) if scikit-image does not carry it, which
    keeps this a model test rather than an image-asset test.
    """
    from PIL import Image, ImageEnhance

    from skimage import data as skdata

    im = Image.fromarray(skdata.astronaut())
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.BICUBIC)
    if brightness != 1.0:
        im = ImageEnhance.Brightness(im).enhance(brightness)
    im.save(path)
    return path


def _noise(path: Path, seed: int = 0) -> Path:
    from PIL import Image

    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((320, 320, 3)) * 255).astype(np.uint8)).save(path)
    return path


def test_a_face_like_image_embeds_and_survives_a_resize_and_brightness_change(real_models):
    base = _face_like(real_models / "a.png")
    v1 = faceembed.embed_face_file(base)
    assert v1 is not None, "the detector found no face in the synthetic image"
    assert v1.shape == (512,) and v1.dtype == np.float32
    assert abs(float(np.linalg.norm(v1)) - 1.0) < 1e-5

    same = _face_like(real_models / "b.png", brightness=1.12, scale=0.8)
    v2 = faceembed.embed_face_file(same)
    assert v2 is not None
    cos = faceembed.cosine(v1, v2)
    assert cos > 0.9, f"a slightly altered copy scored {cos:.3f}; the pipeline is not stable"

    # Deterministic: the same file twice is the same vector bit for bit.
    again = faceembed.embed_face_file(base)
    assert again is not None and np.array_equal(v1, again)


def test_noise_yields_no_embedding(real_models):
    assert faceembed.embed_face_file(_noise(real_models / "n.png")) is None


def test_the_detector_returns_landmarks_inside_the_image(real_models):
    from PIL import Image

    img = np.asarray(Image.open(_face_like(real_models / "c.png")).convert("RGB"), dtype=np.uint8)
    bbox, kps = faceembed._detect(faceembed.SESSIONS.det(), img)
    assert bbox.size == 4 and kps.shape == (5, 2)
    assert np.all(kps >= 0) and np.all(kps < np.array([img.shape[1], img.shape[0]]))
    # eyes above mouth: a sanity check on the template, not on identity
    assert kps[0][1] < kps[3][1] and kps[1][1] < kps[4][1]


def test_the_sessions_are_cpu_only(real_models):
    faceembed.SESSIONS.det()
    faceembed.SESSIONS.arc()
    assert faceembed.SESSIONS._det.get_providers() == ["CPUExecutionProvider"]
    assert faceembed.SESSIONS._arc.get_providers() == ["CPUExecutionProvider"]


@pytest.mark.skipif(True, reason="needs a labelled set of real faces the owner must supply (plan item 4)")
def test_identity_threshold_is_validated():
    """Deliberately not written: precision/recall at 0.30/0.40/0.50 needs real
    labelled data. See the module docstring and the report."""
