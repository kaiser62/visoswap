"""Three-tier resolution, proven end to end on one key against a real database.

``SimilarityThresholdSlider`` is the key that demonstrates the entire point of
Phase 3. Its layout default is the **string** ``'60'`` and its schema default is
the **int** ``60``, it is a project-tier key so it can be overridden per face, and
it is one of the settings a user actually reaches for. If the type survives this
round trip, the mechanism works.

The type assertions are deliberately ``type(x) is int`` rather than ``x == 60``.
The regression this phase exists to prevent is a value that looks right and is
typed wrong, and ``'60' == 60`` is False in Python only by luck of which side of
the comparison the string landed on -- ``json.loads('"60"')`` gives back a string
that prints identically in a failure message. Assert the type explicitly or the
test cannot see the bug it was written for.

Runs on the plain developer interpreter: standard library, pytest, and
``visoswap.settings``, which imports neither torch nor Qt.
"""

import json
import sqlite3

import pytest

from visoswap import schema
from visoswap.settings import db, store

KEY = "SimilarityThresholdSlider"


@pytest.fixture()
def connection(tmp_path):
    """A real on-disk SQLite database with the settings DDL applied.

    On disk rather than ``:memory:`` on purpose: an in-memory database never
    exercises the file-backed path Phase 4 will actually run, and column
    affinity and JSON round-tripping are precisely the things that could differ.

    ``PRAGMA foreign_keys`` is left **off**. Three tables reference
    ``projects(id)``, which belongs to the backend and does not exist here;
    SQLite resolves foreign keys at DML time, so leaving the pragma alone is what
    lets Phase 3 be tested without a stub of Phase 4's table. See
    ``visoswap/settings/db.py`` for why that is a decision and not an oversight.
    """
    conn = sqlite3.connect(tmp_path / "project.db")
    db.apply_settings_schema(conn)
    yield conn
    conn.close()


def test_the_key_this_test_is_about_is_a_typed_project_key():
    """Guard the premise. If this key stops being an int, the rest proves nothing."""
    entry = schema.entry(KEY)
    assert entry["type"] == "int", entry
    assert entry["tier"] == "project", entry
    assert entry["default"] == 60, entry
    assert type(entry["default"]) is int, (
        "the schema default is {!r}; upstream's layout default is the string "
        "'60' and the whole phase is about that no longer being what reaches the "
        "engine".format(entry["default"])
    )


def test_one_key_resolves_through_all_three_tiers_in_both_directions(connection):
    """The tracer: nothing stored, project, face, and back down again."""
    project_id = "project-a"
    face_key = "face-1"

    def current():
        return store.resolve(connection, KEY, project_id, face_key)

    # 1. Nothing stored anywhere: the schema default, typed.
    assert current() == 60
    assert type(current()) is int

    # 2. A project override wins over the default.
    store.set_project(connection, project_id, KEY, 20)
    assert current() == 20
    assert type(current()) is int

    # 3. A face override wins over the project override.
    store.set_face(connection, project_id, face_key, KEY, 35)
    assert current() == 35
    assert type(current()) is int

    # 4. Remove the face override: the project value comes back, not the default.
    assert store.clear_face(connection, project_id, face_key, KEY) is True
    assert current() == 20
    assert type(current()) is int

    # 5. Remove the project override: the schema default comes back.
    assert store.clear_project(connection, project_id, KEY) is True
    assert current() == 60
    assert type(current()) is int


def test_a_face_override_does_not_leak_to_another_face(connection):
    """The face tier is keyed by (project, face), and both halves must matter."""
    store.set_project(connection, "project-a", KEY, 20)
    store.set_face(connection, "project-a", "face-1", KEY, 35)

    assert store.resolve(connection, KEY, "project-a", "face-1") == 35
    assert store.resolve(connection, KEY, "project-a", "face-2") == 20
    assert store.resolve(connection, KEY, "project-b", "face-1") == 60
    assert store.resolve(connection, KEY) == 60


def test_absence_of_a_row_is_the_only_way_to_inherit(connection):
    """An override equal to the default is still an override, and clearing is a delete."""
    store.set_project(connection, "project-a", KEY, 60)
    stored = store.resolve(connection, KEY, "project-a")
    assert stored == 60

    row = connection.execute(
        "SELECT value FROM project_settings WHERE project_id = ? AND key = ?",
        ("project-a", KEY),
    ).fetchone()
    assert row is not None, (
        "writing the default value must still create a row -- a store that "
        "optimised it away would make 'set explicitly to the default' and "
        "'inherit' indistinguishable, and a later default change would silently "
        "move a value the user had pinned"
    )

    assert store.clear_project(connection, "project-a", KEY) is True
    assert store.clear_project(connection, "project-a", KEY) is False


