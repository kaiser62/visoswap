"""The two saved profiles, migrated once and committed as typed data.

These tests run against ``visoswap/settings/data/presets_seed.json`` -- the
committed file -- and never against a fresh migration run. The committed file is
what ships; a test that re-ran the migration would prove only that the migration
agrees with itself, and would pass on the one developer machine that still has
``profiles.json`` while saying nothing about what a user gets.

The migration itself is covered from the other side: re-running it must leave the
committed file byte-identical, which is a ``git diff --exit-code`` in the plan's
verification rather than an assertion here.

**Why every test that touches a value passes an empty models directory.** One of
the 201 keys -- the DFM model selection -- has an option list that is a directory
listing, so its set of legal values depends on which files happen to sit on the
machine running the test. Both source profiles store ``''`` for it, which is
upstream's own "no DFM model chosen" value and is legal exactly when the listing
is empty. Pointing the schema at an empty directory makes that key deterministic
instead of machine-dependent, which is the difference between a test that means
something everywhere and one that passes here.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from visoswap import schema
from visoswap.settings import db, presets, store, validate

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The two profiles the source file holds, measured from
#: ``D:/Visomaster/profiles.json`` before anything was written.
SOURCE_IDS = ("6a24b3aa71d7", "55fc87c18307")
SOURCE_NAMES = {"6a24b3aa71d7": "A", "55fc87c18307": "with AUD"}

#: The tier split, measured on both profiles independently of the schema and
#: agreeing with it exactly: 33 options and 168 parameters in each.
PROJECT_COUNT = 168
GLOBAL_COUNT = 33

#: The two keys the source file types **inconsistently between its own two
#: profiles** -- strings in ``A``, real ints in ``with AUD``. They are named
#: here rather than discovered because they are the measured evidence that
#: migration types by shape and not by looking at what a value already is.
INCONSISTENTLY_TYPED = ("SimilarityThresholdSlider", "StrengthAmountSlider")

#: What both profiles actually stored for the pair the old web UI reassigned on
#: every load. The loader forced ``"256"``; both saved profiles say ``"128"``.
STORED_SWAPPER = {"SwapModelSelection": "Inswapper128", "SwapperResSelection": "128"}

#: Schema types for which a ``str`` is always wrong.
NUMERIC_OR_BOOL = frozenset({"int", "float", "toggle"})


@pytest.fixture
def models_dir(tmp_path):
    """An empty models directory, so the one dynamic key is deterministic."""
    schema.clear_dfm_cache()
    directory = tmp_path / "model_assets"
    directory.mkdir()
    yield str(directory)
    schema.clear_dfm_cache()


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    db.apply_settings_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def seed():
    return presets.load_seed()


# --------------------------------------------------------------------------
# the committed file
# --------------------------------------------------------------------------


def test_the_seed_is_committed_inside_the_package():
    """A seed that lives outside the package is a seed a wheel does not carry.

    The whole reason the migration output is committed is that
    ``profiles.json`` exists on exactly one developer's disk, and a user machine
    must never need it.
    """
    assert presets.SEED_PATH.is_file(), presets.SEED_PATH
    assert presets.SEED_PATH.is_relative_to(REPO_ROOT / "visoswap")


def test_the_seed_holds_exactly_the_two_source_profiles(seed):
    assert [preset["id"] for preset in seed] == list(SOURCE_IDS)
    assert {p["id"]: p["name"] for p in seed} == SOURCE_NAMES


def test_each_preset_carries_both_tiers_whole(seed):
    """168 project and 33 global, per preset, matching the schema's own split."""
    for preset in seed:
        assert len(preset["project"]) == PROJECT_COUNT, preset["id"]
        assert len(preset["global"]) == GLOBAL_COUNT, preset["id"]
        assert set(preset["project"]) == set(schema.keys_in_tier("project"))
        assert set(preset["global"]) == set(schema.keys_in_tier("global"))


def test_each_preset_carries_the_source_timestamps(seed):
    for preset in seed:
        assert isinstance(preset["created"], int), preset["id"]
        assert isinstance(preset["updated"], int), preset["id"]
        assert preset["created"] <= preset["updated"]


# --------------------------------------------------------------------------
# typed, not stringly
# --------------------------------------------------------------------------


