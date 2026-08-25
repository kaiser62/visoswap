"""Continuous mp4 recording of the composed video.

The recorder writes the *full* source video with generated frames substituted
in at their timestamps, plus the source audio, to
`data/projects/<id>/output.mp4.part` — renamed to `output.mp4` on a clean
finish.

Two properties drive every design choice here:

**The file must be playable at all times.** A normal mp4 only becomes readable
once the `moov` atom is written at finalize, so a killed process leaves nothing
usable. `-movflags +frag_keyframe+empty_moov+default_base_moof` writes a
self-contained fragment per keyframe interval, which stays playable at any
truncation point. That flag is load-bearing: without it cancel, error and crash
all produce garbage.

**The recorder is off the playback path.** It may lag, stall or block without
violating the central principle, because nothing about playback waits on it. It
trails the generation watermark — the timestamp below which every job has
settled — and commits whatever exists at each frame. A generated frame arriving
after its deadline is dropped from the recording rather than stalling it.

Unlike `ffmpeg.extract_frame`, this decodes the source sequentially from end to
end. `CLAUDE.md` invariant 5 names this module as the sole component permitted a
full decode and re-encode.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

from backend.config import get_settings
from backend.services import cache

log = logging.getLogger(__name__)

# Raw frames move between the two ffmpeg processes as bgr24, three bytes per
# pixel. Chosen over rgb24 only for consistency with the local swap backend.
BYTES_PER_PIXEL = 3

# Never let a decode read block forever if ffmpeg wedges.
READ_TIMEOUT = 120.0

# Seconds of video per fragment, and so the most a hard kill can cost.
#
# `+frag_keyframe` flushes a fragment at each keyframe, and x264's default GOP
# is 250 frames — about 8s at 30fps, during which the output file stays at 28
# bytes of `ftyp` and a crash yields nothing. Forcing a short GOP is what makes
# the crash-safety guarantee real rather than nominal. Two seconds costs a few
# percent of bitrate against a default GOP, which is the right trade here.
FRAGMENT_SECONDS = 2.0


class RecorderError(RuntimeError):
    pass


def _free_path(folder: Path, stem: str, ext: str) -> Path:
    """`stem.ext`, or `stem (2).ext` and so on if that name is taken.

    Two runs of the same project would otherwise overwrite each other's export,
    silently destroying the earlier result.
    """
    candidate = folder / f"{stem}{ext}"
    index = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({index}){ext}"
        index += 1
    return candidate


def output_path(project_id: str) -> Path:
    return cache.project_dir(project_id) / "output.mp4"


def partial_path(project_id: str) -> Path:
    """The in-progress file. Valid fragmented mp4 at every instant."""
    return output_path(project_id).with_suffix(".mp4.part")


def clear_output(project_id: str) -> int:
    """Drop any recording from a previous run.

    Called alongside `cache.clear_frames`. A run may use a different face, model
    or resolution, so an old recording is as stale as an old frame — and leaving
    it would let a new run silently extend someone else's video.
    """
    removed = 0
    for path in (output_path(project_id), partial_path(project_id)):
        if path.exists():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _decode_jpeg(path: Path, size: tuple[int, int]) -> bytes:
    """Generated frame -> raw bgr24 bytes at exactly `size`.

    Scaling here rather than trusting the generator: a backend that returns a
    different resolution than the source would otherwise desynchronise the raw
    stream and every subsequent frame would be garbage.
    """
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.size != size:
            img = img.resize(size, Image.BILINEAR)
        r, g, b = img.split()
        return Image.merge("RGB", (b, g, r)).tobytes()


class Recorder:
    """Composes and encodes one project's video, incrementally.

    Lifecycle is owned by the scheduler, not by a worker: worker lifetime is a
    single frame, while the recorder must outlive individual frame failures.
    """

    def __init__(
        self,
        project_id: str,
        source: Path,
        *,
        width: int,
        height: int,
        fps: float,
        duration: float | None = None,
        name: str = "",
        face: str = "",
        swap_range: tuple[float, float] | None = None,
    ) -> None:
        s = get_settings()
        self.project_id = project_id
        self.source = Path(source)
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps) if fps and fps > 0 else 0.0
        self.duration = duration
        self.name = name
        # Display stem of the source face this run used — not a path; the
        # recorder never opens it. It only names the exported take.
        self.face = face
        # The span a range run asked for, or None for a run that covers the
        # whole video. Outside it the source frame is written untouched: the
        # nearest-*previous* rule has no upper bound of its own, so a ten-second
        # range over a seventy-second source used to hold the frame generated at
        # t=10 for the remaining sixty seconds -- a take that shows a swapped
        # face over footage nobody asked to swap.
        self.swap_range = swap_range
        # Set once the finished recording has been copied to the output folder.
        self.exported_to: Path | None = None
        self.frame_bytes = self.width * self.height * BYTES_PER_PIXEL

        self._decoder: asyncio.subprocess.Process | None = None
        self._encoder: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._frontier = 0.0
        self._finished = False
        # Set at stop time: the last timestamp worth writing. None means "to the
        # end of the source", which is what a run that completes does.
        self._stop_after: float | None = None
        self._advance = asyncio.Event()
        self._closed = False
        # Held for the whole of `aclose`, so a second caller waits for the
        # finalization rather than returning while the file is still being
        # promoted and copied out from under it.
        self._closing = asyncio.Lock()
        # Decoded generated frame, held for reuse across the span it covers.
        self._held_key: str | None = None
        self._held: bytes | None = None
        # Generated-frame listing, refreshed when the frontier moves.
        self._keys: tuple[list[str], list[str]] | None = None
        self._keys_frontier = -1.0
        self.frames_written = 0
        self.frames_substituted = 0

        if self.frame_bytes <= 0:
            raise RecorderError("source has no usable dimensions")
        if self.fps <= 0:
            raise RecorderError("source has no usable frame rate")

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        s = get_settings()
        dest = partial_path(self.project_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)

        self._decoder = await asyncio.create_subprocess_exec(
            s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(self.source),
            "-map", "0:v:0",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        self._encoder = await asyncio.create_subprocess_exec(
            s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin",
            # 0: the composed video we feed frame by frame.
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}",
            "-r", f"{self.fps:.6f}",
            "-i", "pipe:0",
            # 1: the source again, audio only, copied without re-encoding.
            "-vn", "-i", str(self.source),
            "-map", "0:v:0",
            # `?` keeps a silent source from aborting the whole recording.
            "-map", "1:a:0?",
            "-c:v", "libx264", "-preset", s.recorder_preset,
            "-crf", str(s.recorder_crf), "-pix_fmt", "yuv420p",
            # Short GOP so fragments actually land on disk while the run is in
            # progress — see FRAGMENT_SECONDS.
            "-g", str(max(1, int(self.fps * FRAGMENT_SECONDS))),
            "-force_key_frames", f"expr:gte(t,n_forced*{FRAGMENT_SECONDS:g})",
            "-c:a", "copy",
            # ffmpeg reads the audio input as fast as it can while video
            # trickles in over the pipe, so the muxing queue has to absorb the
            # whole audio track's worth of packets.
            "-max_muxing_queue_size", "4096",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            # Belt and braces with the forced keyframes above: cap fragment
            # length by time as well, so a stretch of video x264 decides needs
            # no keyframe still cannot hold the file back.
            "-frag_duration", str(int(FRAGMENT_SECONDS * 1_000_000)),
            # `-shortest` ends the output when the shortest input ends. Audio
            # runs the full length of the source while video arrives over the
            # pipe, so without it a run that stops early leaves a short video
            # against a full-length audio tail and the container reports the
            # audio's duration — a 16s recording that claims to be 180s.
            "-shortest",
            "-f", "mp4",
            "-y", str(dest),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Drain the encoder's stderr into the log. Captured rather than
        # discarded because a muxer that refuses to start otherwise shows up
        # only as a broken pipe several layers away, and an unread PIPE would
        # eventually block ffmpeg itself.
        self._stderr = asyncio.create_task(
            self._drain_stderr(), name=f"recorder-stderr:{self.project_id}"
        )
        self._pump = asyncio.create_task(
            self._run(), name=f"recorder:{self.project_id}"
        )
        log.info(
            "[RECORDER] project=%s started size=%dx%d fps=%.3f",
            self.project_id, self.width, self.height, self.fps,
        )

    async def _drain_stderr(self) -> None:
        assert self._encoder and self._encoder.stderr
        try:
            async for line in self._encoder.stderr:
                text = line.decode("utf-8", "replace").strip()
                if text:
                    log.warning("[RECORDER] project=%s ffmpeg: %s",
                                self.project_id, text[:300])
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _drain_decoder_stdout(self) -> None:
        """Read the decoder's stdout to EOF so its transport disconnects.

        Raw bgr24 frames can easily outrun a 64 KiB `StreamReader` high-water
        mark, which makes flow control pause the transport. A paused pipe never
        reports EOF, and a `Process.wait()` on a process whose output pipe is
        paused never returns -- even once the child is dead and its returncode
        is set. Reading the stream lets the reader's own
        ``_maybe_resume_transport`` un-pause it and observe end-of-stream, which
        is what wakes the pending exit waiter.
        """
        assert self._decoder and self._decoder.stdout
        # Discard into nothing; the bytes are the output of a process we are
        # killing and are wanted only for the EOF they bring about.
        while await self._decoder.stdout.read(65536):
            pass

    def set_frontier(self, ts: float) -> None:
        """Highest video timestamp whose generation has settled."""
        if ts > self._frontier:
            self._frontier = float(ts)
            self._advance.set()

    def finish(self) -> None:
        """No further frames are coming; drain the rest at full speed."""
        self._finished = True
        self._advance.set()

    async def drain_to(self, t: float, *, timeout: float = 120.0) -> None:
        """Write everything up to and including `t`, then stop the pump.

        This is what a *stopped* run finalizes through. Cancelling the pump
        outright — which is all `aclose` used to do — threw away every frame
        between wherever the writer had got to and the end of what had actually
        been generated, so pressing Stop truncated the recording to whatever
        the writer happened to have reached.

        Bounded on purpose. `finish()` would also release the pump, but it
        releases it all the way to the end of the source, which on a stream run
        stopped 30 seconds into a ten-minute video means encoding nine and a
        half minutes of untouched footage nobody asked for.
        """
        if self._pump is None or self._pump.done():
            return
        if self._finished:
            # `finish()` already released the pump to run to the end of the
            # source, and it is doing exactly that. Imposing a bound now would
            # cut it off wherever it had got to: a completed ten-second range
            # run drained to 67.5s of a 72.2s source and then stopped there,
            # because the stop that followed set the bound back to 10s and the
            # very next iteration was past it. Wait for the drain instead.
            try:
                await asyncio.wait_for(asyncio.shield(self._pump), timeout=timeout)
            except asyncio.TimeoutError:
                log.warning(
                    "[RECORDER] project=%s full drain timed out after %.0fs",
                    self.project_id, timeout,
                )
            return
        self._stop_after = t
        # Everything at or below `t` is settled by definition here: the caller
        # derived `t` from what has already been generated.
        self.set_frontier(t + 1e-6)
        self._advance.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._pump), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(
                "[RECORDER] project=%s drain to %.2fs timed out after %.0fs",
                self.project_id, t, timeout,
            )

    async def aclose(self) -> None:
        """Stop and finalize. Idempotent — cancel, error and shutdown all land here."""
        async with self._closing:
            if self._closed:
                return
            self._closed = True
            await self._aclose()

    async def _aclose(self) -> None:
        for task in (self._pump, self._stderr):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._pump = self._stderr = None

        if self._decoder and self._decoder.returncode is None:
            self._decoder.kill()

        # Drain the decoder's stdout to end of stream before awaiting it. The
        # bytes are discarded on purpose: reading them is the only way the
        # stdout transport observes EOF, and without that observation
        # `Process.wait()` never returns even though the process is already
        # dead. asyncio's exit waiter is woken only from `_call_connection_lost`,
        # which `_try_finish` reaches only once every pipe has disconnected; a
        # paused pipe (flow control suspended because we stopped reading) has no
        # read outstanding, so EOF is never seen. Doing this inside the
        # `returncode is None` guard would leak an undrained pipe for a decoder
        # that exited on its own, so it lives outside the guard.
        if self._decoder is not None and self._decoder.stdout is not None:
            try:
                await asyncio.wait_for(
                    self._drain_decoder_stdout(), timeout=READ_TIMEOUT
                )
            except asyncio.TimeoutError:
                # A decoder that ignores the kill and keeps producing is bound,
                # not followed; its wait below will return once the bound trips.
                pass
            except (BrokenPipeError, ConnectionResetError):
                pass

        if self._decoder is not None:
            try:
                await asyncio.wait_for(self._decoder.wait(), timeout=READ_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning(
                    "[RECORDER] project=%s decoder did not exit within %.0fs",
                    self.project_id, READ_TIMEOUT,
                )

        if self._encoder:
            # Closing stdin is what makes ffmpeg flush its final fragment and
            # exit cleanly. Killing it instead would still leave a playable
            # file, but would drop whatever is buffered.
            if self._encoder.stdin and not self._encoder.stdin.is_closing():
                try:
                    self._encoder.stdin.close()
                    await self._encoder.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            try:
                await asyncio.wait_for(self._encoder.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                self._encoder.kill()
                await self._encoder.wait()

        self._promote()
        self._export()
        log.info(
            "[RECORDER] project=%s closed written=%d substituted=%d status=%s",
            self.project_id, self.frames_written, self.frames_substituted,
            "complete" if self._finished else "partial",
        )

    def _promote(self) -> None:
        """Rename the `.part` to its final name once the run finished cleanly.

        A partial file is deliberately *left* as `.part` — it is a valid,
        playable fragmented mp4, and the suffix is what tells the API (and the
        user) that the recording stopped early.
        """
        part = partial_path(self.project_id)
        if not self._finished or not part.exists() or part.stat().st_size == 0:
            return
        part.replace(output_path(self.project_id))

    def _stem(self) -> str:
        """`project_datetime_face`, each part sanitized and empties dropped.

        The timestamp is what separates one run from the next: without it every
        run of the same project competes for one name and `_free_path` numbers
        them `(2)`, `(3)`, which says nothing about which is which. Local time,
        not UTC — this name is read by the person sitting at the machine.
        """
        def part(value: str) -> str:
            # Strip the extension a face carries (`ada.jpg`) before it becomes
            # a component of an mp4 name.
            cleaned = cache.sanitize_filename(value, "").rsplit(".", 1)[0]
            return cleaned.strip("._")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        pieces = [part(self.name), stamp, part(self.face)]
        return "_".join(p for p in pieces if p) or stamp

    def _export(self) -> None:
        """Copy the recording into the output folder under a readable name.

        A copy, not a move: the file under `data/projects/<id>/` is what the API
        serves and what a later request expects to still be there. This is the
        one a person opens.

        Never fatal — a full disk or a read-only output folder must not turn a
        finished recording into a failed run.
        """
        s = get_settings()
        source = output_path(self.project_id)
        suffix = ""
        if not source.is_file():
            if not (s.output_include_partial and partial_path(self.project_id).is_file()):
                return
            source = partial_path(self.project_id)
            suffix = ".partial"

        try:
            s.output_dir.mkdir(parents=True, exist_ok=True)
            dest = _free_path(s.output_dir, f"{self._stem()}{suffix}", ".mp4")
            shutil.copy2(source, dest)
            self.exported_to = dest
            log.info("[RECORDER] project=%s exported=%s", self.project_id, dest.name)
        except Exception as exc:
            log.warning(
                "[RECORDER] project=%s export failed: %s", self.project_id, exc
            )

    # -- the substitute loop --------------------------------------------------

    async def _run(self) -> None:
        try:
            await self._pump_frames()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[RECORDER] project=%s pump crashed", self.project_id)
            return
        if self._finished:
            # Reaching the end of the source is the whole recording; nothing
            # else is going to arrive. Finalize here rather than waiting for a
            # stop that may never come -- a range run left running parked at a
            # `.part` reporting `complete: false` indefinitely, with the moov
            # atom still unwritten, even though every frame had been pumped.
            # Deferred to a task on purpose: `aclose` cancels `self._pump`,
            # which is this very task, and by the time the task runs this
            # coroutine has returned so the cancel is a no-op.
            asyncio.create_task(self.aclose())

    async def _pump_frames(self) -> None:
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
                # Clean end of the source video.
                self._finished = True
                return
            except asyncio.TimeoutError:
                log.warning("[RECORDER] project=%s decoder stalled", self.project_id)
                return

            t = index / self.fps
            # A stopped run is written to its bound and no further. Returning
            # here leaves the file a `.part`, which is exactly right: the
            # recording did stop early.
            if self._stop_after is not None and t > self._stop_after:
                return
            await self._await_deadline(t)

            frame = await self._compose(t, raw)
            try:
                stdin.write(frame)
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                log.warning("[RECORDER] project=%s encoder closed early", self.project_id)
                return

            self.frames_written += 1
            index += 1

    async def _await_deadline(self, t: float) -> None:
        """Hold at `t` until every job at or below `t` has settled.

        The frontier is a watermark (`db.recording_watermark`): everything below
        it is done, one way or another. There is no additional cushion on top of
        it, and there must not be. A cushion expressed in video seconds is not a
        delay — it is a permanent offset the writer can never make up, so the
        recording ends that many seconds short of what was generated and the
        tail is lost at every stop. `recorder_grace` was 15.0, which is why a
        run that generated a 15-second span exported roughly one second.

        Blocking here is safe — playback does not wait on the recorder. Once the
        watermark clears `t` the frame is committed with whatever exists, so a
        slow backend delays the recording but never deadlocks it.
        """
        while not self._finished and t >= self._frontier:
            self._advance.clear()
            try:
                await asyncio.wait_for(self._advance.wait(), timeout=2.0)
            # 3.10 compatibility: `asyncio.TimeoutError` only became the builtin
            # in 3.11, and the live backend runs VisoMaster's 3.10.
            except asyncio.TimeoutError:
                pass

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
                self._held_key = key
            except Exception as exc:
                # A half-written or corrupt generated frame must not abort the
                # recording; fall back to the original for its whole span.
                log.warning(
                    "[RECORDER] project=%s t=%.3f generated frame unreadable: %s",
                    self.project_id, t, exc,
                )
                self._held, self._held_key = None, key
                return source_frame

        if self._held is None or len(self._held) != self.frame_bytes:
            return source_frame
        self.frames_substituted += 1
        return self._held

    def _generated_for(self, t: float) -> Path | None:
        """Nearest *previous* generated frame — invariant 2, never a future one.

        Keyed off what is actually on disk rather than off `interval`. The target
        grid is not a fixed spacing: stream mode and the adaptive rate in
        `scheduler.grid_rate` both place targets at `k / rate` for a rate that
        moves during the run, so computing the covering timestamp arithmetically
        would miss frames the scheduler really produced.
        """
        if self.swap_range is not None:
            start, end = self.swap_range
            if t < start or t > end:
                return None
        stems, names = self._generated_keys()
        if not stems:
            return None
        # Bisect on the stem, never the filename: `000000.000.jpg` sorts *after*
        # a bare `000000.000`, so probing with the raw key made every timestamp
        # landing exactly on a generated frame fall through to the source — at a
        # 5s interval that silently dropped t=0, t=5, t=10 and so on.
        pos = bisect_right(stems, cache.timestamp_key(t))
        if pos == 0:
            return None
        return cache.generated_dir(self.project_id) / names[pos - 1]

    def _generated_keys(self) -> tuple[list[str], list[str]]:
        """Sorted `(stems, filenames)` for the generated frames on disk.

        `timestamp_key` is zero-padded to a fixed width precisely so that
        lexical order equals numeric order, which is what makes the bisect above
        correct without parsing every name back to a float. The stems are kept
        alongside the names so the probe can compare like with like.

        The listing is cached because this runs once per *source* frame — at
        30fps against a directory holding thousands of generated frames, a fresh
        `iterdir` each time would cost more than the encode. It is only ever
        stale in the safe direction: a frame that landed since the last refresh
        is picked up on the next frontier advance, and the deadline in
        `_await_deadline` guarantees a refresh has happened after the frontier
        passed `t + grace`.
        """
        if self._keys_frontier == self._frontier and self._keys is not None:
            return self._keys
        base = cache.generated_dir(self.project_id)
        names = (
            sorted(
                p.name for p in base.iterdir()
                if p.is_file() and not p.name.endswith(".part")
            )
            if base.is_dir()
            else []
        )
        self._keys = ([Path(n).stem for n in names], names)
        self._keys_frontier = self._frontier
        return self._keys