def test_values_are_stored_json_encoded_not_as_raw_text(connection):
    """The column holds ``20``, not ``'20'``. That is what preserves the type."""
    store.set_project(connection, "project-a", KEY, 20)
    raw = connection.execute(
        "SELECT value FROM project_settings WHERE project_id = ? AND key = ?",
        ("project-a", KEY),
    ).fetchone()[0]

    assert raw == "20", raw
    assert type(json.loads(raw)) is int


def test_a_toggle_round_trips_as_a_bool_not_as_an_int(connection):
    """``isinstance(True, int)`` is True, so a bool is the easy type to lose."""
    key = "SimilarityThresholdSlider"
    toggle = next(
        k
        for k, e in schema.WIDGETS.items()
        if e["type"] == "toggle" and e["tier"] == "project"
    )
    assert toggle != key

    store.set_project(connection, "project-a", toggle, True)
    value = store.resolve(connection, toggle, "project-a")
    assert value is True
    assert type(value) is bool


def test_an_unknown_key_is_refused_at_every_boundary(connection):
    """T-03-02. A key the schema never heard of is a typo or a stale client."""
    unknown = "ThisKeyDoesNotExistSlider"

    with pytest.raises(schema.UnknownSettingsKey):
        store.set_project(connection, "project-a", unknown, 1)
    with pytest.raises(schema.UnknownSettingsKey):
        store.set_global(connection, unknown, 1)
    with pytest.raises(schema.UnknownSettingsKey):
        store.set_face(connection, "project-a", "face-1", unknown, 1)
    with pytest.raises(schema.UnknownSettingsKey):
        store.resolve(connection, unknown, "project-a")

    for table in ("global_settings", "project_settings", "face_settings"):
        count = connection.execute(
            "SELECT count(*) FROM {}".format(table)
        ).fetchone()[0]
        assert count == 0, "a refused write still touched {}".format(table)


def test_a_key_cannot_be_written_at_a_tier_the_schema_does_not_put_it_in(connection):
    """A global key stored per project would sit there and never be read."""
    global_key = next(k for k, e in schema.WIDGETS.items() if e["tier"] == "global")

    with pytest.raises(store.WrongTier):
        store.set_project(connection, "project-a", global_key, 1)
    with pytest.raises(store.WrongTier):
        store.set_global(connection, KEY, 1)


def test_a_hostile_project_id_round_trips_as_data(connection):
    """T-03-01. Bound parameters, never interpolation.

    The project id, the face key and the settings key all arrive from a caller
    that in Phase 4 is an HTTP handler. This id closes a string literal, ends the
    statement and drops a table; if any statement in the store built SQL by
    concatenation, ``project_settings`` would be gone by the end of this test.
    """
    hostile = "a'); DROP TABLE project_settings; --"
    hostile_face = "f\"; DROP TABLE face_settings; --"

    store.set_project(connection, hostile, KEY, 20)
    store.set_face(connection, hostile, hostile_face, KEY, 35)

    assert store.resolve(connection, KEY, hostile) == 20
    assert store.resolve(connection, KEY, hostile, hostile_face) == 35
    assert store.resolve(connection, KEY, "project-a") == 60

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for expected in db.SETTINGS_TABLES:
        assert expected in tables, "{} was dropped -- SQL was interpolated".format(
            expected
        )

    stored_id = connection.execute(
        "SELECT project_id FROM project_settings"
    ).fetchone()[0]
    assert stored_id == hostile


def test_resolve_all_applies_the_same_chain_to_every_key(connection):
    """The bulk read and the single read must not be able to disagree."""
    store.set_project(connection, "project-a", KEY, 20)
    store.set_face(connection, "project-a", "face-1", KEY, 35)
    global_key = next(
        k
        for k, e in schema.WIDGETS.items()
        if e["tier"] == "global" and e["type"] == "toggle"
    )
    store.set_global(connection, global_key, True)

    resolved = store.resolve_all(connection, "project-a", "face-1")

    assert len(resolved) == len(schema.WIDGETS)
    assert resolved[KEY] == 35
    assert type(resolved[KEY]) is int
    assert resolved[global_key] is True

    for key in (KEY, global_key, "ClipText"):
        assert resolved[key] == store.resolve(connection, key, "project-a", "face-1")


def test_the_ddl_is_idempotent(connection):
    """Applying it twice is how Phase 4's connect sequence will call it."""
    db.apply_settings_schema(connection)
    db.apply_settings_schema(connection)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert set(db.SETTINGS_TABLES) <= tables