def test_no_value_is_a_string_where_the_schema_declares_a_number_or_a_bool(seed):
    """The phase's whole claim, applied to 402 migrated values.

    A string here is not cosmetic. It is the value that reaches a tensor
    operation as ``'60'`` and fails several layers away from the file that
    produced it.
    """
    offenders = []
    for preset in seed:
        for tier in ("project", "global"):
            for key, value in preset[tier].items():
                if schema.type_of(key) in NUMERIC_OR_BOOL and isinstance(value, str):
                    offenders.append((preset["id"], tier, key, value))
    assert not offenders, offenders[:5]


def test_the_two_inconsistently_typed_keys_come_out_the_same_type(seed):
    """``with AUD`` stored these as ints and ``A`` as strings. Both are ints now.

    Named explicitly rather than swept, because this pair is the concrete case
    that makes typing-by-shape necessary: a migration that typed a value by
    inspecting what it already was would produce two presets that disagree about
    what a slider is.
    """
    for key in INCONSISTENTLY_TYPED:
        assert schema.type_of(key) == "int", key
        for preset in seed:
            value = preset["project"][key]
            assert type(value) is int, (preset["id"], key, value, type(value))


def test_both_presets_hold_the_swapper_pair_the_source_stored(seed):
    """Not the ``"256"`` the old loader forced into the wrong tier every load."""
    for preset in seed:
        for key, expected in STORED_SWAPPER.items():
            assert preset["project"][key] == expected, (preset["id"], key)


def test_every_seeded_value_passes_strict_validation(models_dir, seed):
    """Strict, not lenient. The seed is data a caller could have sent."""
    rejected = []
    for preset in seed:
        for tier in ("project", "global"):
            for key, value in preset[tier].items():
                try:
                    validate.validate(key, value, models_dir)
                except validate.InvalidSettingValue as error:
                    rejected.append((preset["id"], key, value, str(error)))
    assert not rejected, rejected[:5]


def test_every_seeded_key_sits_in_the_tier_it_was_seeded_into(seed):
    """A key in the wrong tier payload would be written where nothing reads it.

    That is precisely the mistake the removed web-UI reassignment made.
    """
    misplaced = []
    for preset in seed:
        for tier in ("project", "global"):
            for key in preset[tier]:
                if schema.tier_of(key) != tier:
                    misplaced.append((preset["id"], tier, key, schema.tier_of(key)))
    assert not misplaced, misplaced[:5]


# --------------------------------------------------------------------------
# seeding
# --------------------------------------------------------------------------


def _row_count(connection, table):
    return connection.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]


def test_seeding_an_empty_database_inserts_two_rows(connection):
    report = presets.seed_presets(connection)
    assert _row_count(connection, "setting_presets") == 2
    assert report["inserted"] == 2
    assert report["updated"] == 0


def test_seeding_twice_still_leaves_two_rows_and_changes_nothing(connection):
    presets.seed_presets(connection)
    before = connection.execute(
        "SELECT id, name, project_values, global_values, created_at, updated_at "
        "FROM setting_presets ORDER BY id"
    ).fetchall()

    report = presets.seed_presets(connection)

    after = connection.execute(
        "SELECT id, name, project_values, global_values, created_at, updated_at "
        "FROM setting_presets ORDER BY id"
    ).fetchall()
    assert _row_count(connection, "setting_presets") == 2
    assert after == before
    assert report["inserted"] == 0
    assert report["updated"] == 0
    assert report["unchanged"] == 2


def test_listing_returns_the_presets_with_their_payloads_decoded(connection, seed):
    presets.seed_presets(connection)
    listed = presets.list_presets(connection)

    assert [p["id"] for p in listed] == [p["id"] for p in seed]
    for stored, source in zip(listed, seed):
        assert stored["name"] == source["name"]
        assert stored["project"] == source["project"]
        assert stored["global"] == source["global"]
        # Decoded, not raw JSON text: an int that came back a string here is
        # the round-tripping this phase exists to end, reintroduced at the one
        # boundary nobody looks at.
        for key in INCONSISTENTLY_TYPED:
            assert type(stored["project"][key]) is int


def test_an_unknown_preset_id_raises(connection):
    presets.seed_presets(connection)
    with pytest.raises(presets.UnknownPreset):
        presets.get_preset(connection, "no-such-preset")


