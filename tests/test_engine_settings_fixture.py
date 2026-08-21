"""The checked-in settings fixture is typed, complete, and non-overlapping.

``tests/fixtures/engine_settings.json`` is the only settings source the sealed
engine runner is allowed to read. It is generated once, offline, by
``tools/dump_engine_settings.py`` on an interpreter that has Qt -- because three
of the four ``*_layout_data`` modules reach PySide6 on import and therefore
cannot be read by any Qt-free test.

Two properties matter and neither is obvious from looking at the file:

* **It is typed.** The layout dicts' ``default`` values are *strings*:
  ``ClipAmountSlider`` defaults to the string ``'50'``, not ``50``.
  ``profiles.json`` is the same. Handing those straight to the engine fails deep
  inside inference with a ``TypeError`` on a tensor op, a long way from the load
  that caused it. The generator coerces by *widget shape*, not by the Python type
  of ``default``, and this file is where that coercion is held to account.
* **It is complete and disjoint.** 168 project keys, 33 global keys, no key in
  both. A key that goes missing here is a setting the engine silently runs
  without.

This module imports **only the standard library and pytest**, on purpose: it has
to run on the plain developer interpreter, which has neither torch nor Qt. It
reads the fixture as data and never re-derives it -- re-deriving would mean
importing the layout dicts, which is precisely what it cannot do.

The fixture is a **test fixture, not a schema**. It lives under ``tests/``, it is
disposable, and Phase 3's generated ``visoswap/schema/schema.json`` replaces it.
"""

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "engine_settings.json"

#: Measured across all 201 keys. Five widget shapes exist, not the four the plan
#: predicted -- see ``NUMERIC_LOOKING_SELECTIONS`` and ``TEXT_KEYS`` below.
#:
#:   float   42  ``decimals`` alongside min/max/step
#:   int     93  min/max/step without ``decimals``
#:   bool    43  no bounds, no options
#:   str     23  22 with an ``options`` list, plus ``ClipText`` (see below)
EXPECTED_TYPE_CENSUS = {"float": 42, "int": 93, "bool": 43, "str": 23}

EXPECTED_PROJECT_KEYS = 168
EXPECTED_GLOBAL_KEYS = 33

#: Four selection keys whose default is legitimately a numeric-*looking* string.
#: They are genuine dropdowns -- the engine compares the value against an
#: ``options`` list of strings, so ``'128'`` must stay ``'128'`` and must not be
#: "helpfully" coerced to ``128``. Naming them is what lets the string check
#: below stay sharp: any *other* string that parses as a number is a slider whose
#: coercion silently failed.
NUMERIC_LOOKING_SELECTIONS = frozenset(
    {
        "SwapperResSelection",
        "LandmarkDetectModelSelection",
        "WebcamMaxNoSelection",
        "WebCamMaxFPSSelection",
    }
)

#: ``ClipText`` is the fifth shape and the one the plan's four-shape rule misses.
#: It carries ``min_value: '0'`` and ``max_value: '1000'`` but **no** ``step`` and
#: no ``decimals``: those bounds are character-count limits on a line edit, not
#: slider bounds. Its default is the empty string. Typing it as an int the way
#: the "min/max without decimals" rule would is not merely wrong, it crashes the
#: generator -- ``int(float(''))`` raises. It is a str, and it is empty.
TEXT_KEYS = frozenset({"ClipText"})

#: The one key whose layout ``default`` *and* ``options`` are callables
#: (``get_dfm_models_default_value`` / ``get_dfm_models_selection_values``, in
#: ``app/helpers/miscellaneous.py``, deliberately not vendored). They scan a
#: models directory the engine does not own, so the key resolves to the empty
#: string. Plan 02-04 needs to know it starts empty rather than absent.
CALLABLE_DEFAULT_KEYS = frozenset({"DFMModelSelection"})

#: Measured from the layout dicts during planning: string defaults that must have
#: survived coercion as real numbers. ``'2.50'`` in particular proves the int
#: path goes through ``float()`` first rather than ``int('2.50')``, which raises.
TYPED_SPOT_CHECKS = {
    "ClipAmountSlider": (50, int),
    "SimilarityThresholdSlider": (60, int),
    "FaceEditorCropScaleDecimalSlider": (2.50, float),
}


