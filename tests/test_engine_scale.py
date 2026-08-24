"""The resolution knob's contract, driven through the sealed runner.

Phase 05.1 plan 08 (D-15b): ``Engine.swap`` takes an optional target width and
does its detection and swap work at that width, then hands back a frame at the
media's native dimensions. Both halves are the contract. The downscale is the
saving; the upscale is what keeps the recorder's timeline and the browser's
overlay from receiving a frame that changed size mid-run.

The probes run inside ``_engine_runner.py --scale`` -- the same sealed
subprocess every engine test goes through -- and report timings, shapes and
counts only (T-02-10).

**Nothing here skips.** Missing media, a missing settings fixture and a missing
model link are all ``ASSET_MISSING`` from the runner and therefore a nonzero
exit here. A skipped resolution check is a knob that looks like a speedup and
has proven nothing -- the precise failure D-14 recorded for the thread slider.
"""

import pytest

from tests.conftest import run_engine_runner


def _parse(detail: str) -> dict[str, str]:
    """``native=1920x1080 full_shape=1920x1080 ...`` -> a mapping."""
    fields = {}
    for word in detail.split():
        key, sep, value = word.partition("=")
        if sep:
            fields[key] = value
    return fields


def _shape(value: str) -> tuple[int, int]:
    width, _, height = value.partition("x")
    return int(width), int(height)


@pytest.fixture(scope="session")
def scale_run(engine_python):
    """Run the probe mode once and share the result.

    Session-scoped because the run costs a model load plus four swaps, and
    every assertion below is about that one run rather than about a fresh one.
    """
    code, output = run_engine_runner(engine_python, ["--scale"])
    label, _, rest = output.partition(":")
    mode, _, detail = rest.partition(":")
    return {
        "code": code,
        "output": output,
        "label": label,
        "mode": mode,
        "detail": detail,
        "fields": _parse(detail),
    }


def test_the_probe_run_exits_clean(scale_run):
    assert scale_run["code"] == 0, (
        "the sealed scale run did not exit CLEAN:\n  {}\n"
        "ASSET_MISSING (3) means a fixture or media file is not on disk -- see "
        "docs/engine-test-assets.md. ENGINE_ERROR (4) means a probe raised.".format(
            scale_run["output"]
        )
    )
    assert scale_run["label"] == "CLEAN", scale_run["output"]
    assert scale_run["mode"] == "scale", scale_run["output"]


def test_an_unscaled_swap_returns_the_native_dimensions(scale_run):
    fields = scale_run["fields"]
    assert _shape(fields["full_shape"]) == _shape(fields["native"]), (
        "a swap with no target width did not return the media's native "
        "dimensions -- the default path changed behaviour:\n  {}".format(
            scale_run["output"]
        )
    )


@pytest.mark.parametrize("rung", ["three_quarter", "half"])
def test_a_scaled_swap_still_returns_the_native_dimensions(scale_run, rung):
    """The size contract holds at every rung, not only at full width.

    The recorder composes generated frames onto the original timeline and the
    overlay positions an image over a video element; both break on a frame that
    changed size mid-run.
    """
    fields = scale_run["fields"]
    assert _shape(fields["{}_shape".format(rung)]) == _shape(fields["native"]), (
        "the {} swap returned {} but the media is {} -- a downstream consumer "
        "would receive a frame that changed size mid-run:\n  {}".format(
            rung,
            fields.get("{}_shape".format(rung)),
            fields.get("native"),
            scale_run["output"],
        )
    )


def test_a_target_at_or_above_native_width_is_ignored(scale_run):
    """Never upscale on the way in, mirroring the worker helper's rule."""
    assert scale_run["fields"].get("oversize_upscaled") == "no", (
        "a target width at or above the native width changed the work done -- "
        "the engine and the worker helper now disagree about what the same "
        "project row means:\n  {}".format(scale_run["output"])
    )


def test_an_odd_target_width_is_coerced_to_an_even_one(scale_run):
    """Odd dimensions break downstream encoders."""
    processed = scale_run["fields"].get("odd_processed_width")
    assert processed is not None, scale_run["output"]
    assert int(processed) % 2 == 0, (
        "an odd target width reached the pipeline as {} -- an odd dimension "
        "breaks the encoder the recorder feeds:\n  {}".format(
            processed, scale_run["output"]
        )
    )


def test_half_width_costs_less_than_full_width(scale_run):
    """The knob is a lever only if it moves the number (D-14).

    The embedding cache is warm for every timed swap in the run, so the
    comparison isolates the resize rather than a first-call model load.
    """
    fields = scale_run["fields"]
    full = float(fields["full_seconds"])
    half = float(fields["half_seconds"])
    assert half < full, (
        "half width cost {:.3f}s against {:.3f}s at full width -- lowering the "
        "processing scale did not lower the engine's work, so the control is a "
        "non-lever and must be documented as one rather than shipped as a "
        "speedup (D-14):\n  {}".format(half, full, scale_run["output"])
    )
