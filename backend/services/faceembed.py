"""Face embeddings for "group by similarity", on CPU, independent of the swap engine.

The face library can already group by *name* (`facestore.auto_group_all`). This
adds identity: two photos of one person under different names should land in one
group. That needs a 512-d embedding per face, computed with the models the repo
already ships -- SCRFD (`det_10g.onnx`) to find the largest face and its five
landmarks, ArcFace (`w600k_r50.onnx`) to embed the aligned crop.

Deliberately **not** the engine's model loader. `visoswap/processors` binds
cuDNN, takes the GPU, and comes with settings that assume it owns the device;
this must run on a Mac, on a Kaggle kernel and on Colab, and must never compete
with a generation run for GPU memory. So: onnxruntime with the CPU provider
only, one shared session behind a lock, loaded on first use.

The embedding is biometric data. It never leaves the face store, never appears in
a `FaceOut`, a log line or an API response -- only similarity *scores* are ever
returned. Sidecars are named after the face id, which is a content digest, so an
embedding cannot go stale for a face that has not changed.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from backend.config import get_settings

log = logging.getLogger(__name__)

#: ArcFace's own input: 112x112 RGB, normalised to [-1, 1].
ARCFACE_SIZE = 112
DIM = 512
#: SCRFD runs at a fixed square input, as in the engine's own detector.
DET_SIZE = 640
#: Detector confidence below this is not a face worth embedding.
DET_SCORE = 0.5
#: ArcFace template for a 112x112 crop: eyes, nose, mouth corners. Copied from
#: `visoswap/processors/models_processor.py` (arcface_dst) rather than imported,
#: so this module never pulls in the engine.
ARCFACE_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

DET_FILE = "det_10g.onnx"
ARC_FILE = "w600k_r50.onnx"

#: Sidecar suffixes live in `facestore` so `list_faces` and `delete` share the
#: one definition -- a second copy here is how a sidecar gets forgotten.
EMB_SUFFIX = ".emb.npy"
NONE_SUFFIX = ".emb.none"


class EmbedderUnavailable(RuntimeError):
    """The models are not on this machine, so no embedding can be computed."""


# --------------------------------------------------------------------------- paths
def emb_path(face_id: str) -> Path:
    """The embedding sidecar path (owned by facestore, so delete cannot miss it)."""
    from backend.services import facestore

    return facestore.emb_path(face_id)


def none_path(face_id: str) -> Path:
    """The "no usable face" marker path."""
    from backend.services import facestore

    return facestore.emb_none_path(face_id)


def has_embedding(face_id: str) -> bool:
    return emb_path(face_id).is_file()


def is_marked_none(face_id: str) -> bool:
    return none_path(face_id).is_file()


def read(face_id: str) -> Optional[np.ndarray]:
    """The stored embedding, or None when absent/unreadable/malformed.

    A truncated or wrong-shaped file is treated as absent rather than raised on:
    one bad sidecar must not break a whole grouping call.
    """
    path = emb_path(face_id)
    if not path.is_file():
        return None
    try:
        vec = np.load(path)
    except (OSError, ValueError) as exc:
        log.warning("[FACEEMB] %s unreadable embedding (%s); treating as absent", face_id, exc)
        return None
    if vec.shape != (DIM,) or not np.isfinite(vec).all():
        log.warning("[FACEEMB] %s has a malformed embedding %s; ignoring", face_id, vec.shape)
        return None
    return vec.astype(np.float32, copy=False)


def write(face_id: str, vec: np.ndarray) -> None:
    """Store one embedding atomically (a half-written sidecar must never load)."""
    vec = np.asarray(vec, dtype=np.float32).reshape(DIM)
    path = emb_path(face_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    # Through a handle: np.save appends ".npy" to a bare filename that lacks it,
    # which would write to `<id>.emb.npy.part.npy` and leave the replace to fail.
    with tmp.open("wb") as handle:
        np.save(handle, vec)
    os.replace(tmp, path)
    none_path(face_id).unlink(missing_ok=True)


def write_none(face_id: str) -> None:
    """Record that this image has no detectable face."""
    path = none_path(face_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    emb_path(face_id).unlink(missing_ok=True)


def delete_sidecars(face_id: str) -> None:
    """Remove the embedding and the marker. Called when a face is deleted."""
    emb_path(face_id).unlink(missing_ok=True)
    none_path(face_id).unlink(missing_ok=True)


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    """Unit-length copy. Cosine similarity on unit vectors is a dot product."""
    vec = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("cannot normalize a zero or non-finite vector")
    return (vec / norm).astype(np.float32)


# --------------------------------------------------------------------------- model access
def models_dir() -> Optional[Path]:
    return get_settings().models_dir


def models_present() -> bool:
    """Both files must exist for an embedding to be possible."""
    root = models_dir()
    if root is None:
        return False
    return (root / DET_FILE).is_file() and (root / ARC_FILE).is_file()


class _Sessions:
    """One shared pair of CPU sessions, built on first use, guarded by a lock.

    Sessions are process-global and expensive to build (~200 MB resident), so
    they are created once and reused. `run` is serialised because an
    onnxruntime session is not documented as thread-safe for concurrent `run`
    on the same object, and the grouping job is the only caller.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._det: Any = None
        self._arc: Any = None

    def _build(self) -> None:
        if self._det is not None:
            return
        root = models_dir()
        if root is None or not models_present():
            raise EmbedderUnavailable(
                "the face models are not installed: need "
                f"{DET_FILE} and {ARC_FILE} in MODELS_DIR"
            )
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        # CPU only, on purpose: this must run on a machine with no GPU, and it
        # must never allocate against a generation run's VRAM.
        self._det = ort.InferenceSession(str(root / DET_FILE), sess_options=opts, providers=["CPUExecutionProvider"])
        self._arc = ort.InferenceSession(str(root / ARC_FILE), sess_options=opts, providers=["CPUExecutionProvider"])
        log.info("[FACEEMB] loaded %s and %s on CPU", DET_FILE, ARC_FILE)

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def det(self) -> Any:
        with self._lock:
            self._build()
            return self._det

    def arc(self) -> Any:
        with self._lock:
            self._build()
            return self._arc

    def reset(self) -> None:
        """Drop the sessions (the memory goes with them). Used by tests and by
        the grouping job once it is done on a memory-constrained box."""
        with self._lock:
            self._det = self._arc = None


