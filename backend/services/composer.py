"""Offline compose: every generated frame, muxed into one mp4 with its audio.

The `Recorder` composes *live*. It trails the generation watermark and commits
whatever exists as each frame's deadline passes, so a frame that arrives late is
dropped from the take. That is the right trade for a file you can scrub while
the run is still going, and the wrong one for the artefact you keep.

This module is the second pass. It runs after a run has stopped, when nothing is
being generated any more and the frame directory is final, and it re-decodes the
source substituting *every* generated frame that exists. Nothing races it, so
nothing is dropped.

It covers the **generated span** only -- from the first generated frame to one
grid step past the last -- not the whole source. Composing a ten-second swap
into a ninety-minute source would spend an hour re-encoding footage nobody
touched. The audio is cut to the same span from the same offset, so the result
stays in sync with itself rather than starting from the top of the track.

`CLAUDE.md` invariant 5 names `recorder` as the only component permitted a full
decode and re-encode; this is that module's offline half and shares its
substitution rule (nearest *previous* generated frame, invariant 2) and its
decode helper, deliberately by import rather than by copy.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend.config import get_settings
from backend.services import cache, ffmpeg
from backend.services.recorder import (
    BYTES_PER_PIXEL,
    READ_TIMEOUT,
    _decode_jpeg,
    _free_path,
)

log = logging.getLogger(__name__)


class ComposeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Span:
    """The stretch of source a compose covers, and the frames driving it.

    `end` is one grid step past the last generated frame, because under the
    nearest-previous rule that frame covers the gap after itself exactly as
    every other frame covers the gap after it. Stopping at `last` would drop the
    final frame's own span, which for a 5s interval is five seconds of swap.
    """

    start: float
    end: float
    #: Sorted `(stems, filenames)` of the generated frames inside the span.
    stems: list[str]
    names: list[str]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def generated_index(project_id: str) -> tuple[list[str], list[str]]:
    """Sorted `(stems, filenames)` of what has been generated, `.part` excluded.

    Snapshotted once per compose rather than refreshed as the pass runs: this
    only ever runs when generation has stopped, so a re-listing could not turn
    up anything new and would cost a directory walk per source frame.
    """
    base = cache.generated_dir(project_id)
    if not base.is_dir():
        return [], []
    names = sorted(
        p.name for p in base.iterdir()
        if p.is_file() and not p.name.endswith(".part")
    )
    return [Path(n).stem for n in names], names


def span_for(
    project_id: str, *, fps: float, duration: float | None = None
) -> Span | None:
    """What a compose of this project would cover, or None if nothing to do.

    The trailing step is the *median* gap between consecutive generated frames,
    not the mean and not the last gap: a run that was stopped mid-flight leaves
    one ragged interval at the end, and a mean would let that one outlier
    stretch the tail of every take.
    """
    stems, names = generated_index(project_id)
    if not stems:
        return None

    times = [float(stem) for stem in stems]
    start = times[0]
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    step = statistics.median(gaps) if gaps else (1.0 / fps if fps > 0 else 0.0)
    end = times[-1] + max(step, 1.0 / fps if fps > 0 else 0.0)
    if duration is not None and duration > 0:
        end = min(end, float(duration))
    if end <= start:
        # A single generated frame at the very last timestamp of the source.
        # One frame is still a take; give it one frame's worth of span.
        end = start + (1.0 / fps if fps > 0 else 0.0)
    return Span(start=start, end=end, stems=stems, names=names)


def export_stem(name: str, face: str) -> str:
    """`project_datetime_face_composed`, matching the recorder's take names.

    Same shape as `Recorder._stem` on purpose -- these land in the same folder
    and are read by the same person -- with the suffix that says which pass
    produced it, so a composed take is never mistaken for the live recording of
    the same run.
    """
    def part(value: str) -> str:
        cleaned = cache.sanitize_filename(value, "").rsplit(".", 1)[0]
        return cleaned.strip("._")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pieces = [part(name), stamp, part(face)]
    return "_".join(p for p in pieces if p) or stamp


class ComposePass:
    """One offline compose. Constructed per job, never reused.

    Cancellation is cooperative *and* forceful: awaiting the pump raises
    `CancelledError` in `run`, and the `finally` kills both ffmpeg processes so
    a decoder blocked on a full pipe cannot outlive the job that owns it.
    """

    def __init__(
        self,
        project_id: str,
        source: Path,
        span: Span,
        *,
        width: int,
        height: int,
        fps: float,
        name: str = "",
        face: str = "",
    ) -> None:
        self.project_id = project_id
        self.source = Path(source)
        self.span = span
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.name = name
        self.face = face
        self.frame_bytes = self.width * self.height * BYTES_PER_PIXEL
        self.total_frames = max(1, round(self.span.duration * self.fps))
        self.frames_written = 0
        self.frames_substituted = 0
        self.output: Path | None = None

        self._decoder: asyncio.subprocess.Process | None = None
        self._encoder: asyncio.subprocess.Process | None = None
        self._held_key: str | None = None
        self._held: bytes | None = None

        if self.frame_bytes <= 0:
            raise ComposeError("source has no usable dimensions")
        if self.fps <= 0:
            raise ComposeError("source has no usable frame rate")

    # -- the pass -------------------------------------------------------------

    async def run(self) -> Path:
        """Compose the span and return the finished file in the output folder."""
        s = get_settings()
        cache.project_dir(self.project_id).mkdir(parents=True, exist_ok=True)
        working = cache.project_dir(self.project_id) / "compose.mp4.part"
        working.unlink(missing_ok=True)

        start = f"{self.span.start:.6f}"
        length = f"{self.span.duration:.6f}"

        try:
            self._decoder = await asyncio.create_subprocess_exec(
                s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin",
                # Input seek: ffmpeg lands on the keyframe before `start` and
                # discards up to it, which is both fast and frame-accurate for
                # the output. Seeking after `-i` would decode the whole prefix.
                "-ss", start, "-t", length,
                "-i", str(self.source),
                "-map", "0:v:0",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._encoder = await asyncio.create_subprocess_exec(
                s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin",
                # 0: the composed video, fed frame by frame over the pipe.
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{self.width}x{self.height}",
                "-r", f"{self.fps:.6f}",
                "-i", "pipe:0",
                # 1: the same source again, audio only, cut to the same offset
                # and length as the video. Without the matching `-ss` the take
                # would carry the audio from the top of the track against video
                # from the middle of it.
                "-ss", start, "-t", length,
                "-vn", "-i", str(self.source),
                "-map", "0:v:0",
                # `?` keeps a silent source from failing the whole compose.
                "-map", "1:a:0?",
                "-c:v", "libx264", "-preset", s.recorder_preset,
                "-crf", str(s.recorder_crf), "-pix_fmt", "yuv420p",
                # Re-encoded rather than copied: a stream copy starts at the
                # first audio packet at or before `-ss`, which puts the track up
                # to a packet out of step with a video stream that is exact.
                "-c:a", "aac", "-b:a", "192k",
                "-max_muxing_queue_size", "4096",
                # This file is written once and read later, so it gets a normal
                # moov atom up front. The crash-safe fragmented layout the live
                # recorder needs costs bitrate and buys nothing here: a compose
                # that dies leaves a file that is deleted, not served.
                "-movflags", "+faststart",
                "-shortest",
                "-f", "mp4",
                "-y", str(working),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            stderr_drain = asyncio.create_task(
                self._drain_stderr(), name=f"compose-stderr:{self.project_id}"
            )
            try:
                await self._pump()
            finally:
                stderr_drain.cancel()
                await asyncio.gather(stderr_drain, return_exceptions=True)

            await self._finish_encoder()
            if not working.is_file() or working.stat().st_size == 0:
                raise ComposeError("ffmpeg produced no output")
            self.output = self._publish(working)
            log.info(
                "[COMPOSE] project=%s wrote=%s frames=%d substituted=%d "
                "span=%.3f-%.3f",
                self.project_id, self.output.name, self.frames_written,
                self.frames_substituted, self.span.start, self.span.end,
            )
            return self.output
        finally:
            await self._teardown(working)

    async def _pump(self) -> None:
        assert self._decoder and self._decoder.stdout
        assert self._encoder and self._encoder.stdin
        stdout = self._decoder.stdout
        stdin = self._encoder.stdin

        index = 0
        while True:
            try:
                raw = await asyncio.wait_for(
                    stdout.readexactly(self.frame_bytes), timeout=READ_TIMEOUT
                )
            except asyncio.IncompleteReadError:
                return  # End of the span: ffmpeg's `-t` ran out.
            except asyncio.TimeoutError:
                raise ComposeError("decoder stalled") from None

            t = self.span.start + index / self.fps
            frame = await self._compose(t, raw)
            try:
                stdin.write(frame)
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                raise ComposeError("encoder closed early") from None

            self.frames_written += 1
            index += 1

    async def _compose(self, t: float, source_frame: bytes) -> bytes:
        """Generated frame covering `t` if there is one, else the source frame."""
        path = self._generated_for(t)
        if path is None:
            return source_frame
        key = path.name
        if key != self._held_key:
            try:
                self._held = await asyncio.to_thread(
                    _decode_jpeg, path, (self.width, self.height)
                )
            except Exception as exc:
                log.warning(
                    "[COMPOSE] project=%s t=%.3f generated frame unreadable: %s",
                    self.project_id, t, exc,
                )
                self._held = None
            self._held_key = key
        if self._held is None or len(self._held) != self.frame_bytes:
            return source_frame
        self.frames_substituted += 1
        return self._held

    def _generated_for(self, t: float) -> Path | None:
        """Nearest *previous* generated frame — invariant 2, never a future one."""
        stems = self.span.stems
        if not stems:
            return None
        # Bisect the stem, never the filename: `000000.000.jpg` sorts after the
        # bare key, so probing with the raw name drops every timestamp that
        # lands exactly on a generated frame.
        pos = bisect_right(stems, cache.timestamp_key(t))
        if pos == 0:
            return None
        return cache.generated_dir(self.project_id) / self.span.names[pos - 1]

    # -- teardown -------------------------------------------------------------

    async def _drain_stderr(self) -> None:
        assert self._encoder and self._encoder.stderr
        try:
            async for line in self._encoder.stderr:
                text = line.decode("utf-8", "replace").strip()
                if text:
                    log.warning(
                        "[COMPOSE] project=%s ffmpeg: %s",
                        self.project_id, text[:300],
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _finish_encoder(self) -> None:
        """Close the pipe and wait for the muxer to write its trailer."""
        assert self._encoder
        if self._encoder.stdin is not None:
            try:
                self._encoder.stdin.close()
                await self._encoder.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            await asyncio.wait_for(self._encoder.wait(), timeout=READ_TIMEOUT)
        except asyncio.TimeoutError:
            raise ComposeError("encoder did not finish") from None

    async def _teardown(self, working: Path) -> None:
        """Kill the processes, wait for their handles, and bin an unpublished file.

        Cancellation is why this is shaped the way it is. The first `await` in
        the cleanup of a task that has just been cancelled raises
        `CancelledError` straight back out, which would leave the encoder
        un-awaited and its handle on the working file still open — and on
        Windows an open handle refuses both `unlink` and `rename`. So the kill
        is issued synchronously, and the delivered cancellation is absorbed once
        so the waiting can actually happen. `run`'s `finally` still propagates
        the original `CancelledError`; only the cleanup is protected.
        """
        self._kill_now()
        try:
            await self._wait_out()
        except asyncio.CancelledError:
            # Delivered here because this runs inside a cancelled task. The
            # processes are already dead; what is left is bookkeeping that has
            # to finish. Cancellation is delivered once, so the retry proceeds.
            try:
                await self._wait_out()
            except Exception:
                pass
        except Exception:
            pass

        # A working file still present belongs to a pass that did not publish --
        # cancelled, or failed. Nothing serves it and nothing sweeps it.
        if self.output is None:
            self._discard(working)

    def _kill_now(self) -> None:
        """Synchronous, so a cancelled task still gets the signal sent."""
        # Close the write end first. A transport still open when the loop shuts
        # down is finalized by the garbage collector against a closed loop,
        # which on Windows is a page of "Event loop is closed" noise out of
        # `__del__` for a process that is already gone.
        if self._encoder is not None and self._encoder.stdin is not None:
            try:
                self._encoder.stdin.close()
            except Exception:
                pass
        for proc in (self._decoder, self._encoder):
            if proc is not None and proc.returncode is None:
                proc.kill()

    async def _wait_out(self) -> None:
        # Read the decoder's stdout to EOF before awaiting it. A pipe paused by
        # flow control never reports EOF, and `Process.wait()` on a process with
        # an undisconnected pipe never returns even once the child is dead --
        # the same trap `Recorder._aclose` documents at length.
        if self._decoder is not None and self._decoder.stdout is not None:
            try:
                while await self._decoder.stdout.read(65536):
                    pass
            except (asyncio.CancelledError, ValueError):
                raise
            except Exception:
                pass
        for proc in (self._decoder, self._encoder):
            if proc is not None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass

    def _discard(self, working: Path) -> None:
        """Delete the working file, allowing for a handle that is on its way out.

        `Process.kill` returns before Windows has finished closing the child's
        handles, and an unlink in that window fails with WinError 32. A couple
        of short retries is the difference between a clean project directory and
        a `.part` nothing will ever pick up.
        """
        for _ in range(5):
            try:
                working.unlink(missing_ok=True)
                return
            except OSError:
                time.sleep(0.05)
        log.info(
            "[COMPOSE] project=%s could not remove %s",
            self.project_id, working.name,
        )

    def _publish(self, working: Path) -> Path:
        """Move the finished file into the output folder under a readable name.

        A move, not a copy: unlike the live recording there is no API serving
        this from the project directory, so a second copy there would be dead
        weight that the next compose would have to clear.
        """
        s = get_settings()
        s.output_dir.mkdir(parents=True, exist_ok=True)
        dest = _free_path(
            s.output_dir, f"{export_stem(self.name, self.face)}_composed", ".mp4"
        )
        working.replace(dest)
        return dest


async def build_pass(
    project_id: str, source: Path, *, name: str = "", face: str = ""
) -> ComposePass | None:
    """Probe the source, work out the span, and prepare the pass.

    None when there is nothing generated to compose — an ordinary state after a
    run that was stopped before it produced anything, not an error.
    """
    info = await ffmpeg.probe(source)
    span = span_for(project_id, fps=info.fps, duration=info.duration)
    if span is None:
        return None
    return ComposePass(
        project_id, source, span,
        width=info.width, height=info.height, fps=info.fps,
        name=name, face=face,
    )
