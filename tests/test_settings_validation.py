"""Wrong values are refused at the boundary, and the rules come from the schema.

The failure this file exists to prevent is not a crash. It is a value that is
accepted, persisted, resolved, handed to the swap pipeline, and only then found to
be a string where a tensor operation wanted a float -- at which point the
traceback points at the inference code and the actual mistake is three layers and
one process boundary away.

Two entry points, deliberately separated:

* **strict** is for everything that arrives from a caller. It accepts a value only
  if it already has the type the schema declares. A string is a rejection, not an
  input to be repaired.
* **lenient** is for migration, and has exactly one intended caller in the whole
  project: plan 03-03's preset seeding, because every value in ``profiles.json``
  is a string. It coerces by shape and then runs the strict validator on the
  result.

Conflating the two is how the string round-tripping this phase exists to end
comes quietly back, so the split is asserted here rather than merely documented.
"""

import json
import re
import sqlite3

import pytest

from visoswap import schema
from visoswap.settings import db, store, validate

#: One key of each shape, chosen by querying the schema rather than by being
#: remembered, so a key being retyped upstream moves this file with it.
INT_KEY = "SimilarityThresholdSlider"
FLOAT_KEY = "ColorBrightnessDecimalSlider"
TOGGLE_KEY = "ColorEnableToggle"
SELECTION_KEY = "SwapModelSelection"
TEXT_KEY = "ClipText"
DYNAMIC_KEY = schema.DYNAMIC_KEY

#: Measured on ``D:/Visomaster`` through the four ``*_layout_data`` dicts on the
#: Qt interpreter: the **raw** default upstream holds, before any coercion.
#: Spans all five shapes and both tiers.
#:
#: Inline rather than imported. Three of the four layout modules reach PySide6 on
#: import, so nothing in the test suite can read them -- that is the whole reason
#: the schema is generated offline and committed. A table of nine measured pairs
#: is the honest way to assert the migration path and the generation path cannot
#: disagree.
MEASURED_RAW_DEFAULTS = (
    ("SimilarityThresholdSlider", "60"),
    ("StrengthAmountSlider", "100"),
    ("nThreadsSlider", "2"),
    ("ColorBrightnessDecimalSlider", "1.00"),
    ("FaceExpressionVYRatioDecimalSlider", "-0.125"),
    ("ViewFaceMaskEnableToggle", False),
    ("SwapModelSelection", "Inswapper128"),
    ("ThemeSelection", "Dark"),
    ("ClipText", ""),
)


@pytest.fixture()
def connection(tmp_path):
    conn = sqlite3.connect(tmp_path / "project.db")
    db.apply_settings_schema(conn)
    yield conn
    conn.close()


@pytest.fixture()
def models_dir(tmp_path):
    """A models directory holding one DFM model, and a clean listing cache."""
    directory = tmp_path / "model_assets"
    (directory / schema.DFM_SUBDIR).mkdir(parents=True)
    (directory / schema.DFM_SUBDIR / "somebody.dfm").write_bytes(b"not a model")
    schema.clear_dfm_cache()
    yield directory
    schema.clear_dfm_cache()


@pytest.fixture()
def empty_models_dir(tmp_path):
    directory = tmp_path / "empty_assets"
    (directory / schema.DFM_SUBDIR).mkdir(parents=True)
    schema.clear_dfm_cache()
    yield directory
    schema.clear_dfm_cache()


# --------------------------------------------------------------------------
# the premise: the keys this file names still have the shapes it assumes
# --------------------------------------------------------------------------


def test_the_representative_keys_still_have_the_types_this_file_assumes():
    expected = {
        INT_KEY: "int",
        FLOAT_KEY: "float",
        TOGGLE_KEY: "toggle",
        SELECTION_KEY: "selection",
        TEXT_KEY: "text",
        DYNAMIC_KEY: "selection",
    }
    actual = {key: schema.type_of(key) for key in expected}
    assert actual == expected


# --------------------------------------------------------------------------
# unknown keys
# --------------------------------------------------------------------------


def test_an_unknown_key_is_rejected_by_name():
    unknown = "ThisKeyDoesNotExistSlider"
    for entry_point in (validate.validate, validate.coerce):
        with pytest.raises(schema.UnknownSettingsKey) as raised:
            entry_point(unknown, 1)
        assert unknown in str(raised.value), (
            "a rejection that does not name the key is a support conversation"
        )


