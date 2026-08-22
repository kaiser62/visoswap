"""FFmpeg/ffprobe access.

Only single frames are ever decoded: `-ss` before `-i` makes ffmpeg seek to the
nearest keyframe and decode forward, so extraction cost is independent of video
length and nothing is held in RAM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from backend.config import get_settings

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


@dataclass(slots=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    codec: str | None = None


def ffmpeg_available() -> bool:
    s = get_settings()
    return bool(shutil.which(s.ffmpeg_bin) and shutil.which(s.ffprobe_bin))


async def _run(cmd: list[str], timeout: float = 120.0) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FFmpegError(f"timed out after {timeout}s: {cmd[0]}") from None
    return proc.returncode or 0, out, err


async def probe(source: str | Path, timeout: float = 60.0) -> VideoInfo:
    """Read duration/resolution/fps without decoding the video."""
    s = get_settings()
    cmd = [
        s.ffprobe_bin, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(source),
    ]
    code, out, err = await _run(cmd, timeout=timeout)
    if code != 0:
        raise FFmpegError(f"ffprobe failed: {err.decode('utf-8', 'replace')[:400]}")
    try:
        data = json.loads(out or b"{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe returned invalid JSON") from exc

    streams = [st for st in data.get("streams", []) if st.get("codec_type") == "video"]
    if not streams:
        raise FFmpegError("no video stream found")
    st = streams[0]

    duration = _first_float(
        st.get("duration"), data.get("format", {}).get("duration"), default=0.0
    )
    if duration <= 0:
        raise FFmpegError("could not determine video duration")

    return VideoInfo(
        duration=duration,
        width=int(st.get("width") or 0),
        height=int(st.get("height") or 0),
        fps=_parse_fps(st.get("avg_frame_rate") or st.get("r_frame_rate")),
        codec=st.get("codec_name"),
    )


async def extract_frame(
    source: str | Path,
    timestamp: float,
    dest: Path,
    *,
    scale_width: int | None = None,
    quality: int = 90,
    timeout: float = 120.0,
) -> Path:
    """Extract exactly one frame at `timestamp` into `dest`.

    Writes to a temp file first so a crash can never leave a truncated frame
    that later looks like a valid cache hit.
    """
    s = get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    cmd = [
        s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{max(0.0, float(timestamp)):.3f}",
        "-i", str(source),
        "-frames:v", "1",
    ]
    if scale_width:
        # -2 keeps the aspect ratio and an even height for every encoder.
        cmd += ["-vf", f"scale={int(scale_width)}:-2"]
    suffix = dest.suffix.lower()
    if suffix == ".webp":
        cmd += ["-c:v", "libwebp", "-quality", str(quality)]
    elif suffix in (".jpg", ".jpeg"):
        cmd += ["-q:v", "3"]
    # The `.part` suffix hides the real extension, so ffmpeg cannot infer a
    # muxer ("Unable to choose an output format"). State it explicitly.
    cmd += ["-f", "webp" if suffix == ".webp" else "image2"]
    cmd += ["-y", str(tmp)]

    code, _, err = await _run(cmd, timeout=timeout)
    if code != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        detail = err.decode("utf-8", "replace").strip()[:400] or "unknown ffmpeg error"
        raise FFmpegError(f"frame extraction failed at t={timestamp:.3f}: {detail}")

    tmp.replace(dest)
    return dest


def _parse_fps(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_float(*values: object, default: float = 0.0) -> float:
    for v in values:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return default
