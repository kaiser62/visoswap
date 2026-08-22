"""Reject a wrong value at the boundary that accepted it, not four layers later.

The failure this module exists to prevent is not a crash. It is a value that is
accepted, persisted, resolved, handed to the swap pipeline, and only *there*
found to be a string where a tensor operation wanted a float -- at which point
the traceback points at inference code and the actual mistake is three layers
and one process boundary away.

Two entry points, deliberately separated
----------------------------------------
``validate`` is **strict**, and it is for everything that arrives from a caller:
in Phase 4 an HTTP handler, in Phase 5 a control the user moved. It accepts a
value only if it already carries the type the schema declares, then checks
bounds, option membership and text length. A string offered for a number is a
rejection, not an input to be repaired.

``coerce`` is **lenient**, and it exists for **migration** and nothing else. Its
sole intended caller in this project is plan 03-03's preset seeding, because
``profiles.json`` stores most values as strings. It applies the same
shape-derived conversion the schema generator applies -- ``tools/
dump_engine_settings.py:coerce`` -- and then runs the strict validator on the
result, so lenient means *lenient about the type it was handed*, never about the
value it produced.

Conflating the two is exactly how the string round-tripping this phase exists to
end comes quietly back, one convenience at a time.
``tests/test_settings_validation.py`` asserts against the tree that nothing else
calls the lenient path.

The rules come from the schema and from nowhere else
----------------------------------------------------
Type, bounds, option lists and text length are read from the loaded schema. No
settings key name appears as a literal anywhere below, and a test pins that: a
second hardcoded rule here is the type moving back out of the data and into
code, which is the arrangement this phase replaced.

Three traps a naive validator falls into
----------------------------------------
1. ``isinstance(True, int)`` is True in Python. Bools are therefore tested
   **before** ints in every numeric branch, or every toggle passes as a slider
   position and every ``1`` passes as a toggle.
2. An int is a perfectly good float input. Reject ``1`` for a float key and the
   frontend has to send ``1.0`` for a slider sitting at one, which no renderer
   does -- so ints are **widened** to float for float keys, and what comes back
   is the widened value. The widening is deliberately one-way: ``2.5`` for an
   integral key is a caller who has not decided what they mean.
3. The **step** is not a constraint. A value that does not land on a step
   boundary is **accepted**, and that is a decision rather than an omission: the
   swap strength slider steps by 25 over a range of 0 to 500, and three of the
   makeup sliders step by 3 over 0 to 255 -- which does not even reach the
   maximum. Upstream never enforced the step; it is a renderer's increment hint.
   Rejecting off-step values would make legitimate stored values unwritable.

Standard library only, and nothing from this package except ``visoswap.schema``.
"""

from visoswap import schema

__all__ = [
    "validate",
    "validate_stored",
    "coerce",
    "InvalidSettingValue",
    "UnknownSettingsKey",
    "VALIDATORS",
]

UnknownSettingsKey = schema.UnknownSettingsKey


class InvalidSettingValue(ValueError):
    """Raised for a value the schema's own rules refuse.

    The message names the key, what was offered and what was expected. A
    validation failure that says only "invalid" is a support conversation, and
    the caller who has to have it is a user who cannot see this code.
    """


#: Longest offending value echoed back verbatim. The one text key accepts a
#: thousand characters, and a rejection message is read in a log or a toast --
#: neither of which is improved by the whole rejected string.
_ECHO_LIMIT = 120


def _show(value):
    shown = repr(value)
    if len(shown) <= _ECHO_LIMIT:
        return shown
    return "{}... [{} characters]".format(shown[:_ECHO_LIMIT], len(shown))


def _reject(key, entry, value, expectation):
    raise InvalidSettingValue(
        "{}: refused {} ({}) -- the schema declares {}, {}".format(
            key,
            _show(value),
            type(value).__name__,
            entry["type"],
            expectation,
        )
    )


# --------------------------------------------------------------------------
# strict: the type must already be right
# --------------------------------------------------------------------------


def _within_bounds(key, entry, value):
    """Range check shared by both numeric shapes. The step is not consulted."""
    minimum = entry.get("minimum")
    if minimum is not None and value < minimum:
        _reject(
            key,
            entry,
            value,
            "and its minimum is {!r}".format(minimum),
        )
    maximum = entry.get("maximum")
    if maximum is not None and maximum < value:
        _reject(
            key,
            entry,
            value,
            "and its maximum is {!r}".format(maximum),
        )
    return value


def _validate_int(key, entry, value):
    # Bool first. `isinstance(True, int)` is True, so the order is the check.
    if isinstance(value, bool) or not isinstance(value, int):
        _reject(key, entry, value, "which takes a whole number and nothing else")
    return _within_bounds(key, entry, value)