# --------------------------------------------------------------------------
# types
# --------------------------------------------------------------------------


def test_a_string_is_rejected_by_strict_and_converted_by_lenient():
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(INT_KEY, "60")
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(FLOAT_KEY, "1.0")

    converted = validate.coerce(INT_KEY, "60")
    assert converted == 60
    assert type(converted) is int

    converted = validate.coerce(FLOAT_KEY, "1.00")
    assert converted == 1.0
    assert type(converted) is float


def test_a_bool_is_not_an_int_and_an_int_is_not_a_bool():
    """``isinstance(True, int)`` is True in Python, so the naive check accepts it.

    Both directions matter: a toggle passing as an int puts ``True`` where a
    slider position belongs, and an int passing as a toggle puts ``1`` where the
    engine tests identity against ``True``.
    """
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(INT_KEY, True)
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(INT_KEY, False)
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(FLOAT_KEY, True)
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(TOGGLE_KEY, 1)
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(TOGGLE_KEY, 0)


def test_bools_are_tested_before_ints_across_every_numeric_key():
    """One key proving it is a coincidence; ninety-three and forty-two is a rule."""
    leaked = [
        key
        for key, entry in schema.WIDGETS.items()
        if entry["type"] in ("int", "float")
        and _accepts(key, True)
    ]
    assert not leaked, "these numeric keys accepted a bool: {}".format(leaked)

    leaked = [
        key
        for key, entry in schema.WIDGETS.items()
        if entry["type"] == "toggle" and _accepts(key, 1)
    ]
    assert not leaked, "these toggles accepted an int: {}".format(leaked)


def _accepts(key, value):
    try:
        validate.validate(key, value)
    except (validate.InvalidSettingValue, schema.UnknownSettingsKey):
        return False
    return True


def test_an_int_is_a_perfectly_good_float_and_is_widened():
    """Reject ``1`` for a float key and the frontend has to send ``1.0`` for a
    slider sitting at one, which no renderer does."""
    widened = validate.validate(FLOAT_KEY, 1)
    assert widened == 1.0
    assert type(widened) is float, (
        "the value must come back a float, not survive as the int it arrived as "
        "-- the point of the widening is that what reaches the engine is typed"
    )


def test_a_float_is_not_accepted_for_an_int_key():
    """The widening is deliberately one-way. ``2.5`` for a step count is a
    caller who has not decided what they mean."""
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(INT_KEY, 60.5)


def test_a_toggle_takes_a_bool_and_nothing_that_merely_spells_one():
    for offered in (1, 0, "true", "false", "True", "False", None, 1.0):
        with pytest.raises(validate.InvalidSettingValue):
            validate.validate(TOGGLE_KEY, offered)
    assert validate.validate(TOGGLE_KEY, True) is True
    assert validate.validate(TOGGLE_KEY, False) is False


def test_a_non_string_is_rejected_for_a_selection_and_for_text():
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(SELECTION_KEY, 1)
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(TEXT_KEY, 1)


# --------------------------------------------------------------------------
# bounds
# --------------------------------------------------------------------------


def test_a_number_outside_its_bounds_is_rejected_naming_value_and_bound():
    entry = schema.entry(INT_KEY)
    below = entry["minimum"] - 1
    above = entry["maximum"] + 1

    with pytest.raises(validate.InvalidSettingValue) as raised:
        validate.validate(INT_KEY, below)
    message = str(raised.value)
    assert str(below) in message and str(entry["minimum"]) in message, message

    with pytest.raises(validate.InvalidSettingValue) as raised:
        validate.validate(INT_KEY, above)
    message = str(raised.value)
    assert str(above) in message and str(entry["maximum"]) in message, message


def test_the_bounds_themselves_are_inside_the_range():
    entry = schema.entry(INT_KEY)
    assert validate.validate(INT_KEY, entry["minimum"]) == entry["minimum"]
    assert validate.validate(INT_KEY, entry["maximum"]) == entry["maximum"]