# --------------------------------------------------------------------------
# application: overrides only
# --------------------------------------------------------------------------


def _differing_keys(preset, tier, models_dir):
    """The keys this preset actually disagrees with the schema default about."""
    differing = []
    for key, value in preset[tier].items():
        default = schema.effective_default(key, models_dir)
        if type(value) is not type(default) or value != default:
            differing.append(key)
    return differing


def test_applying_a_preset_writes_only_the_keys_that_differ_from_the_default(
    connection, models_dir, seed
):
    """Counted rows, not resolved values.

    Asserting only that resolution returns the preset's values would pass just
    as well for a preset that wrote all 201 keys -- and a full copy is the thing
    that quietly destroys inheritance, because a later change to a schema
    default would then never reach a project that had taken a preset.
    """
    presets.seed_presets(connection)
    for preset in seed:
        project_id = "project-{}".format(preset["id"])
        presets.apply_preset(connection, preset["id"], project_id, models_dir)

        expected_project = len(_differing_keys(preset, "project", models_dir))
        written_project = connection.execute(
            "SELECT COUNT(*) FROM project_settings WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        assert written_project == expected_project, (preset["id"], "project")

    # The global tier is not per project, so it is counted once, after the last
    # preset applied, against that preset's own differing set.
    last = seed[-1]
    expected_global = len(_differing_keys(last, "global", models_dir))
    assert _row_count(connection, "global_settings") == expected_global


def test_applying_a_preset_writes_at_least_one_override_somewhere(
    connection, models_dir, seed
):
    """Guards the override-only test against passing for the wrong reason.

    If every preset agreed with every default, "wrote only the differing keys"
    would be satisfied by writing nothing at all, and the counting test above
    would be vacuous.
    """
    total = 0
    for preset in seed:
        total += len(_differing_keys(preset, "project", models_dir))
        total += len(_differing_keys(preset, "global", models_dir))
    assert total > 0


def test_resolving_after_applying_returns_the_presets_own_values(
    connection, models_dir, seed
):
    presets.seed_presets(connection)
    for preset in seed:
        project_id = "project-{}".format(preset["id"])
        presets.apply_preset(connection, preset["id"], project_id, models_dir)

        resolved = store.resolve_parameters(
            connection, project_id, models_dir=models_dir
        )
        assert resolved == preset["project"], preset["id"]

        control = store.resolve_control(connection, models_dir=models_dir)
        assert control == preset["global"], preset["id"]


def test_applying_a_preset_clears_an_override_it_agrees_with_the_default_about(
    connection, models_dir, seed
):
    """Taking a preset must leave the project holding the preset's overrides and
    nobody else's -- an override left behind from before is a value the user
    can no longer see the source of."""
    presets.seed_presets(connection)
    preset = seed[0]
    project_id = "leftover"

    # A key the preset agrees with the default about, pre-overridden to
    # something else entirely.
    agreed = [
        key
        for key in preset["project"]
        if key not in _differing_keys(preset, "project", models_dir)
        and schema.type_of(key) == "toggle"
    ]
    assert agreed, "no agreed toggle to test with"
    key = agreed[0]
    store.set_project(
        connection,
        project_id,
        key,
        not schema.effective_default(key, models_dir),
        models_dir,
    )
    assert (
        store.get_override(connection, key, project_id, models_dir=models_dir)
        is not None
    )

    presets.apply_preset(connection, preset["id"], project_id, models_dir)

    assert store.get_override(connection, key, project_id, models_dir=models_dir) is None
    assert (
        store.resolve(connection, key, project_id, models_dir=models_dir)
        == preset["project"][key]
    )


def test_application_goes_through_the_store_and_not_around_it():
    """A second write path is a second set of rules, and the second one is
    always the one without validation."""
    source = (REPO_ROOT / "visoswap" / "settings" / "presets.py").read_text(
        encoding="utf-8"
    )
    assert "INSERT INTO project_settings" not in source
    assert "INSERT INTO global_settings" not in source
    assert "INSERT INTO face_settings" not in source


def test_the_seed_file_is_sorted_and_newline_terminated():
    """So a regeneration diffs as the value that moved, never as a reordering."""
    raw = presets.SEED_PATH.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    document = json.loads(raw)
    assert (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n" == raw
    )
