"""The recorder's composition rules and its partial-output guarantees."""

from __future__ import annotations

import asyncio
import re
import subprocess
from datetime import datetime

import pytest

from backend.config import get_settings
from backend.services import cache, recorder

PROJECT_ID = "e" * 32
SIZE = (160, 120)
FPS = 10.0
DURATION = 6.0


# ffmpeg/ffprobe are a hard requirement of the backend, not an optional feature:
# the recorder and the frame extractor both shell out to them. A test that skips
# on their absence is a test that can silently report green over a backend that
# cannot record anything. The backend refuses to start without them anyway
# (roadmap Phase 4 criterion), so these are never skipped in practice -- make
# that a hard requirement rather than a conditional one.


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Every close exports, including the cancelled-run case, so this has to be
    # redirected for *all* tests — not just the export ones. Left at its default
    # the suite writes real files into the repo's `output/` folder.
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


def _make(project_id, source, **kw):
    return recorder.Recorder(
        project_id, source,
        width=SIZE[0], height=SIZE[1], fps=FPS, **kw,
    )


def _streams(path) -> list[str]:
    s = get_settings()
    out = subprocess.run(
        [s.ffprobe_bin, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _decodes(path) -> bool:
    """A file ffmpeg can read from start to finish without an error."""
    s = get_settings()
    return subprocess.run(
        [s.ffmpeg_bin, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
    ).returncode == 0


# -- frame selection ----------------------------------------------------------


def test_a_timestamp_landing_exactly_on_a_generated_frame_uses_it(project):
    """The boundary case: `t` equal to a generated timestamp, not just past it.

    Filenames carry an extension, so bisecting a bare `000000.000` against
    `000000.000.jpg` sorts the file *after* the probe and the frame is missed.
    At a 5s interval that silently dropped t=0, t=5, t=10 and every other exact
    hit — the majority of the frames that matter.
    """
    _write_generated(project, [0.0, 2.0, 4.0])
    rec = _make(project, "unused.mp4")

    for ts in (0.0, 2.0, 4.0):
        picked = rec._generated_for(ts)
        assert picked is not None, f"no generated frame chosen for t={ts}"
        assert picked.stem == cache.timestamp_key(ts)


def test_it_picks_the_nearest_previous_frame_never_a_future_one(project):
    _write_generated(project, [0.0, 2.0, 4.0])
    rec = _make(project, "unused.mp4")

    assert rec._generated_for(3.9).stem == cache.timestamp_key(2.0)
    assert rec._generated_for(4.1).stem == cache.timestamp_key(4.0)


def test_a_timestamp_before_the_first_generated_frame_falls_back_to_source(project):
    _write_generated(project, [2.0])
    rec = _make(project, "unused.mp4")

    assert rec._generated_for(1.9) is None


def test_half_written_frames_are_never_selected(project):
    """`.part` files are a frame mid-write, not a cache hit."""
    _write_generated(project, [0.0])
    part = cache.generated_dir(project) / f"{cache.timestamp_key(2.0)}.jpg.part"
    part.write_bytes(b"not a jpeg yet")
    rec = _make(project, "unused.mp4")

    assert rec._generated_for(2.5).stem == cache.timestamp_key(0.0)


# -- output lifecycle ---------------------------------------------------------


def test_clear_output_removes_both_the_finished_and_partial_files(project):
    recorder.output_path(project).write_bytes(b"old")
    recorder.partial_path(project).write_bytes(b"older")

    assert recorder.clear_output(project) == 2
    assert not recorder.output_path(project).exists()
    assert not recorder.partial_path(project).exists()



def test_a_completed_run_is_promoted_and_carries_the_audio(project, tmp_path):
    source = _source(tmp_path, audio=True)
    _write_generated(project, [0.0, 2.0, 4.0])

    async def run():
        rec = _make(project, source)
        await rec.start()
        rec.set_frontier(1e9)
        rec.finish()
        await asyncio.wait_for(rec._pump, timeout=120)
        await rec.aclose()
        return rec

    rec = asyncio.run(run())

    assert rec.frames_written == int(DURATION * FPS)
    # Every frame is covered: generated frames start at t=0.
    assert rec.frames_substituted == rec.frames_written
    assert recorder.output_path(project).is_file()
    assert not recorder.partial_path(project).exists()
    assert _streams(recorder.output_path(project)) == ["video", "audio"]



def test_a_silent_source_still_records(project, tmp_path):
    """`-map 1:a:0?` — a source with no audio must not abort the recording."""
    source = _source(tmp_path, audio=False)
    _write_generated(project, [0.0])

    async def run():
        rec = _make(project, source)
        await rec.start()
        rec.set_frontier(1e9)
        rec.finish()
        await asyncio.wait_for(rec._pump, timeout=120)
        await rec.aclose()

    asyncio.run(run())

    assert _streams(recorder.output_path(project)) == ["video"]



def test_a_cancelled_run_leaves_a_playable_partial(project, tmp_path):
    """Closing without `finish()` is the cancel path: keep what was made.

    The file stays `.part` — that suffix is what marks the recording as having
    stopped early, and it must still decode end to end.
    """
    source = _source(tmp_path, audio=True)
    _write_generated(project, [0.0, 2.0, 4.0])

    async def run():
        # Hold the frontier partway in rather than racing a sleep: the source is
        # short enough that the pump can otherwise consume all of it first, and
        # a run that reached the end is a *completed* run, not a cancelled one.
        stop_at = 3.0
        rec = _make(project, source)
        await rec.start()
        rec.set_frontier(stop_at)

        # Wait until the pump is parked on the deadline at `stop_at`.
        deadline = asyncio.get_event_loop().time() + 30
        while rec.frames_written < int(stop_at * FPS):
            assert asyncio.get_event_loop().time() < deadline, "pump never advanced"
            await asyncio.sleep(0.02)

        # Regression guard, not the fix: a cancelled close that takes longer than
        # a few seconds is a failure whether or not it eventually returns, and a
        # test that hangs reports nothing to anybody. The mechanism is pinned in
        # tests/test_subprocess_wait_shape.py.
        await asyncio.wait_for(rec.aclose(), timeout=10.0)
        return rec

    asyncio.run(run())

    partial = recorder.partial_path(project)
    assert partial.is_file()
    assert not recorder.output_path(project).exists()
    assert _decodes(partial)



def test_aclose_is_idempotent(project, tmp_path):
    source = _source(tmp_path, audio=True)
    _write_generated(project, [0.0])

    async def run():
        rec = _make(project, source)
        await rec.start()
        rec.set_frontier(1e9)
        await rec.aclose()
        await rec.aclose()

    asyncio.run(run())


# -- export to the output folder ----------------------------------------------



def test_a_finished_recording_is_copied_to_the_output_folder(project, tmp_path):
    out_dir = tmp_path / "out"
    get_settings().output_dir = out_dir
    source = _source(tmp_path, audio=True)
    _write_generated(project, [0.0])

    async def run():
        rec = _make(project, source, name="My Clip")
        await rec.start()
        rec.set_frontier(1e9)
        rec.finish()
        await asyncio.wait_for(rec._pump, timeout=120)
        await rec.aclose()
        return rec

    rec = asyncio.run(run())

    assert rec.exported_to is not None
    # `cache.sanitize_filename` is reused, so spaces become underscores; the
    # take name now carries the run's local timestamp (D-12).
    assert re.fullmatch(r"My_Clip_\d{8}-\d{6}\.mp4", rec.exported_to.name), (
        rec.exported_to.name
    )
    assert rec.exported_to.is_file()
    # A copy, not a move: the API still serves the project's own file.
    assert recorder.output_path(project).is_file()



def test_a_second_run_does_not_overwrite_the_first_export(project, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "My_Clip.mp4").write_bytes(b"the earlier result")
    get_settings().output_dir = out_dir
    source = _source(tmp_path, audio=True)
    _write_generated(project, [0.0])

    async def run():
        rec = _make(project, source, name="My Clip")
        await rec.start()
        rec.set_frontier(1e9)
        rec.finish()
        await asyncio.wait_for(rec._pump, timeout=120)
        await rec.aclose()
        return rec

    rec = asyncio.run(run())

    # The timestamped stem already separates runs, so the new take never
    # competes with the seeded old-convention file for one name.
    assert re.fullmatch(r"My_Clip_\d{8}-\d{6}\.mp4", rec.exported_to.name), (
        rec.exported_to.name
    )
    assert rec.exported_to.name != "My_Clip.mp4"
    assert (out_dir / "My_Clip.mp4").read_bytes() == b"the earlier result"


def test_an_unwritable_output_folder_does_not_fail_the_run(project, tmp_path, capsys):
    """Export is a convenience; it must never turn a finished run into a failure."""
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"this is a file, not a directory")
    get_settings().output_dir = blocker / "nested"
    recorder.output_path(project).write_bytes(b"pretend recording")

    rec = _make(project, "unused.mp4", name="clip")
    rec._export()  # must not raise

    assert rec.exported_to is None


# -- D-12 take naming: {project}_{stamp}_{face}.mp4[.partial] -----------------


def _export_full_run(project_id, source, **kw):
    """Record the whole clip and close it — the clean-finish export path."""

    async def run():
        rec = _make(project_id, source, **kw)
        await rec.start()
        rec.set_frontier(1e9)
        rec.finish()
        await asyncio.wait_for(rec._pump, timeout=120)
        await rec.aclose()
        return rec

    return asyncio.run(run())


TAKE_RE = re.compile(r"^af_\d{8}-\d{6}_afia_2\.mp4$")


def test_a_clean_run_exports_project_stamp_face(project, tmp_path):
    get_settings().output_dir = tmp_path / "out"
    source = _source(tmp_path, audio=True)
    _write_generated(project, [0.0])

    rec = _export_full_run(project, source, name="af", face="afia_2.jpg")

    assert rec.exported_to is not None
    assert TAKE_RE.fullmatch(rec.exported_to.name), rec.exported_to.name


def test_a_run_stopped_early_exports_a_partial_take(project, tmp_path):
    out_dir = tmp_path / "out"
    get_settings().output_dir = out_dir
    # A stopped early run leaves its `.part` unpromoted; aclose exports it
    # under the `.partial` suffix when output_include_partial allows.
    recorder.partial_path(project).write_bytes(b"partial recording bytes")
    rec = _make(project, "unused.mp4", name="af", face="afia_2.jpg")

    asyncio.run(rec.aclose())  # never finished -> the stop path

    names = [p.name for p in out_dir.iterdir()]
    assert len(names) == 1, names
    assert re.fullmatch(r"af_\d{8}-\d{6}_afia_2\.partial\.mp4", names[0]), names


def test_two_runs_leave_two_distinct_unnumbered_takes(
    project, tmp_path, monkeypatch
):
    get_settings().output_dir = tmp_path / "out"
    source = _source(tmp_path, audio=True)
    _write_generated(project, [0.0])
    # A run finishes inside a wall-clock second, so the clock is pinned and
    # advanced between runs: the stamps must differ by design (that is what
    # separates the takes), not by luck of the scheduler.
    clock = {"now": datetime(2026, 8, 24, 12, 34, 56)}

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(recorder, "datetime", Frozen)

    first = _export_full_run(project, source, name="af", face="afia_2.jpg")
    recorder.clear_output(project)  # what scheduler start does between runs
    clock["now"] = datetime(2026, 8, 24, 12, 34, 57)
    second = _export_full_run(project, source, name="af", face="afia_2.jpg")

    out_dir = get_settings().output_dir
    names = sorted(p.name for p in out_dir.iterdir())
    assert len(list(out_dir.iterdir())) == 2, names
    assert first.exported_to.name != second.exported_to.name
    # The stamps differ, so neither take needed collision numbering.
    assert all("(" not in name for name in names), names
    assert all(TAKE_RE.fullmatch(name) for name in names), names


def test_same_second_collisions_are_numbered_not_overwritten(
    project, tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    get_settings().output_dir = out_dir

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 24, 12, 34, 56)

    monkeypatch.setattr(recorder, "datetime", Frozen)
    recorder.output_path(project).write_bytes(b"recording")
    rec = _make(project, "unused.mp4", name="af", face="afia_2.jpg")
    rec._export()
    rec._export()

    names = {p.name for p in out_dir.iterdir()}
    assert names == {
        "af_20260824-123456_afia_2.mp4",
        "af_20260824-123456_afia_2 (2).mp4",
    }, names


def test_empty_name_and_no_face_exports_the_timestamp_alone(project, tmp_path):
    out_dir = tmp_path / "out"
    get_settings().output_dir = out_dir
    recorder.output_path(project).write_bytes(b"recording")
    rec = _make(project, "unused.mp4", name="", face="")

    rec._export()

    names = [p.name for p in out_dir.iterdir()]
    assert len(names) == 1, names
    assert re.fullmatch(r"\d{8}-\d{6}\.mp4", names[0]), names


def test_face_directory_and_extension_contribute_only_the_sanitized_stem(
    project, tmp_path
):
    out_dir = tmp_path / "out"
    get_settings().output_dir = out_dir
    recorder.output_path(project).write_bytes(b"recording")
    face = tmp_path / "faces" / "Weird Name.JPG"
    rec = _make(project, "unused.mp4", name="af", face=str(face))

    rec._export()

    names = [p.name for p in out_dir.iterdir()]
    assert len(names) == 1, names
    assert re.fullmatch(r"af_\d{8}-\d{6}_Weird_Name\.mp4", names[0]), names


def test_starting_again_wipes_only_working_files_and_keeps_the_take(
    project, tmp_path
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    get_settings().output_dir = out_dir
    take = out_dir / "af_20260823-033806_afia_2.mp4"
    take.write_bytes(b"the earlier take")
    recorder.output_path(project).write_bytes(b"working")
    recorder.partial_path(project).write_bytes(b"working-part")

    removed = recorder.clear_output(project)

    assert removed == 2
    assert take.is_file(), "accumulation: the previous take survives a new start"
    assert not recorder.output_path(project).exists()
    assert not recorder.partial_path(project).exists()
    assert [p.name for p in out_dir.iterdir()] == [take.name]


def test_an_unwritable_output_folder_leaves_aclose_normal_and_export_none(
    project, tmp_path
):
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"a file, not a directory")
    get_settings().output_dir = blocker / "nested"
    _write_generated(project, [0.0])
    generated_before = sorted(p.name for p in cache.generated_dir(project).iterdir())

    rec = _make(project, "unused.mp4", name="af", face="afia_2.jpg")
    raised = False
    try:
        asyncio.run(rec.aclose())
    except Exception:
        raised = True

    assert raised is False
    assert rec.exported_to is None
    assert (
        sorted(p.name for p in cache.generated_dir(project).iterdir())
        == generated_before
    ), "the run's frames are intact"


# -- the generation watermark -------------------------------------------------


def test_it_waits_for_the_watermark_then_commits(project):
    """The deadline releases as soon as the watermark passes `t`, not later.

    There is no cushion on top of the watermark, and there must not be: a
    cushion expressed in video seconds is a permanent offset the writer can
    never make up, so the recording would end that many seconds short of what
    was generated. That is the whole of the truncation bug.
    """

    async def run():
        rec = _make(project, "unused.mp4")
        rec.set_frontier(10.0)
        # Everything below 10.0 has settled, so nothing here waits.
        await asyncio.wait_for(rec._await_deadline(4.0), timeout=1.0)
        await asyncio.wait_for(rec._await_deadline(9.999), timeout=1.0)

        # At the watermark itself the frame is not settled yet: blocked.
        pending = asyncio.ensure_future(rec._await_deadline(10.0))
        await asyncio.sleep(0.05)
        assert not pending.done()

        # Moving it by the smallest amount is enough — no extra trail.
        rec.set_frontier(10.001)
        await asyncio.wait_for(pending, timeout=2.0)

    asyncio.run(run())


def test_finishing_releases_a_frame_still_above_the_watermark(project):
    """Nothing more is coming, so the recorder must stop waiting and drain."""

    async def run():
        rec = _make(project, "unused.mp4")
        rec.set_frontier(1.0)
        pending = asyncio.ensure_future(rec._await_deadline(30.0))
        await asyncio.sleep(0.05)
        assert not pending.done()

        rec.finish()
        await asyncio.wait_for(pending, timeout=2.0)

    asyncio.run(run())


def test_stopping_writes_the_generated_span_before_closing(project, tmp_path):
    """Stop must not discard what the writer had not caught up on yet.

    The writer trails generation, so there is always a swapped-but-not-muxed
    span when Stop is pressed. `aclose` cancels the pump, so before this the
    recording ended wherever the writer happened to be.
    """
    source = _source(tmp_path, audio=False)
    stop_at = 2.0

    async def run():
        rec = _make(project, source)
        await rec.start()
        # The pump parks here: nothing above 0.5s has settled yet.
        rec.set_frontier(0.5)
        deadline = asyncio.get_event_loop().time() + 30
        while rec.frames_written < int(0.5 * FPS):
            assert asyncio.get_event_loop().time() < deadline, "pump never advanced"
            await asyncio.sleep(0.02)
        parked = rec.frames_written

        # Generation had in fact reached `stop_at`, so that is what gets written.
        await rec.drain_to(stop_at, timeout=60.0)
        await rec.aclose()
        return rec, parked

    rec, parked = asyncio.run(run())
    assert parked < int(stop_at * FPS), "the test never exercised a trailing writer"
    assert rec.frames_written == int(stop_at * FPS) + 1, rec.frames_written
    # Stopped early, so the file stays a `.part` and is still playable.
    assert recorder.partial_path(project).is_file()
    assert _decodes(recorder.partial_path(project))


def test_a_fully_generated_span_is_written_whole(project, tmp_path):
    """The reported symptom: a run that generated N seconds exported ~1.

    The frame at the very end of the generated span must still be committed.
    With a trail of `g` seconds every recording stopped `g` seconds early, and
    at the shipped default of 15.0 a 16-second run exported one second.
    """

    source = _source(tmp_path, audio=False)

    async def run():
        rec = _make(project, source)
        await rec.start()
        # The whole source has settled: the watermark sits just past its last
        # frame, which is exactly what `db.recording_watermark` reports once
        # every job is done. Not 1e9 — the point is that the boundary itself
        # is inclusive of everything generated.
        rec.set_frontier(DURATION)
        rec.finish()
        await asyncio.wait_for(rec._pump, timeout=120)
        await rec.aclose()
        return rec

    rec = asyncio.run(run())
    # Every source frame, not merely the ones outside a trailing window.
    assert rec.frames_written == int(DURATION * FPS), rec.frames_written


def test_a_stop_after_finish_does_not_cut_the_full_drain_short(project, tmp_path):
    """The stop that arrives while the full drain is still running.

    A completed range run calls `finish()`, which releases the pump to the end
    of the source. The stop that follows derives its bound from the last
    *generated* timestamp -- a few seconds in on a range run -- and applying
    that bound mid-drain ended the recording wherever the pump had reached. A
    ten-second range over a 72.2s source exported 67.5s of it.
    """
    source = _source(tmp_path, audio=False)

    async def run():
        rec = _make(project, source)
        await rec.start()
        rec.set_frontier(DURATION)
        rec.finish()
        # Stop, with a bound the pump is already past, while the drain runs.
        await rec.drain_to(0.1, timeout=120.0)
        await rec.aclose()
        return rec

    rec = asyncio.run(run())
    assert rec.frames_written == int(DURATION * FPS), rec.frames_written


def test_reaching_the_end_of_the_source_finalizes_without_a_stop(project, tmp_path):
    """A run left running still has to produce a finished file.

    Nothing further can arrive once the source is exhausted, but finalization
    used to happen only in `aclose`, so a range run nobody stopped sat on a
    `.part` reporting an incomplete recording indefinitely.
    """
    source = _source(tmp_path, audio=False)

    async def run():
        rec = _make(project, source)
        await rec.start()
        rec.set_frontier(DURATION)
        rec.finish()
        deadline = asyncio.get_event_loop().time() + 120
        while not recorder.output_path(project).is_file():
            assert asyncio.get_event_loop().time() < deadline, "never finalized"
            await asyncio.sleep(0.05)
        # Idempotent: the ordinary stop still runs afterwards and must be a
        # no-op rather than a second finalization.
        await rec.aclose()

    asyncio.run(run())
    assert not recorder.partial_path(project).exists()
    assert _decodes(recorder.output_path(project))