def test_an_off_step_value_is_accepted_not_rejected():
    """A deliberate decision, not an omission.

    ``StrengthAmountSlider`` steps by 25 over 0 to 500 and three makeup sliders
    step by 3 over 0 to 255 -- which does not even reach the maximum. The step is
    a renderer's increment hint and upstream never enforced it, so rejecting an
    off-step value would make legitimate stored values unwritable.
    """
    key = "StrengthAmountSlider"
    entry = schema.entry(key)
    assert entry["step"] == 25, entry
    off_step = 137
    assert off_step % entry["step"] != 0
    assert validate.validate(key, off_step) == off_step


def test_the_step_decision_is_recorded_where_the_next_reader_will_look():
    """A decision made by omission is indistinguishable from a bug."""
    assert "step" in validate.__doc__, (
        "the module docstring does not mention the step rule; the next reader "
        "will assume off-step values are rejected and 'fix' it"
    )


# --------------------------------------------------------------------------
# option lists
# --------------------------------------------------------------------------


def test_a_value_outside_a_literal_option_list_is_rejected():
    entry = schema.entry(SELECTION_KEY)
    assert validate.validate(SELECTION_KEY, entry["options"][-1]) == entry["options"][-1]

    with pytest.raises(validate.InvalidSettingValue) as raised:
        validate.validate(SELECTION_KEY, "NotAModel")
    assert "NotAModel" in str(raised.value)


def test_the_dynamic_key_is_checked_against_the_list_resolved_at_load(models_dir):
    assert validate.validate(DYNAMIC_KEY, "somebody.dfm", models_dir) == "somebody.dfm"
    with pytest.raises(validate.InvalidSettingValue):
        validate.validate(DYNAMIC_KEY, "nobody.dfm", models_dir)


def test_the_dynamic_key_accepts_anything_when_its_list_is_empty(empty_models_dir):
    """A bare checkout has no models directory, and refusing every value for a
    key whose list is empty would make the key unwritable rather than unset."""
    assert validate.validate(DYNAMIC_KEY, "anything.dfm", empty_models_dir) == (
        "anything.dfm"
    )
    assert validate.validate(DYNAMIC_KEY, "", empty_models_dir) == ""


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------


def test_text_is_bounded_by_character_count_not_by_value():
    entry = schema.entry(TEXT_KEY)
    longest = "x" * entry["max_length"]
    assert validate.validate(TEXT_KEY, longest) == longest

    with pytest.raises(validate.InvalidSettingValue) as raised:
        validate.validate(TEXT_KEY, longest + "x")
    message = str(raised.value)
    assert str(entry["max_length"]) in message, message


# --------------------------------------------------------------------------
# the migration path
# --------------------------------------------------------------------------


def test_lenient_conversion_reproduces_the_schemas_typed_defaults():
    """The item that matters most and is easiest to leave out.

    If the migration path and the generation path can disagree, a preset seeded
    from ``profiles.json`` holds a different value from the schema default it
    was supposed to equal, and nothing anywhere says so.
    """
    disagreements = []
    for key, raw_default in MEASURED_RAW_DEFAULTS:
        converted = validate.coerce(key, raw_default)
        expected = schema.default_of(key)
        if converted != expected or type(converted) is not type(expected):
            disagreements.append((key, raw_default, converted, expected))

    assert not disagreements, "\n".join(
        "  {}: raw {!r} -> {!r} ({}), schema holds {!r} ({})".format(
            key, raw, got, type(got).__name__, want, type(want).__name__
        )
        for key, raw, got, want in disagreements
    )


def test_the_measured_table_spans_every_shape_and_both_tiers():
    """Guard against the agreement above passing over four ints."""
    shapes = {schema.type_of(key) for key, _ in MEASURED_RAW_DEFAULTS}
    assert shapes == {"int", "float", "toggle", "selection", "text"}, shapes
    tiers = {schema.tier_of(key) for key, _ in MEASURED_RAW_DEFAULTS}
    assert tiers == {"project", "global"}, tiers


def test_a_decimal_string_in_an_integral_key_survives_via_the_float_hop():
    """``int('2.50')`` raises. The lenient path goes through ``float`` first.

    No key in the measured 201 actually carries a decimal string default today
    -- that was checked, and the count is zero -- but ``profiles.json`` is
    hand-written and hand-edited, which is the input this path exists for.
    """
    assert validate.coerce(INT_KEY, "60.0") == 60
    assert type(validate.coerce(INT_KEY, "60.0")) is int


