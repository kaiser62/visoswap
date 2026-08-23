"""Filesystem layout, path derivation and path-safety helpers.

Every path the API can reach is derived from a validated project id plus a
numeric timestamp. User-supplied strings never become path segments.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from backend.config import get_settings

PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_EXT_BY_FORMAT = {"webp": ".webp", "png": ".png", "jpeg": ".jpg"}


class UnsafePathError(ValueError):
    """Raised when a requested path would escape the project data directory."""


def validate_project_id(project_id: str) -> str:
    """Project ids are uuid4 hex. Anything else never touches the filesystem."""
    if not PROJECT_ID_RE.match(project_id or ""):
        raise UnsafePathError("invalid project id")
    return project_id


def sanitize_filename(name: str, fallback: str = "video") -> str:
    """Reduce an uploaded filename to a safe basename plus extension."""
    name = unicodedata.normalize("NFKD", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")[:80]
    ext = re.sub(r"[^A-Za-z0-9]", "", ext).lower()[:8]
    if not stem:
        stem = fallback
    return f"{stem}.{ext}" if ext else stem


def timestamp_key(ts: float) -> str:
    """Canonical millisecond-resolution key: 123.45 -> '000123.450'."""
    return f"{round(float(ts), 3):010.3f}"


def key_to_timestamp(key: str) -> float:
    return float(key)


def project_dir(project_id: str) -> Path:
    return get_settings().projects_dir / validate_project_id(project_id)


def source_dir(project_id: str) -> Path:
    return project_dir(project_id) / "source"


def frames_dir(project_id: str) -> Path:
    return project_dir(project_id) / "frames"


def generated_dir(project_id: str) -> Path:
    return project_dir(project_id) / "generated"


def preview_dir(project_id: str) -> Path:
    """One-shot preview frames live here, deliberately separate from
    `generated_dir`: `Recorder._generated_for` bisects the sorted listing of
    `generated_dir` to pick which generated frame covers a timestamp, so
    anything written there is a candidate for muxing into the recording. A
    preview is not part of the video."""
    return project_dir(project_id) / "preview"


def ensure_project_dirs(project_id: str) -> Path:
    root = project_dir(project_id)
    for sub in ("source", "frames", "generated", "preview"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def clear_frames(project_id: str) -> int:
    """Delete every extracted and generated image for a project.

    Generation is not cached across runs: a run may use a different face,
    model or resolution, so an old image at the right timestamp is still the
    wrong image. Only the source video survives.
    """
    removed = 0
    for base in (frames_dir(project_id), generated_dir(project_id)):
        if not base.is_dir():
            continue
        for path in base.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
    ensure_project_dirs(project_id)
    return removed


def frame_path(project_id: str, ts: float, *, generated: bool, fmt: str) -> Path:
    base = generated_dir(project_id) if generated else frames_dir(project_id)
    ext = _EXT_BY_FORMAT.get(fmt, ".webp")
    return base / f"{timestamp_key(ts)}{ext}"


def assert_within_project(path: Path, project_id: str) -> Path:
    """Guard against traversal for any path that came from the database."""
    root = project_dir(project_id).resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise UnsafePathError("path escapes project directory")
    return resolved


def to_relative(path: Path | str) -> str:
    """Store paths relative to DATA_DIR so the cache survives relocation."""
    p = Path(path)
    data_dir = get_settings().data_dir
    try:
        return p.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def to_absolute(relative: str) -> Path:
    p = Path(relative)
    return p if p.is_absolute() else (get_settings().data_dir / p)


def dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