SESSIONS = _Sessions()
#: Serialises a full embed across callers: decode + detect + align + embed is a
#: burst of CPU, and two of them at once would just thrash.
_RUN_LOCK = threading.Lock()


# --------------------------------------------------------------------------- geometry
def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    preds = []
    for i in range(0, distance.shape[1], 2):
        preds.append(points[:, 0] + distance[:, i])
        preds.append(points[:, 1] + distance[:, i + 1])
    return np.stack(preds, axis=-1).reshape(-1, 5, 2)


def _nms(dets: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][ovr <= thresh]
    return keep


def _detect(session: Any, img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Largest face: bbox (4,) and 5 landmarks (5,2), in the original image's pixels.

    The image is letterboxed to a 640x640 square with its aspect ratio kept and
    the result divided back out, the same way the engine's detector does it, so
    landmarks land on the original coordinates.
    """
    height, width = img.shape[:2]
    if height == 0 or width == 0:
        return np.zeros(0, np.float32), np.zeros((0, 5, 2), np.float32)
    det_scale = float(DET_SIZE) / max(height, width)
    new_h, new_w = int(round(height * det_scale)), int(round(width * det_scale))
    new_h, new_w = max(1, new_h), max(1, new_w)
    canvas = np.zeros((DET_SIZE, DET_SIZE, 3), dtype=np.float32)
    resized = _resize(img, new_w, new_h)
    canvas[:new_h, :new_w] = resized

    blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)
    # SCRFD in this repo is fed raw 0-255 BGR-like channels, as the engine does.
    blob = (blob - 127.5) / 128.0
    outputs = session.run(None, {session.get_inputs()[0].name: blob})
    fmc = 3
    scores_list, bboxes_list, kpss_list = [], [], []
    for idx, stride in enumerate((8, 16, 32)):
        scores = outputs[idx].reshape(-1)
        bbox_preds = outputs[idx + fmc].reshape(-1, 4) * stride
        kps_preds = outputs[idx + fmc * 2].reshape(-1, 10) * stride
        fh, fw = DET_SIZE // stride, DET_SIZE // stride
        anchors = np.stack(np.mgrid[:fh, :fw][::-1], axis=-1).astype(np.float32).reshape(-1, 2)
        # This SCRFD export puts two anchors at every feature-map location (the
        # head emits 2*80*80 = 12800 rows at stride 8), so each centre is
        # duplicated to line up with the rows. The engine's detector does the
        # same with np.stack([centers]*2); without it the bbox rows cannot be
        # broadcast against the centres at all.
        anchors = (anchors * stride).astype(np.float32)
        anchors = np.stack([anchors] * 2, axis=1).reshape(-1, 2)
        pos = np.where(scores >= DET_SCORE)[0]
        if pos.size == 0:
            continue
        bboxes = _distance2bbox(anchors, bbox_preds)[pos]
        kpss = _distance2kps(anchors, kps_preds)[pos]
        scores_list.append(scores[pos])
        bboxes_list.append(bboxes)
        kpss_list.append(kpss)
    if not scores_list:
        return np.zeros(0, np.float32), np.zeros((0, 5, 2), np.float32)
    scores = np.concatenate(scores_list)
    bboxes = np.concatenate(bboxes_list) / det_scale
    kpss = np.concatenate(kpss_list) / det_scale
    pre = np.hstack((bboxes, scores.reshape(-1, 1))).astype(np.float32)
    order = scores.argsort()[::-1]
    pre = pre[order]
    kpss = kpss[order]
    keep = _nms(pre, 0.4)
    if not keep:
        return np.zeros(0, np.float32), np.zeros((0, 5, 2), np.float32)
    best = keep[0]  # NMS keeps highest-scoring first: the largest confident face
    return pre[best, :4], kpss[best]


def _resize(img: np.ndarray, new_w: int, new_h: int) -> np.ndarray:
    """Bilinear resize without pulling in OpenCV (PIL is already a dependency)."""
    from PIL import Image

    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    return np.asarray(Image.fromarray(img).resize((new_w, new_h), Image.BILINEAR))


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Similarity transform (rotation, uniform scale, translation) src -> dst.

    The standard Umeyama estimate, which is what aligns a detected face onto the
    ArcFace template. Not an affine fit: a similarity keeps the face's shape.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    num, dim = src.shape
    src_mean, dst_mean = src.mean(0), dst.mean(0)
    src_demean, dst_demean = src - src_mean, dst - dst_mean
    cov = (dst_demean.T @ src_demean) / num
    u, s, vt = np.linalg.svd(cov)
    d = np.ones(dim)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[dim - 1] = -1.0
    r = u @ np.diag(d) @ vt
    var_src = (src_demean ** 2).sum() / num
    if var_src <= 1e-12:
        raise ValueError("degenerate landmark set: cannot align")
    scale = float((s * d).sum() / var_src)
    t = dst_mean - scale * (r @ src_mean)
    out = np.eye(dim + 1, dtype=np.float64)
    out[:dim, :dim] = scale * r
    out[:dim, dim] = t
    return out


def _warp(img: np.ndarray, matrix: np.ndarray, size: int) -> np.ndarray:
    """Apply the 2x3 similarity to an image and crop to `size` x `size`."""
    from PIL import Image

    m = matrix[:2, :]
    coeffs = (m[0, 0], m[0, 1], m[0, 2], m[1, 0], m[1, 1], m[1, 2])
    pil = Image.fromarray(img if img.dtype == np.uint8 else np.clip(img, 0, 255).astype(np.uint8))
    # PIL's AFFINE maps output -> input, so the inverse is what is wanted here.
    inv = np.linalg.inv(matrix)
    ic = (inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2])
    return np.asarray(pil.transform((size, size), Image.AFFINE, ic, resample=Image.BILINEAR), dtype=np.uint8)


# --------------------------------------------------------------------------- the embedder
class _HookEmbedder:
    """Test double: `VISOSWAP_FACE_EMBEDDER="module:function"` -> `f(Path) -> ndarray|None`."""

    def __init__(self, fn: Callable[[Path], Any]) -> None:
        self._fn = fn

    def embed(self, path: Path) -> Optional[np.ndarray]:
        out = self._fn(Path(path))
        if out is None:
            return None
        return l2_normalize(np.asarray(out, dtype=np.float32))


def _hook() -> Optional[Callable[[Path], Any]]:
    spec = os.environ.get("VISOSWAP_FACE_EMBEDDER")
    if not spec:
        return None
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn)


class Embedder:
    """CPU SCRFD + ArcFace. `embed` returns an L2-normalised 512-d vector or None."""

    name = "arcface-cpu"

    def embed(self, path: Path) -> Optional[np.ndarray]:
        from PIL import Image

        with _RUN_LOCK:
            try:
                with Image.open(path) as im:
                    img = np.asarray(im.convert("RGB"), dtype=np.uint8)
            except (OSError, ValueError) as exc:
                log.warning("[FACEEMB] cannot decode %s: %s", Path(path).name, exc)
                return None
            det = SESSIONS.det()
            arc = SESSIONS.arc()
            bbox, kps = _detect(det, img)
            if bbox.size == 0 or not np.isfinite(kps).all():
                return None
            try:
                matrix = _umeyama(kps, ARCFACE_DST)
            except (ValueError, np.linalg.LinAlgError):
                return None
            crop = _warp(img, matrix, ARCFACE_SIZE)
            blob = crop.transpose(2, 0, 1)[None].astype(np.float32)
            blob = (blob - 127.5) / 127.5
            out = arc.run(None, {arc.get_inputs()[0].name: blob})[0]
            return l2_normalize(np.asarray(out).reshape(DIM))


def embedder() -> Any:
    """The embedder to use. The test hook wins, so CI needs no model files."""
    fn = _hook()
    if fn is not None:
        return _HookEmbedder(fn)
    if not models_present():
        root = models_dir()
        raise EmbedderUnavailable(
            "the face models are not installed: need "
            f"{DET_FILE} and {ARC_FILE} in MODELS_DIR"
            + (f" (MODELS_DIR is {root})" if root else " (MODELS_DIR is unset)")
        )
    return Embedder()


def embed_face_file(path: Path) -> Optional[np.ndarray]:
    """Embed one image file. None means "no usable face, do not retry"."""
    return embedder().embed(Path(path))


def embed_face_id(face_id: str, *, force: bool = False) -> Optional[np.ndarray]:
    """The embedding for a stored face, computing and caching it on first use.

    Returns None when the image has no detectable face (the `.emb.none` marker
    is written so the decode is not repeated) or when the file is gone.
    """
    from backend.services import facestore

    face_id = facestore._validate(face_id)
    if not force:
        cached = read(face_id)
        if cached is not None:
            return cached
        if is_marked_none(face_id):
            return None
    path = facestore.face_path(face_id)
    if not path.is_file():
        return None
    vec = embed_face_file(path)
    if vec is None:
        write_none(face_id)
        return None
    write(face_id, vec)
    return read(face_id)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two embeddings (they are stored normalised)."""
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