def _validate_float(key, entry, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(key, entry, value, "which takes a number")
    # Deliberate widening: an int is a fine float input, and what comes back is
    # typed, so a slider sitting at one reaches the engine as 1.0 either way.
    return _within_bounds(key, entry, float(value))


def _validate_toggle(key, entry, value):
    if not isinstance(value, bool):
        _reject(
            key,
            entry,
            value,
            "which takes a bool -- a string that spells one is still a string",
        )
    return value


def _validate_selection(key, entry, value):
    if not isinstance(value, str):
        _reject(key, entry, value, "which takes one of its option strings")
    options = entry.get("options")
    if not options:
        # One key's options are a directory listing, and a bare checkout has no
        # models directory. Refusing every value for a key whose list is empty
        # would make the key unwritable rather than merely unset.
        return value
    if value not in options:
        _reject(
            key,
            entry,
            value,
            "whose options are {!r}".format(list(options)),
        )
    return value


def _validate_text(key, entry, value):
    if not isinstance(value, str):
        _reject(key, entry, value, "which takes a string")
    # Character-count bounds, not slider bounds. Emitting these as
    # minimum/maximum is the confusion that makes the current renderer draw a
    # slider from 0 to 1000 over a text field.
    maximum = entry.get("max_length")
    if maximum is not None and maximum < len(value):
        _reject(
            key,
            entry,
            value,
            "and its maximum length is {!r} characters, not {!r}".format(
                maximum, len(value)
            ),
        )
    minimum = entry.get("min_length")
    if minimum is not None and len(value) < minimum:
        _reject(
            key,
            entry,
            value,
            "and its minimum length is {!r} characters".format(minimum),
        )
    return value


#: Dispatch by the schema's own type vocabulary. A shape the schema declares and
#: this table does not is a ``KeyError`` on the first write of such a key, and a
#: test compares the two sets directly so it never gets that far.
VALIDATORS = {
    "int": _validate_int,
    "float": _validate_float,
    "toggle": _validate_toggle,
    "selection": _validate_selection,
    "text": _validate_text,
}


def validate(key, value, models_dir=None):
    """The value, typed as the schema declares it, or ``InvalidSettingValue``.

    Raises ``UnknownSettingsKey`` for a key the schema has never heard of --
    a typo or a stale client, both of which silently do nothing in the system
    this replaces.

    ``models_dir`` reaches the one key whose option list is a directory listing;
    every other key ignores it.
    """
    entry = schema.resolved_entry(key, models_dir)
    return VALIDATORS[entry["type"]](key, entry, value)


def validate_stored(key, value, models_dir=None):
    """``validate`` for a value already on disk, with one deliberate relaxation.

    A key whose schema entry names a resolver for its options -- there is one,
    and its options are a directory listing -- has an option list that changes
    without anyone editing a setting. Deleting a model file would otherwise turn
    a perfectly ordinary stored selection into a hard read failure, and because
    resolution reads the whole tier at once, one removed file would take every
    other setting down with it.

    "The file this names is no longer on disk" is not the same fact as "this
    value was never allowed", and only the second one is corruption. The first
    belongs to whatever tries to load the model, which is where a missing file
    is diagnosable. So membership is enforced on the way **in** and not on the
    way out; type, bounds and length are enforced in both directions.

    Discovered by ``resolve`` failing on a value it had itself just accepted,
    written against one models directory and read back against another.
    """
    entry = schema.resolved_entry(key, models_dir)
    if entry.get("options_from") is not None:
        entry = dict(entry)
        entry["options"] = None
    return VALIDATORS[entry["type"]](key, entry, value)


# --------------------------------------------------------------------------
# lenient: migration only
# --------------------------------------------------------------------------


def _to_int(value):
    # Through `float` first on purpose: ``int('2.50')`` raises, and upstream
    # writes decimal strings into keys that are otherwise integral.
    return int(float(value))


def _to_float(value):
    return float(value)


def _to_bool(value):
    # Not a conversion. Upstream's toggles are real bools in both the layout
    # dicts and the saved profiles -- measured, not assumed -- so anything else
    # offered for a toggle is a caller inventing a spelling, and the strict
    # validator below is what says so.
    return value


def _to_str(value):
    return value if isinstance(value, str) else str(value)


_CONVERTERS = {
    "int": _to_int,
    "float": _to_float,
    "toggle": _to_bool,
    "selection": _to_str,
    "text": _to_str,
}


def coerce(key, value, models_dir=None):
    """Convert by shape, then validate. **Migration only** -- see the docstring.

    The conversion is the schema generator's, so a value migrated from a saved
    profile and the same value generated into the schema cannot disagree about
    whether they are an int or a str. That agreement is asserted, not assumed.
    """
    entry = schema.resolved_entry(key, models_dir)
    shape = entry["type"]
    if shape in ("int", "float") and isinstance(value, bool):
        # `int(float(True))` is 1, which would smuggle a toggle into a slider
        # through the one path that is allowed to be lenient.
        _reject(key, entry, value, "and a bool is not a number here either")
    try:
        converted = _CONVERTERS[shape](value)
    except (TypeError, ValueError) as error:
        raise InvalidSettingValue(
            "{}: refused {} ({}) -- it does not convert to {}: {}".format(
                key, _show(value), type(value).__name__, shape, error
            )
        ) from None
    return VALIDATORS[shape](key, entry, converted)
