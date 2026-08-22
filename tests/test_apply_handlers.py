"""The side effects that died with the Qt callbacks, and what they do now.

Six settings keys carried an ``exec_function`` upstream. The current serializer
skips any key whose name starts with that prefix, which is how six side effects
went missing without anything failing to say so. Three of them need a handler
here; three deliberately do not, and are recorded by name.

The target is a **recording stand-in**, not a mock library. The provider
handler's two calls have to happen in a particular order, and a stand-in that
keeps a list makes the assertion read as the specification rather than as a
framework incantation. It also means these tests need neither torch nor a GPU,
which is the point: a handler that could only be tested against a live inference
session would not be tested.
"""

import pytest

from visoswap import schema
from visoswap.settings import handlers, validate

PROVIDER_KEY = "ProvidersPrioritySelection"
THREADS_KEY = "nThreadsSlider"
CUSTOM_FPS_TOGGLE = "VideoPlaybackCustomFpsToggle"
CUSTOM_FPS_SLIDER = "VideoPlaybackCustomFpsSlider"


class RecordingTarget:
    """Records what it was asked to do, in the order it was asked.

    Stands in for ``ModelsProcessor``. Only the methods a handler is allowed to
    reach exist here, so a handler that grew a fourth call would fail with an
    ``AttributeError`` naming it rather than passing quietly.
    """

    def __init__(self):
        self.calls = []

    def switch_providers_priority(self, provider_name):
        self.calls.append(("switch_providers_priority", provider_name))
        return provider_name

    def set_number_of_threads(self, value):
        self.calls.append(("set_number_of_threads", value))

    def clear_gpu_memory(self):
        self.calls.append(("clear_gpu_memory",))


class Media:
    """A loaded clip. Only its frame rate is ever read."""

    def __init__(self, fps):
        self.fps = fps


@pytest.fixture
def target():
    return RecordingTarget()


# --------------------------------------------------------------------------
# execution provider
# --------------------------------------------------------------------------


def test_setting_the_provider_switches_priority_then_clears_gpu_memory(target):
    """Order is part of the behaviour: memory is freed *after* the device the
    models were on has changed, not before."""
    handlers.apply_providers_priority("CPU", target)
    assert target.calls == [
        ("switch_providers_priority", "CPU"),
        ("clear_gpu_memory",),
    ]


def test_the_provider_handler_does_not_invent_a_stop_for_the_dropped_processor(target):
    """Upstream stopped the video processor first. Phase 1 dropped that module
    whole, and a stub standing in for it would be a call that looks like it does
    something."""
    handlers.apply_providers_priority("CUDA", target)
    assert not any(call[0].startswith("stop") for call in target.calls)
    assert "video_processor" in handlers.apply_providers_priority.__doc__


# --------------------------------------------------------------------------
# thread count
# --------------------------------------------------------------------------


def test_setting_the_thread_count_sets_it_on_the_target_as_an_int(target):
    handlers.apply_thread_count(4, target)
    assert target.calls == [("set_number_of_threads", 4)]
    assert type(target.calls[0][1]) is int


def test_the_thread_handler_reaches_the_processor_that_still_exists(target):
    """The trap this handler exists to avoid.

    Upstream's callback sets the thread count on the **video processor** -- the
    module Phase 1 dropped -- so a faithful port is a function that does nothing
    while looking exactly right. The call that reaches the engine is the models
    processor's, which is what the headless reference uses.
    """
    assert "models_processor" in handlers.apply_thread_count.__doc__
    handlers.apply_thread_count(1, target)
    assert target.calls == [("set_number_of_threads", 1)]


# --------------------------------------------------------------------------
# custom playback frame rate
# --------------------------------------------------------------------------


def _slider_bounds():
    entry = schema.entry(CUSTOM_FPS_SLIDER)
    return entry["minimum"], entry["maximum"]


def test_turning_the_toggle_on_with_media_loaded_seeds_the_slider_from_the_media():
    """The direction is upstream's, and it is the reverse of the name.

    ``set_video_playback_fps`` does not apply the slider to playback. It writes
    the media's own rate *into* the slider.
    """
    updates = handlers.apply_custom_playback_fps(True, Media(fps=24.0))
    assert updates == {CUSTOM_FPS_SLIDER: 24}
    assert type(updates[CUSTOM_FPS_SLIDER]) is int


def test_a_fractional_rate_is_rounded_because_the_schema_types_the_slider_int():
    """29.97 is the most common frame rate there is, and it cannot be stored.

    That is a consequence of the data model rather than a bug: the slider is an
    int with a step of 1, upstream and here.
    """
    assert handlers.apply_custom_playback_fps(True, Media(29.97)) == {
        CUSTOM_FPS_SLIDER: 30
    }
    assert handlers.apply_custom_playback_fps(True, Media(23.976)) == {
        CUSTOM_FPS_SLIDER: 24
    }
    # Half rounds up, not to even. Python's own round() sends 30.5 to 30, which
    # is not what anybody expects a frame rate to do.
    assert handlers.apply_custom_playback_fps(True, Media(30.5)) == {
        CUSTOM_FPS_SLIDER: 31
    }


def test_a_rate_outside_the_sliders_bounds_is_clamped_into_them():
    minimum, maximum = _slider_bounds()
    assert handlers.apply_custom_playback_fps(True, Media(240.0)) == {
        CUSTOM_FPS_SLIDER: maximum
    }
    assert handlers.apply_custom_playback_fps(True, Media(0.4)) == {
        CUSTOM_FPS_SLIDER: minimum
    }


def test_the_bounds_are_read_from_the_schema_and_not_written_as_literals():
    """Retype the slider and the handler moves with it, or the clamp becomes a
    second, stale copy of a rule that lives in the data.

    Checked against the parsed code rather than the file text, so naming ``120``
    in a docstring -- which the module does, to explain the clamp -- stays
    allowed while writing it into the clamp does not.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(handlers.__file__).read_text(encoding="utf-8"))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }
    minimum, maximum = _slider_bounds()
    assert maximum not in literals, (
        "the slider maximum {} is a literal in handlers.py; read it from the "
        "schema entry instead".format(maximum)
    )


def test_turning_the_toggle_on_with_no_media_produces_no_update():
    """Upstream's callback runs only when a media capture is loaded. With none,
    there is no rate to seed the slider from and inventing one would pin
    playback to a number no clip ever had."""
    assert handlers.apply_custom_playback_fps(True, None) == {}
    assert handlers.apply_custom_playback_fps(True, Media(fps=None)) == {}
    assert handlers.apply_custom_playback_fps(True, Media(fps=0)) == {}


def test_turning_the_toggle_off_produces_no_update():
    assert handlers.apply_custom_playback_fps(False, Media(fps=25.0)) == {}
    assert handlers.apply_custom_playback_fps(False, None) == {}


def test_the_frame_rate_handler_mutates_nothing(target):
    """It returns the updates it would cause. A handler that wrote them itself
    would need a connection, a project and a tier, and would then be the second
    place settings are written."""
    handlers.apply_custom_playback_fps(True, Media(30.0))
    assert target.calls == []


def test_the_effective_playback_rate_is_the_slider_when_on_and_the_media_when_off():
    """Phase 4's scheduler needs one answer to this, derived in one place.
    Two derivations is how the player and the generator end up disagreeing about
    what time it is."""
    assert handlers.effective_playback_fps(True, 30, 23.976) == 30
    assert handlers.effective_playback_fps(False, 30, 23.976) == 23.976
    # No media and the toggle off: there is no rate at all, and saying so is
    # better than substituting the slider the user never turned on.
    assert handlers.effective_playback_fps(False, 30, None) is None


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------


def test_every_key_with_a_dropped_side_effect_is_accounted_for():
    """The gate that keeps a future upstream ``exec_function`` from landing
    unnoticed -- which is exactly how these six went missing."""
    carrying = sorted(
        key
        for key, entry in schema.WIDGETS.items()
        if entry.get("exec_function") is not None
    )
    assert carrying, "no key carries a side effect -- the walk proves nothing"
    unaccounted = [
        key
        for key in carrying
        if key not in handlers.HANDLERS and key not in handlers.DELIBERATELY_UNHANDLED
    ]
    assert not unaccounted, (
        "these keys carry a dropped Qt side effect and are neither handled nor "
        "recorded as deliberately unhandled: {}".format(unaccounted)
    )


def test_the_deliberately_unhandled_keys_are_named_with_reasons():
    """A silent absence and a recorded decision look identical in a diff a year
    from now. This makes it the second one."""
    assert set(handlers.DELIBERATELY_UNHANDLED) == {
        "ViewFaceMaskEnableToggle",
        "ViewFaceCompareEnableToggle",
        "ThemeSelection",
    }
    for key, reason in handlers.DELIBERATELY_UNHANDLED.items():
        assert len(reason) > 40, (key, reason)


def test_the_three_handlers_are_registered_against_their_own_keys():
    assert set(handlers.HANDLERS) == {PROVIDER_KEY, THREADS_KEY, CUSTOM_FPS_TOGGLE}
    assert handlers.has_side_effect(PROVIDER_KEY)
    assert not handlers.has_side_effect("SwapperResSelection")


def test_dispatching_runs_the_registered_handler(target):
    updates = handlers.dispatch(PROVIDER_KEY, "TensorRT", target)
    assert target.calls == [
        ("switch_providers_priority", "TensorRT"),
        ("clear_gpu_memory",),
    ]
    assert updates == {}

    assert handlers.dispatch(CUSTOM_FPS_TOGGLE, True, Media(60.0)) == {
        CUSTOM_FPS_SLIDER: 60
    }


def test_dispatching_a_key_with_no_side_effect_raises(target):
    """Silently doing nothing is the failure this whole file exists to end."""
    with pytest.raises(handlers.NoSuchHandler):
        handlers.dispatch("SwapperResSelection", "256", target)


def test_dispatching_a_recorded_non_handler_raises_and_says_why(target):
    with pytest.raises(handlers.NoSuchHandler) as caught:
        handlers.dispatch("ThemeSelection", "Light", target)
    assert "stylesheet" in str(caught.value).lower()


def test_dispatching_a_key_the_schema_never_heard_of_raises(target):
    with pytest.raises(schema.UnknownSettingsKey):
        handlers.dispatch("NotASettingAtAll", 1, target)


def test_dispatch_validates_before_the_target_sees_anything(target):
    """A settings value here becomes an execution-provider change on a live
    inference session (T-03-17). The option list and the numeric bounds are the
    only thing standing between a stale client and that session."""
    with pytest.raises(validate.InvalidSettingValue):
        handlers.dispatch(PROVIDER_KEY, "TotallyRealProvider", target)
    with pytest.raises(validate.InvalidSettingValue):
        handlers.dispatch(THREADS_KEY, 10_000, target)
    with pytest.raises(validate.InvalidSettingValue):
        handlers.dispatch(THREADS_KEY, "4", target)
    assert target.calls == [], "the target was reached before validation"


def test_handlers_take_their_target_as_an_argument_and_import_no_engine():
    """They cannot reach a session the caller did not hand them, and importing
    settings must never drag the inference stack in behind it."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(handlers.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
    assert not imported & {"torch", "numpy", "onnxruntime", "cv2", "kornia"}, imported
