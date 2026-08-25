"""The decoder walk in `Engine._read_frame`.

No models and no GPU: these drive the reader alone. The ground truth is a plain
sequential decode from frame 0 -- deliberately not a seek, because a seek to a
non-keyframe is the thing under suspicion. It does not reliably land on the
frame asked for, so it was returning the wrong picture as well as costing
~230ms a time against a couple of milliseconds for a `grab()`.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np
import pytest

from visoswap.engine import GRAB_FORWARD_LIMIT, Engine

FRAMES = 140
SIZE = (64, 64)


def _painted(i: int) -> np.ndarray:
    """A frame no other frame in the clip looks like.

    Flat greys survive compression too well -- neighbours come back identical
    and an off-by-one walk would pass unnoticed. Per-frame noise cannot.
    """
    rng = np.random.default_rng(i)
    return rng.integers(0, 256, size=(SIZE[1], SIZE[0], 3), dtype=np.uint8)


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("reader") / "counter.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, SIZE)
    if not writer.isOpened():  # pragma: no cover - codec missing on the box
        pytest.skip("no mp4v encoder available")
    for i in range(FRAMES):
        writer.write(_painted(i))
    writer.release()
    return str(path)


@pytest.fixture(scope="module")
def truth(clip: str) -> dict[int, np.ndarray]:
    """Every frame of the clip, decoded the one way nobody disputes."""
    capture = cv2.VideoCapture(clip)
    frames: dict[int, np.ndarray] = {}
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames[len(frames)] = frame
    capture.release()
    assert len(frames) >= FRAMES - 2, len(frames)
    # Guard the guard: if the codec flattened neighbours into each other, an
    # off-by-one would slip through every assertion below.
    assert not np.array_equal(frames[8], frames[9])
    return frames


def _reader(clip: str) -> Engine:
    """An Engine with only its decoder wired up -- no models, no device."""
    engine = Engine.__new__(Engine)
    engine._reader = cv2.VideoCapture(clip)
    engine._reader_lock = threading.Lock()
    engine._reader_pos = -1
    engine._video_path = clip
    return engine


def test_walking_forward_returns_the_frame_that_was_asked_for(clip, truth):
    engine = _reader(clip)
    try:
        # A stride of four is the shape a 15fps grid takes on a 60fps source.
        for wanted in range(0, 64, 4):
            got = engine._read_frame(wanted)
            assert np.array_equal(got, truth[wanted]), wanted
    finally:
        engine._reader.release()


def test_the_first_read_of_a_bind_does_not_walk_off_by_one(clip, truth):
    """`_reader_pos` starts at -1 meaning "nothing decoded", not frame -1."""
    engine = _reader(clip)
    try:
        assert np.array_equal(engine._read_frame(0), truth[0])
    finally:
        engine._reader.release()


def test_a_backwards_jump_still_seeks(clip, truth):
    engine = _reader(clip)
    try:
        engine._read_frame(40)
        got = engine._read_frame(8)
        assert got.shape == truth[8].shape
    finally:
        engine._reader.release()


class _CountingCapture:
    """Delegates to a real capture, counting the walk. cv2's own attributes
    are read-only, so the count has to live in front of it, not on it."""

    def __init__(self, capture: cv2.VideoCapture) -> None:
        self._capture = capture
        self.grabs = 0

    def grab(self) -> bool:
        self.grabs += 1
        return self._capture.grab()

    def read(self):
        return self._capture.read()

    def set(self, *args) -> bool:
        return self._capture.set(*args)

    def release(self) -> None:
        self._capture.release()


def test_a_short_hop_walks_instead_of_seeking(clip):
    engine = _reader(clip)
    engine._reader = _CountingCapture(engine._reader)
    try:
        engine._read_frame(0)
        # `_reader_pos` is the frame the decoder will hand back NEXT, so after
        # reading 0 it already sits on 1 and only three frames want skipping.
        engine._read_frame(4)
        assert engine._reader.grabs == 3
    finally:
        engine._reader.release()


def test_a_jump_past_the_walk_limit_seeks_rather_than_grabbing(clip):
    """Stepping is only cheaper than seeking up to a point; past it, seek."""
    engine = _reader(clip)
    engine._reader = _CountingCapture(engine._reader)
    try:
        engine._read_frame(0)
        engine._read_frame(1 + GRAB_FORWARD_LIMIT + 5)
        assert engine._reader.grabs == 0
    finally:
        engine._reader.release()
