"""Video source resolution: uploads, direct URLs and yt-dlp page URLs.

Design decision (documented in the README): a *direct* video URL is handed to
ffmpeg as-is — ffmpeg does ranged HTTP reads, so random frame access works
without downloading a copy. A *page* URL (YouTube etc.) is resolved with yt-dlp;
its media URLs are short-lived and often not range-friendly, and section 23 of
the spec prioritises reliable random access over storage, so those are
downloaded to the project directory.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse

from backend.config import get_settings
from backend.services import cache
from backend.services.ffmpeg import FFmpegError, VideoInfo, probe

log = logging.getLogger(__name__)

DIRECT_VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts", ".m3u8", ".mpd",
}
ALLOWED_UPLOAD_EXTS = DIRECT_VIDEO_EXTS | {".flv", ".wmv", ".mpg", ".mpeg", ".ogv"}


class VideoSourceError(RuntimeError):
    pass


def ytdlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


def validate_url(url: str) -> str:
    """Only http(s), and never a literal loopback/link-local/private host.

    Blocks the obvious SSRF shape where a URL field is used to make the backend
    fetch something on its own network.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise VideoSourceError("only http/https URLs are supported")
    if not parsed.hostname:
        raise VideoSourceError("URL has no host")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return parsed.geturl()  # hostname, not a literal IP
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
        raise VideoSourceError("refusing to fetch a private/loopback address")
    return parsed.geturl()


def is_direct_video_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DIRECT_VIDEO_EXTS)


def validate_upload_name(filename: str) -> str:
    safe = cache.sanitize_filename(filename)
    if Path(safe).suffix.lower() not in ALLOWED_UPLOAD_EXTS:
        raise VideoSourceError(f"unsupported video extension: {Path(safe).suffix}")
    return safe


async def resolve_url(
    project_id: str, url: str, *, require_local: bool = False
) -> tuple[str, VideoInfo]:
    """Return `(playable source, info)` for a URL.

    Direct URLs stay remote; page URLs are downloaded via yt-dlp.

    `require_local` forces the download even for a direct URL. The in-process
    VisoMaster backend seeks the source with a local decoder and has no fetcher,
    so a remote source there fails every frame with "project has no local video
    to bind" — a whole run of warnings and not one generated frame.
    """
    url = validate_url(url)

    if is_direct_video_url(url) and not require_local:
        try:
            info = await probe(url)
            return url, info
        except FFmpegError as exc:
            log.info("direct probe failed, falling back to yt-dlp: %s", exc)

    settings = get_settings()
    why = (
        "this backend needs the file on disk"
        if require_local
        else "URL is not a direct video file"
    )
    if not settings.enable_ytdlp:
        raise VideoSourceError(f"{why} and yt-dlp is disabled")
    if not ytdlp_available():
        raise VideoSourceError(f"{why} and yt-dlp is not installed")

    path = await download_with_ytdlp(project_id, url)
    return str(path), await probe(path)


async def download_with_ytdlp(project_id: str, url: str) -> Path:
    """Download to `<project>/source/`. The URL is passed as an argv element,
    never through a shell, so no command injection is possible."""
    cache.ensure_project_dirs(project_id)
    out_dir = cache.source_dir(project_id)
    template = str(out_dir / "video.%(ext)s")

    cmd = [
        "yt-dlp", "--no-playlist", "--no-progress", "--newline",
        "--merge-output-format", "mp4",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "-o", template, "--", url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=3600)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise VideoSourceError("yt-dlp timed out") from None

    if proc.returncode != 0:
        detail = err.decode("utf-8", "replace").strip()[-400:]
        raise VideoSourceError(f"yt-dlp failed: {detail}")

    files = sorted(
        (p for p in out_dir.glob("video.*") if p.suffix != ".part"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise VideoSourceError("yt-dlp produced no output file")
    return files[0]
