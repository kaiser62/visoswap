"""Downscaled frame variants: smaller pictures for the same frame.

A generated frame is roughly 93 KB and playback needs 24 fps, which is close to
what a Cloudflare-tunnelled backend can deliver on a phone's connection. A 720px
preview of the same frame is a fraction of the bytes and is indistinguishable on
a phone screen, so the client can ask for one instead of the original.

Variants are generated once, on CPU, and cached beside the project's frames --
in `cache.variant_dir`, deliberately *not* in `generated_dir`, because the
recorder and composer bisect that directory and a variant sorting next to its
original would end up muxed into the recording.

The original is never modified. A request with no `w`/`q` serves the original
bytes exactly as before, so existing clients are unaffected.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from backend.services import cache

log = logging.getLogger(__name__)

#: Requested widths are clamped here; below this is unreadable, above it is a
#: preview larger than any phone needs (the original is always available).
MIN_WIDTH = 160
MAX_WIDTH = 1920
#: JPEG quality clamp. 30 is already ugly; 95 is bigger than the original.
MIN_QUALITY = 30
MAX_QUALITY = 95

DEFAULT_QUALITY = 70


class VariantError(ValueError):
    """A width/quality pair that cannot be served."""


def clamp(width: int, quality: int) -> Tuple[int, int]:
    return (
        max(MIN_WIDTH, min(MAX_WIDTH, int(width))),
        max(MIN_QUALITY, min(MAX_QUALITY, int(quality))),
    )


def variant_name(key: str, width: int, quality: int) -> str:
    """`000123.450.w720q70.jpg`. The key is inside the name, so a variant can
    never collide with another frame's."""
    return f"{key}.w{width}q{quality}.jpg"


def variant_path(project_id: str, key: str, width: int, quality: int) -> Path:
    return cache.variant_dir(project_id) / variant_name(key, width, quality)


def _render(source: Path, width: int, quality: int) -> bytes:
    """Decode, downscale to `width` (never up), re-encode as JPEG."""
    from PIL import Image

    with Image.open(source) as im:
        im.load()
        target = min(width, im.width)          # never upscale: it adds bytes, not detail
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if target != im.width:
            height = max(1, round(im.height * target / im.width))
            im = im.resize((target, height), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def get_or_create(
    project_id: str, key: str, source: Path, width: int, quality: int
) -> Optional[Path]:
    """The cached variant, generating it once. None if the source is unreadable.

    Written through a temp file and renamed, so a concurrent reader can never
    see a half-encoded JPEG -- `FileResponse` would happily stream one.
    """
    path = variant_path(project_id, key, width, quality)
    if path.is_file() and path.stat().st_size > 0:
        return path
    try:
        data = _render(source, width, quality)
    except (OSError, ValueError) as exc:
        log.warning("[VARIANT] project=%s key=%s could not render: %s", project_id, key, exc)
        return None
    if not data:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid in the temp name: two workers rendering the same variant must not
    # write the same temp file and interleave their bytes.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def delete_variants(project_id: str, key: str) -> int:
    """Remove every cached size of one frame (it was regenerated or deleted)."""
    base = cache.variant_dir(project_id)
    if not base.is_dir():
        return 0
    removed = 0
    for path in base.glob(f"{key}.w*q*.jpg"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed
