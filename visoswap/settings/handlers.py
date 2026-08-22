"""What a settings change *does*, now that there is no Qt callback to do it.

Upstream attaches an ``exec_function`` to six of the 201 keys: changing the
value fires a Qt callback which reaches into the main window and does something
to the running session. Six keys, and the current serializer skips every one of
them -- ``web_ui.py`` drops any key whose name starts with that prefix -- which
is how six side effects went missing with nothing failing to say so.

This module is the replacement: an explicit registry from settings key to the
effect that key causes. Explicit is the whole point. A side effect attached to a
widget is invisible to everything that is not a widget, and the six that went
missing are the evidence.

**Handlers take their target as an argument.** Nothing here imports the engine,
touches a global, or reaches for a session; a handler can only affect the object
it was handed (T-03-17). That is also what keeps this file inside a settings
package that must stay importable without torch, numpy, onnxruntime or Qt --
plan 03-02's AST scan asserts it over the whole package and this file is walked
by it. The GPU cache clear that upstream's callback did through ``torch``
directly is a method on the models processor, so nothing here needs torch to do
the same work.

**Each handler is named after the setting, not after the upstream function.**
Upstream's names describe a Qt event -- ``change_execution_provider``,
``change_threads_number``, ``set_video_playback_fps``. These describe an effect.
One of the three upstream names is actively misleading about its own direction;
see ``apply_custom_playback_fps``.

**Every handler returns the settings updates it causes**, as a mapping, which is
usually empty. Two of the three act on the target and update nothing; the third
updates a setting and touches no target. One return contract covers both, and a
handler that wrote its own updates would need a connection, a project id and a
tier -- and would then be the second place settings are written.

Validation happens in ``dispatch``, before the target is reached
---------------------------------------------------------------
A value here becomes an execution provider or a thread count on a live
inference session, so it is held to the schema's option list and numeric bounds
first. The store already validates on write, but a handler can be dispatched
from a value that never went through the store -- a control that fires before it
persists, say -- and "the caller definitely validated" is the assumption that is
true right up until it is not.

Standard library only: ``math`` and ``logging``.
"""

import logging
import math

from visoswap import schema
from visoswap.settings import validate

LOGGER = logging.getLogger(__name__)

__all__ = [
    "HANDLERS",
    "DELIBERATELY_UNHANDLED",
    "NoSuchHandler",
    "apply_providers_priority",
    "apply_thread_count",
    "apply_custom_playback_fps",
    "effective_playback_fps",
    "dispatch",
    "has_side_effect",
]

#: The slider the frame-rate toggle seeds. Named here because the handler reads
#: its declared bounds out of the schema rather than restating them.
CUSTOM_FPS_SLIDER = "VideoPlaybackCustomFpsSlider"


class NoSuchHandler(KeyError):
    """Raised for a key that has no side effect, recorded or otherwise.

    Raising rather than returning quietly is deliberate. A dispatcher that
    silently does nothing for an unregistered key is indistinguishable from the
    serializer that silently dropped all six of these in the first place, and it
    would fail the same way: invisibly, and only in what the user sees.
    """


def apply_providers_priority(value, target, models_dir=None):
    """Switch the execution provider, then release what the old one was holding.

    Order matters: memory is freed *after* the device the models were loaded on
    has changed, which is the order upstream used and the only order in which
    the second call frees the right thing.

    Upstream (``control_actions.change_execution_provider``) stopped the
    ``video_processor`` first and then updated a Qt progress bar. Both calls are
    deliberately absent. ``video_processor`` is the module Phase 1 dropped whole
    -- see ``tests/test_dropped_modules.py`` and the Phase 1 context surface for
    why -- and a stub standing in for it would be a call that looks like it does
    something. The progress bar is Qt. Neither omission is a gap; both are
    decisions, recorded here so the next reader finds one.
    """
    target.switch_providers_priority(value)
    target.clear_gpu_memory()
    return {}


def apply_thread_count(value, target, models_dir=None):
    """Set the number of execution threads on the models processor.

    **Read this before "correcting" it back to upstream.** Upstream's callback
    (``control_actions.change_threads_number``) sets the thread count on
    ``video_processor`` -- the module Phase 1 dropped -- so a faithful port is a
    function that does nothing at all while looking exactly right. The call that
    reaches the engine is ``models_processor.set_number_of_threads``, which is
    what ``visomaster_headless.py`` uses and what this calls.

    Upstream also emptied the CUDA cache directly through ``torch`` and updated
    a Qt progress bar. The cache clear is already what the processor's own
    ``delete_models_trt`` does as part of setting the count; the progress bar is
    Qt.
    """
    target.set_number_of_threads(int(value))
    return {}


def apply_custom_playback_fps(value, media, models_dir=None):
    """Seed the frame-rate slider **from** the media. Yes, that direction.

    ``control_actions.set_video_playback_fps`` does the opposite of what its
    name suggests: it does not apply the slider to playback, it writes the
    loaded clip's own frame rate into the slider, and only when the toggle is on
    and a media capture exists. That direction is reproduced here exactly,
    because a renderer built on the name rather than the behaviour would push
    the user's slider value at a clip that never had that rate.

    ``media`` is whatever holds the loaded clip; only its ``fps`` is read, and a
    missing or zero rate counts as no media. With the toggle off, or with
    nothing loaded, there is no update at all -- inventing one would pin
    playback to a number no clip ever had.

    **The slider is an int**, with a step of 1 and a declared range, upstream and
    here. So 29.97 -- the most common frame rate there is -- is stored as 30, and
    a 240 fps clip is stored at the slider's maximum. That precision loss is a
    consequence of the schema typing the key as an int, not a bug in this
    function, and it is the one place in the phase where the data model is
    visible to a user as a number that moved. Halves round **up**: Python's own
    ``round`` sends 30.5 to 30 and 31.5 to 32, which is not a rule anyone expects
    of a frame rate.

    The bounds are read from the schema entry. Writing them here would be a
    second, staler copy of a rule that already lives in the data.
    """
    if not value:
        return {}
    rate = getattr(media, "fps", None)
    if not rate:
        return {}

    entry = schema.entry(CUSTOM_FPS_SLIDER)
    seeded = int(math.floor(float(rate) + 0.5))
    minimum = entry.get("minimum")
    maximum = entry.get("maximum")
    if minimum is not None:
        seeded = max(seeded, minimum)
    if maximum is not None:
        seeded = min(seeded, maximum)
    if seeded != rate:
        LOGGER.info(
            "custom playback fps: media rate %r stored as %d -- %s is an "
            "integer slider bounded to [%r, %r]",
            rate,
            seeded,
            CUSTOM_FPS_SLIDER,
            minimum,
            maximum,
        )
    return {CUSTOM_FPS_SLIDER: seeded}


def effective_playback_fps(custom_enabled, slider_fps, media_fps):
    """The rate playback should actually run at: the slider when the toggle is
    on, the clip's own rate when it is off.

    Here rather than at the call site because Phase 4's scheduler needs this
    answer and so does the player, and two derivations of it is exactly how the
    two end up disagreeing about what time it is. ``None`` when the toggle is off
    and nothing is loaded -- there is no rate, and saying so is better than
    substituting a slider the user never turned on.
    """
    if custom_enabled:
        return slider_fps
    return media_fps


#: Key -> the effect changing it causes. Three of the six.
HANDLERS = {
    "ProvidersPrioritySelection": apply_providers_priority,
    "nThreadsSlider": apply_thread_count,
    "VideoPlaybackCustomFpsToggle": apply_custom_playback_fps,
}

#: The other three, recorded by name with the reason each one needs nothing.
#:
#: A silent absence and a deliberate decision look identical in a diff a year
#: from now, and only one of them is safe to leave alone. This is the second.
DELIBERATELY_UNHANDLED = {
    "ViewFaceMaskEnableToggle": (
        "Still fully functional as a setting: the frame worker reads it "
        "directly at four sites (lines 73, 751, 757, 1280 of "
        "processors/workers/frame_worker.py). Its upstream callback, "
        "layout_actions.fit_image_to_view_onchange, only refit a Qt image view "
        "that does not exist here."
    ),
    "ViewFaceCompareEnableToggle": (
        "Same as ViewFaceMaskEnableToggle: read directly by the frame worker, "
        "and its callback only refit a Qt image view. Turning it on still "
        "changes what the engine renders; nothing needs to be told."
    ),
    "ThemeSelection": (
        "Upstream's callback loads a Qt stylesheet and applies it to the "
        "QApplication. There is no QApplication here, and theming belongs to "
        "the web frontend Phase 5 builds. The setting stays; the effect moves."
    ),
}


def has_side_effect(key):
    """True when changing ``key`` has an effect beyond the stored value."""
    return key in HANDLERS


def dispatch(key, value, target=None, models_dir=None):
    """Validate ``value``, run ``key``'s handler against ``target``, return updates.

    ``UnknownSettingsKey`` for a key the schema never heard of.
    ``InvalidSettingValue`` for a value its own schema entry refuses -- checked
    before ``target`` is touched at all.
    ``NoSuchHandler`` for a schema key with no side effect, including one
    recorded as deliberately unhandled; the message carries the recorded reason,
    so a caller that dispatches wrongly is told why rather than told nothing.
    """
    schema.entry(key)
    if key not in HANDLERS:
        recorded = DELIBERATELY_UNHANDLED.get(key)
        if recorded is not None:
            raise NoSuchHandler(
                "{} deliberately has no handler: {}".format(key, recorded)
            )
        raise NoSuchHandler(
            "{} has no side effect. {} of the 201 keys do; changing any other "
            "key means storing it and nothing more.".format(key, len(HANDLERS))
        )
    checked = validate.validate(key, value, models_dir)
    return HANDLERS[key](checked, target, models_dir)