def test_the_lenient_path_still_validates_after_converting():
    """Lenient about the type it was handed, not about the value it produced."""
    with pytest.raises(validate.InvalidSettingValue):
        validate.coerce(INT_KEY, "5000")
    with pytest.raises(validate.InvalidSettingValue):
        validate.coerce(SELECTION_KEY, "NotAModel")
    with pytest.raises(validate.InvalidSettingValue):
        validate.coerce(INT_KEY, "not a number")


def test_lenient_conversion_is_documented_as_having_exactly_one_caller():
    assert "migration" in validate.__doc__.lower(), validate.__doc__


def test_nothing_outside_the_migration_path_calls_the_lenient_converter():
    """The rule stated in the docstring, enforced against the tree.

    A second caller is not a style problem. It is the string round-tripping this
    phase exists to end, re-established one convenience at a time.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in list((root / "visoswap").rglob("*.py")) + list(
        (root / "backend").rglob("*.py")
        if (root / "backend").is_dir()
        else []
    ):
        if path.name == "validate.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bvalidate\.coerce\b|\bfrom .*validate import .*coerce", text):
            offenders.append(path.relative_to(root).as_posix())
    assert not offenders, (
        "the lenient converter is called outside migration: {}".format(offenders)
    )


# --------------------------------------------------------------------------
# every default validates against its own entry
# --------------------------------------------------------------------------


def test_every_schema_default_passes_its_own_validation():
    """201 keys. A default that its own rules reject is a schema that is wrong
    about itself, and it would only surface the first time somebody reset a
    control."""
    rejected = []
    for key in schema.WIDGETS:
        default = schema.effective_default(key)
        try:
            round_tripped = validate.validate(key, default)
        except validate.InvalidSettingValue as error:
            rejected.append((key, default, str(error)))
            continue
        if round_tripped != default or type(round_tripped) is not type(default):
            rejected.append((key, default, "round-tripped to {!r}".format(round_tripped)))
    assert not rejected, "\n".join(
        "  {}: {!r} -- {}".format(*row) for row in rejected
    )


# --------------------------------------------------------------------------
# the store boundary
# --------------------------------------------------------------------------


def test_every_store_write_path_validates(connection):
    """Global, project and face alike. One unguarded path is the whole hole."""
    global_key = next(
        k for k, e in schema.WIDGETS.items() if e["tier"] == "global" and e["type"] == "int"
    )

    with pytest.raises(validate.InvalidSettingValue):
        store.set_global(connection, global_key, "2")
    with pytest.raises(validate.InvalidSettingValue):
        store.set_project(connection, "project-a", INT_KEY, "60")
    with pytest.raises(validate.InvalidSettingValue):
        store.set_face(connection, "project-a", "face-1", INT_KEY, "60")
    with pytest.raises(validate.InvalidSettingValue):
        store.set_project(connection, "project-a", INT_KEY, 5000)

    for table in ("global_settings", "project_settings", "face_settings"):
        count = connection.execute(
            "SELECT count(*) FROM {}".format(table)
        ).fetchone()[0]
        assert count == 0, "a refused write still touched {}".format(table)


def test_a_write_stores_the_validated_value_not_the_offered_one(connection):
    """The int-to-float widening has to survive into the database, or the
    widening happened for nothing."""
    store.set_project(connection, "project-a", FLOAT_KEY, 1)
    raw = connection.execute(
        "SELECT value FROM project_settings WHERE key = ?", (FLOAT_KEY,)
    ).fetchone()[0]
    assert json.loads(raw) == 1.0
    assert type(json.loads(raw)) is float, raw
    assert type(store.resolve(connection, FLOAT_KEY, "project-a")) is float


@pytest.mark.parametrize(
    "tier, insert",
    [
        (
            "global",
            "INSERT INTO global_settings (key, value) VALUES (?, ?)",
        ),
        (
            "project",
            "INSERT INTO project_settings (project_id, key, value) "
            "VALUES ('project-a', ?, ?)",
        ),
        (
            "face",
            "INSERT INTO face_settings (project_id, face_key, key, value) "
            "VALUES ('project-a', 'face-1', ?, ?)",
        ),
    ],
)
def test_a_stored_row_that_fails_validation_names_the_tier_it_sits_in(
    connection, tier, insert
):
    """On a read, a failure means the stored data is already wrong.

    That happens through a migration or a hand edit, and knowing which tier the
    offending row is in is the whole diagnosis -- the value itself says nothing
    about where it came from.
    """
    # An *int* key at each tier, deliberately. Taking the first global key of
    # any shape works today only because that key happens to be a toggle, which
    # also refuses a string; it would stop meaning anything the day a text key
    # sorted first, because a text key accepts the payload below quite happily.
    key = INT_KEY if tier != "global" else next(
        k
        for k, e in schema.WIDGETS.items()
        if e["tier"] == "global" and e["type"] == "int"
    )
    connection.execute(insert, (key, json.dumps("not an int")))

    with pytest.raises(store.CorruptStoredSetting) as raised:
        store.resolve(connection, key, "project-a", "face-1")
    message = str(raised.value)
    assert tier in message, message
    assert key in message, message

    with pytest.raises(store.CorruptStoredSetting):
        store.resolve_all(connection, "project-a", "face-1")


def test_a_stored_selection_whose_model_file_vanished_is_not_corruption(
    connection, models_dir, empty_models_dir
):
    """Membership is enforced on the way in and not on the way out.

    The one dynamic key's option list is a directory listing, so it changes
    without anyone editing a setting. If a deleted model file made the stored
    value fail validation on read, and resolution reads the whole tier at once,
    one removed file would take all 201 settings down with it -- and the message
    would say the database was corrupt, which it would not be.

    "The file this names is gone" and "this value was never allowed" are
    different facts. Only the second is corruption, and the first is diagnosable
    where a model is loaded rather than where a slider is read.
    """
    store.set_project(connection, "project-a", DYNAMIC_KEY, "somebody.dfm", models_dir)

    # Same value, read back against a directory that no longer holds the file.
    assert (
        store.resolve(connection, DYNAMIC_KEY, "project-a", models_dir=empty_models_dir)
        == "somebody.dfm"
    )
    resolved = store.resolve_all(connection, "project-a", models_dir=empty_models_dir)
    assert resolved[DYNAMIC_KEY] == "somebody.dfm"

    # The type is still enforced in both directions; only membership relaxes.
    connection.execute(
        "UPDATE project_settings SET value = ? WHERE key = ?",
        (json.dumps(7), DYNAMIC_KEY),
    )
    with pytest.raises(store.CorruptStoredSetting):
        store.resolve(connection, DYNAMIC_KEY, "project-a", models_dir=empty_models_dir)


def test_a_value_that_was_never_a_model_is_still_refused_on_the_way_in(
    connection, models_dir
):
    """The relaxation above is on the read side only, and this is the proof."""
    with pytest.raises(validate.InvalidSettingValue):
        store.set_project(
            connection, "project-a", DYNAMIC_KEY, "nobody.dfm", models_dir
        )


def test_a_corrupt_row_is_still_an_invalid_setting_value(connection):
    """The read-side error subclasses the write-side one, so a caller that only
    knows about invalid values still catches it."""
    assert issubclass(store.CorruptStoredSetting, validate.InvalidSettingValue)


# --------------------------------------------------------------------------
# the rules live in the schema, and only there
# --------------------------------------------------------------------------


def test_no_settings_key_appears_as_a_literal_in_the_validator():
    """A second hardcoded rule here is the type moving back out of the data."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "visoswap"
        / "settings"
        / "validate.py"
    ).read_text(encoding="utf-8")
    hits = sorted(
        key
        for key in schema.WIDGETS
        if re.search(r"[\"']" + re.escape(key) + r"[\"']", source)
    )
    assert not hits, hits


def test_the_validator_reads_no_type_information_of_its_own():
    """Every type name it knows must come from the schema's own vocabulary."""
    declared = {entry["type"] for entry in schema.WIDGETS.values()}
    assert declared == {"int", "float", "toggle", "selection", "text"}, declared
    assert set(validate.VALIDATORS) == declared, (
        "the validator handles {} but the schema declares {}".format(
            sorted(validate.VALIDATORS), sorted(declared)
        )
    )
