"""The stream-mode target grid must be stable across planning ticks.

Targets are `k / rate`, so the rate decides WHICH timestamps exist, not merely
how many. A rate taken straight from a live throughput measurement drifts a few
percent per tick and drags the whole grid with it -- the failure these tests
pin down.
"""

from __future__ import annotations

from backend.services.scheduler import (
    MODE_STREAM,
    grid_rate,
    snap_rate,
    targets_for_rate,
)

FPS = 59.920309934100814


def _project(**kw):
    return {"generation_mode": MODE_STREAM, "fps": FPS, **kw}


def test_snap_rate_lands_on_the_fps_ladder_and_never_overshoots():
    assert snap_rate(FPS, FPS * 2) == FPS  # never faster than the source
    assert snap_rate(FPS, FPS) == FPS
    for rate in (30.0, 17.5, 12.0, 4.3, 0.7):
        snapped = snap_rate(FPS, rate)
        assert snapped <= rate + 1e-9, (rate, snapped)
        # `fps / snapped` is the whole number of source frames each generated
        # frame covers -- that integer is what makes the grids nest.
        n = FPS / snapped
        assert abs(n - round(n)) < 1e-9, (rate, n)
        # ...and a power of two, which is what makes the grids nest.
        assert round(n) & (round(n) - 1) == 0, (rate, n)


def test_a_drifting_throughput_measurement_no_longer_moves_the_grid():
    # Three consecutive planning ticks, each with a slightly different observed
    # rate -- exactly what a live pool reports.
    rates = [
        grid_rate(_project(), observed_rate=observed)
        for observed in (14.6, 14.71, 14.55)
    ]
    assert len(set(rates)) == 1, rates

    grids = [targets_for_rate(0.0, 2.0, rate) for rate in rates]
    assert grids[0] == grids[1] == grids[2]
    # And the grid is sane: ~15 targets a second, evenly spaced.
    assert 25 <= len(grids[0]) <= 35, len(grids[0])


def test_stepping_the_rate_down_keeps_earlier_frames_on_grid():
    """A coarser grid must be a subset of the finer one, or work already paid
    for is stranded off-grid and re-queued a millisecond away."""
    fine = grid_rate(_project(), observed_rate=30.0)
    coarse = grid_rate(_project(), observed_rate=8.0)
    assert coarse < fine

    fine_targets = set(targets_for_rate(0.0, 10.0, fine))
    coarse_targets = set(targets_for_rate(0.0, 10.0, coarse))
    stranded = coarse_targets - fine_targets
    assert not stranded, sorted(stranded)[:5]


def test_full_video_mode_asks_for_every_source_frame():
    """Nothing races the playhead in a whole-clip render, so a throughput cap
    would only guarantee a permanently sparse take."""
    assert grid_rate(_project(full_video_mode=1), observed_rate=4.0) == FPS
    # It is not real time, so even a very slow backend gets the full grid.
    assert grid_rate(_project(full_video_mode=1), observed_rate=0.4) == FPS
    # ...while an ordinary stream run is still capped.
    assert grid_rate(_project(), observed_rate=4.0) < FPS


def test_a_video_with_no_reported_fps_still_gets_a_stable_grid():
    first = grid_rate({"generation_mode": MODE_STREAM, "fps": 0}, observed_rate=7.1)
    second = grid_rate({"generation_mode": MODE_STREAM, "fps": 0}, observed_rate=7.3)
    assert first == second > 0


def test_stream_mode_targets_15s_seamless_buffer():
    # At 30fps with 15s buffer, targets_for_rate covers 450 consecutive frames
    fps = 30.0
    targets = targets_for_rate(0.0, 15.0, fps)
    assert len(targets) == 451
    # Consecutive spacing matches 1/fps exactly
    diff = round(targets[1] - targets[0], 3)
    assert diff == round(1.0 / fps, 3)