def value_kind(value) -> str:
    """The fixture's own type name for a value.

    ``bool`` is checked before ``int`` deliberately: ``isinstance(True, int)`` is
    ``True`` in Python, so a census that tests ``int`` first counts all 43
    toggles as integers and the whole check goes quietly green.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


@pytest.fixture(scope="module")
def settings() -> dict:
    assert FIXTURE.is_file(), (
        "{} is missing. Regenerate it on an interpreter with Qt:\n"
        '  "D:/Visomaster/dependencies/Python/python.exe" -B '
        "tools/dump_engine_settings.py".format(FIXTURE)
    )
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def every_key(settings) -> dict:
    merged = dict(settings["project"])
    merged.update(settings["global"])
    return merged


def test_fixture_has_exactly_two_tiers(settings):
    assert sorted(settings) == ["global", "project"], (
        "the fixture must have exactly the two top-level members `project` and "
        "`global`; found {}".format(sorted(settings))
    )


def test_tier_key_counts(settings):
    assert len(settings["project"]) == EXPECTED_PROJECT_KEYS
    assert len(settings["global"]) == EXPECTED_GLOBAL_KEYS


def test_tiers_do_not_intersect(settings):
    overlap = sorted(set(settings["project"]) & set(settings["global"]))
    assert not overlap, (
        "a key in both tiers has no defined resolution order: {}".format(overlap)
    )


def test_value_type_census(every_key):
    """Every value's type, counted. Pins the shape rule's outcome as a whole.

    A per-key type assertion is impossible here -- deciding a key's shape needs
    the layout dicts, which need Qt. The census is the checkable shadow of the
    shape rule: if a slider leaks through as a string the str count rises and the
    int count falls, and this fails naming both.
    """
    census = {}
    for value in every_key.values():
        census[value_kind(value)] = census.get(value_kind(value), 0) + 1

    assert census == EXPECTED_TYPE_CENSUS, (
        "value type census changed.\n  expected: {}\n  actual:   {}".format(
            EXPECTED_TYPE_CENSUS, census
        )
    )
    assert sum(census.values()) == EXPECTED_PROJECT_KEYS + EXPECTED_GLOBAL_KEYS


def test_no_slider_value_survived_as_a_string(every_key):
    """The failure this whole fixture exists to prevent, stated directly.

    Any string that parses as a number is a numeric widget whose coercion did not
    happen -- except the four named dropdowns whose options genuinely are numeric
    strings.
    """
    leaked = []
    for name, value in every_key.items():
        if not isinstance(value, str) or name in NUMERIC_LOOKING_SELECTIONS:
            continue
        try:
            float(value)
        except ValueError:
            continue
        leaked.append("{} = {!r}".format(name, value))

    assert not leaked, (
        "numeric values left as strings -- these fail inside inference with a "
        "TypeError, not at load:\n  " + "\n  ".join(leaked)
    )


def test_named_numeric_selections_are_still_strings(every_key):
    """The exemption above must stay an exemption, not become a blanket.

    If one of these four ever arrives as an int, the dropdown comparison against
    its string ``options`` list stops matching and the setting silently falls
    back to whatever the engine does with an unknown value.
    """
    for name in NUMERIC_LOOKING_SELECTIONS:
        assert name in every_key, "{} vanished from the fixture".format(name)
        assert isinstance(every_key[name], str), (
            "{} must stay a string: it is compared against a string options "
            "list, not used as a number. Got {!r}".format(name, every_key[name])
        )


def test_callable_defaults_resolve_to_empty_string(settings):
    for name in CALLABLE_DEFAULT_KEYS:
        assert name in settings["project"], (
            "{} must be present rather than dropped -- plan 02-04 needs to know "
            "it starts empty, and absent and empty are different facts.".format(name)
        )
        assert settings["project"][name] == "", (
            "{} resolves against a models directory the engine does not own, so "
            "it must be the empty string. Got {!r}".format(
                name, settings["project"][name]
            )
        )


def test_text_keys_are_empty_strings(settings):
    for name in TEXT_KEYS:
        assert name in settings["project"], "{} is missing".format(name)
        value = settings["project"][name]
        assert isinstance(value, str) and value == "", (
            "{} is a line edit whose min/max are character bounds, not slider "
            "bounds. It must be the empty string, never an int. Got {!r}".format(
                name, value
            )
        )


@pytest.mark.parametrize("name", sorted(TYPED_SPOT_CHECKS))
def test_measured_defaults_are_typed(every_key, name):
    expected, expected_type = TYPED_SPOT_CHECKS[name]
    assert name in every_key, "{} is missing from the fixture".format(name)
    value = every_key[name]
    assert value_kind(value) == expected_type.__name__, (
        "{} should be {} but is {} ({!r})".format(
            name, expected_type.__name__, value_kind(value), value
        )
    )
    assert value == expected


def test_fixture_is_sorted_with_a_trailing_newline():
    """So a regeneration diffs as a value change rather than a reordering.

    Python dicts preserve insertion order and ``json.dump`` writes that order, so
    without ``sort_keys`` the file's layout depends on the merge order of three
    layout modules -- and every regeneration after an upstream edit would show up
    as a whole-file churn that hides the one value that actually moved.
    """
    raw = FIXTURE.read_text(encoding="utf-8")
    assert raw.endswith("\n"), "the fixture must end with a trailing newline"

    loaded = json.loads(raw)
    for tier in ("project", "global"):
        keys = list(loaded[tier])
        assert keys == sorted(keys), (
            "{} tier is not sorted -- regenerate with sort_keys=True".format(tier)
        )
