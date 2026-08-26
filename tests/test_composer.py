"""The offline compose pass: span, substitution, audio and cancellation.

The live recorder is allowed to drop a frame that arrives after its deadline —
it is racing playback. This pass is not racing anything, so "every generated
frame is in the file" is a property that can actually be asserted, and these
tests assert it by reading pixels back out of the composed mp4.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from backend.config import get_settings
from backend.services import cache, composer

PROJECT_ID = "c" * 32
SIZE = (160, 120)
FPS = 10.0
DURATION = 6.0

# ffmpeg is a hard requirement of the backend (see test_recorder), never skipped.


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    get_settings.cache_clear()
    cache.ensure_project_dirs(PROJECT_ID)
    yield PROJECT_ID
    get_settings.cache_clear()


def _write_generated(project_id: str, timestamps, colour=(0, 255, 0)) -> None:
    from PIL import Image

    for ts in timestamps:
        Image.new("RGB", SIZE, colour).save(
            cache.generated_dir(project_id) / f"{cache.timestamp_key(float(ts))}.jpg"
        )


def _source(tmp_path, *, audio: bool = True):
    s = get_settings()
    dest = tmp_path / ("with_audio.mp4" if audio else "silent.mp4")
    cmd = [
        s.ffmpeg_bin, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"testsrc=size={SIZE[0]}x{SIZE[1]}:rate={FPS:g}:duration={DURATION:g}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION:g}",
                "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _probe(path, entries: str, stream: str = "v:0") -> list[str]:
    s = get_settings()
    out = subprocess.run(
        [s.ffprobe_bin, "-v", "error", "-select_streams", stream,
         "-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _mean_green(path, at: float) -> float:
    """Mean green channel of the frame at `at`, read back out of the file.

    Reading pixels rather than trusting the pass's own counters: a substitution
    that is counted but written to the wrong timestamp would pass a count
    assertion and produce a take with the swap in the wrong place.
    """
    s = get_settings()
    raw = subprocess.run(
        [s.ffmpeg_bin, "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    greens = raw[1::3]
    return sum(greens) / max(1, len(greens))


async def _run(project_id, source, **kw):
    run = await composer.build_pass(project_id, source, **kw)
    assert run is not None
    return run, await run.run()


# -- span ---------------------------------------------------------------------


def test_span_covers_one_step_past_the_last_generated_frame(project):
    # The nearest-previous rule gives every generated frame the gap *after* it.
    # Ending at the last frame's own timestamp would throw that gap away, which
    # at a 1s spacing is a whole second of swap missing from the tail.
    _write_generated(project, [1.0, 2.0, 3.0])
    span = composer.span_for(project, fps=FPS, duration=DURATION)
    assert span is not None
    assert span.start == pytest.approx(1.0)
    assert span.end == pytest.approx(4.0)


def test_span_uses_the_median_gap_so_one_ragged_interval_cannot_stretch_it(project):
    # A run stopped mid-flight leaves one odd gap at the end. A mean would let
    # that outlier lengthen the tail of every take it appears in.
    _write_generated(project, [0.0, 1.0, 2.0, 5.0])
    span = composer.span_for(project, fps=FPS, duration=DURATION)
    assert span is not None
    assert span.end == pytest.approx(6.0)


def test_span_is_clamped_to_the_source_duration(project):
    _write_generated(project, [5.0, 5.5])
    span = composer.span_for(project, fps=FPS, duration=DURATION)
    assert span is not None
    assert span.end == pytest.approx(DURATION)


def test_nothing_generated_is_no_span_rather_than_an_error(project):
    assert composer.span_for(project, fps=FPS, duration=DURATION) is None


def test_a_half_written_frame_is_not_part_of_the_span(project):
    _write_generated(project, [1.0])
    (cache.generated_dir(project) / f"{cache.timestamp_key(9.0)}.jpg.part").write_bytes(b"x")
    span = composer.span_for(project, fps=FPS, duration=DURATION)
    assert span is not None
    assert span.end < 9.0


# -- the pass -----------------------------------------------------------------


def test_composes_the_generated_span_and_publishes_a_take(project, tmp_path):
    _write_generated(project, [1.0, 2.0, 3.0])
    run, output = asyncio.run(_run(project, _source(tmp_path), name="Demo", face="ada"))

    assert output.parent == get_settings().output_dir
    assert output.name.endswith("_composed.mp4")
    assert output.stat().st_size > 0
    # Every frame of the span carries a generated frame under the
    # nearest-previous rule, so the pass substitutes all of them.
    assert run.frames_written > 0
    assert run.frames_substituted == run.frames_written


def test_the_composed_take_is_the_span_long_not_the_source_long(project, tmp_path):
    # The whole point of composing the span rather than the clip: a swap over
    # three seconds of a six-second source must not re-encode six seconds.
    _write_generated(project, [1.0, 2.0, 3.0])
    _, output = asyncio.run(_run(project, _source(tmp_path)))
    duration = float(_probe(output, "format=duration", stream="v:0")[0])
    assert duration == pytest.approx(3.0, abs=0.35)


def test_the_generated_pixels_are_actually_in_the_file(project, tmp_path):
    # Green frames in, green frames out. A pass that counted substitutions but
    # wrote the source would pass every counter assertion above and this one is
    # what catches it.
    _write_generated(project, [1.0, 2.0, 3.0], colour=(0, 255, 0))
    _, output = asyncio.run(_run(project, _source(tmp_path)))
    assert _mean_green(output, 0.5) > 200


def test_the_take_carries_audio_cut_to_the_same_span(project, tmp_path):
    # A stream copy would start at the audio packet at or before the seek and
    # leave the track a packet out of step; and mapping audio without the
    # matching `-ss` would put the top of the track under the middle of the
    # video.
    _write_generated(project, [2.0, 3.0, 4.0])
    _, output = asyncio.run(_run(project, _source(tmp_path)))
    codecs = _probe(output, "stream=codec_name", stream="a:0")
    assert codecs == ["aac"]
    audio_duration = float(_probe(output, "stream=duration", stream="a:0")[0])
    assert audio_duration == pytest.approx(3.0, abs=0.35)


def test_a_silent_source_still_composes(project, tmp_path):
    # `-map 1:a:0?` — a source with no audio must not fail the whole compose.
    _write_generated(project, [1.0, 2.0])
    _, output = asyncio.run(_run(project, _source(tmp_path, audio=False)))
    assert output.stat().st_size > 0
    assert _probe(output, "stream=codec_name", stream="a:0") == []


def test_an_unreadable_generated_frame_falls_back_to_the_source(project, tmp_path):
    # A corrupt frame is one frame's worth of loss, never the take's.
    _write_generated(project, [1.0, 2.0])
    (cache.generated_dir(project) / f"{cache.timestamp_key(2.0)}.jpg").write_bytes(b"not a jpeg")
    _, output = asyncio.run(_run(project, _source(tmp_path)))
    assert output.stat().st_size > 0


def test_nothing_generated_yields_no_pass_at_all(project, tmp_path):
    assert asyncio.run(composer.build_pass(project, _source(tmp_path))) is None


def test_a_cancelled_pass_leaves_no_working_file_behind(project, monkeypatch, tmp_path):
    # A `.part` from a cancelled compose is served by nothing and swept by
    # nothing; it would just sit in the project directory forever.
    _write_generated(project, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    source = _source(tmp_path)

    started = asyncio.Event()

    async def stalled(self, t, source_frame):
        # Held open rather than raced against a wall-clock sleep: a six-second
        # 160x120 clip composes in well under the time it would take to be sure
        # the cancel landed mid-pass.
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(composer.ComposePass, "_compose", stalled)

    async def scenario():
        run = await composer.build_pass(project, source)
        assert run is not None
        task = asyncio.create_task(run.run())
        await asyncio.wait_for(started.wait(), timeout=30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return run

    run = asyncio.run(scenario())
    assert run.output is None
    assert not (cache.project_dir(project) / "compose.mp4.part").exists()


def test_two_composes_of_one_project_do_not_overwrite_each_other(project, tmp_path):
    _write_generated(project, [1.0, 2.0])
    source = _source(tmp_path)
    _, first = asyncio.run(_run(project, source, name="Demo"))
    _, second = asyncio.run(_run(project, source, name="Demo"))
    assert first != second
    assert first.exists() and second.exists()
