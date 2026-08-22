"""Named tier payloads: seed them, list them, apply one to a project.

The two presets are VisoMaster's two saved profiles, migrated once by
``tools/migrate_profiles.py`` and committed as
``visoswap/settings/data/presets_seed.json``. The source file lives on one
developer's disk; the seed ships. Nothing here reads the source file and nothing
here converts a type -- the committed seed is already typed, and re-coercing it
would put a second typing rule in the tree, which is the specific thing the
migration exists to avoid.

Applying a preset writes **overrides only**
-------------------------------------------
A preset holds all 201 keys, but applying it writes only the ones whose value
differs from the schema default, and it **clears** any existing override for a
key where the preset agrees with the default.

Writing all 201 would work and would be wrong. Each tier means "the keys this
level disagrees about"; fill it with a complete copy and inheritance stops
meaning anything, because every value is then pinned at the most specific level
that has ever been touched. The consequence of doing it correctly is worth
stating plainly, because it is a behaviour and not an implementation detail:
**a later change to a schema default propagates into a project that took a
preset**, for exactly the keys that project never disagreed about. That is what
an override model is for. It is written down in ``docs/settings-presets.md``
rather than left to be discovered by whoever first notices a value moved.

Every write goes through ``store``
----------------------------------
``store.set_project`` and ``store.set_global``, never SQL of its own. Preset
application is a write like any other and is held to the same validation, the
same tier check and the same JSON encoding; a private INSERT here would be a
side door around all three, and it would be the door nobody remembers to close.
``tests/test_presets_seed.py`` asserts against the source of this file that no
such INSERT exists.

The one key whose legal values are a directory listing
------------------------------------------------------
``DFMModelSelection``'s options are the DFM model files on disk, so a preset can
carry a value that is perfectly valid on the machine it was saved on and not an
option on the machine applying it. That is not corruption -- it is the same
distinction ``store.validate_stored`` already draws: "the file this names is not
here" and "this was never allowed" are different facts. Such a key is skipped,
its stale override cleared, and its name **returned in the report** rather than
raising or being swallowed.

Standard library only. ``json``, ``sqlite3``, ``datetime``, ``pathlib``.
"""

import datetime
import json
import logging
import sqlite3
from pathlib import Path

from visoswap import schema
from visoswap.settings import store

LOGGER = logging.getLogger(__name__)

__all__ = [
    "SEED_PATH",
    "UnknownPreset",
    "load_seed",
    "seed_presets",
    "list_presets",
    "get_preset",
    "apply_preset",
]

#: The committed seed. Inside the package on purpose: a wheel that does not
#: carry it is a wheel whose presets table can only be filled from a file the
#: user does not have.
SEED_PATH = Path(__file__).resolve().parent / "data" / "presets_seed.json"

#: The two tiers a preset carries, and the store call that writes each. The
#: face tier is absent by design -- a preset is a starting point for a project,
#: and a face override is a decision about one person in one video.
_WRITERS = {
    "project": (store.set_project, store.clear_project),
    "global": (store.set_global, store.clear_global),
}


class UnknownPreset(KeyError):
    """Raised for a preset id that is not in the table.

    A ``KeyError`` subclass so ``except KeyError`` still catches it, named so a
    caller can tell "no such preset" from any other missing key.
    """


def _timestamp(unix_seconds):
    """A Unix timestamp as the text SQLite's own ``datetime('now')`` produces.

    The seed carries the source file's raw integers; the table's columns are
    TEXT and every other ``updated_at`` in this schema was written by
    ``datetime('now')``. Storing two spellings of a time in one column is how a
    sort silently stops working.
    """
    moment = datetime.datetime.fromtimestamp(
        unix_seconds, tz=datetime.timezone.utc
    )
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _encode_payload(values):
    # Sorted so two encodings of the same payload compare equal as text, which
    # is what makes re-seeding able to say "unchanged" without decoding.
    return json.dumps(values, sort_keys=True)


def load_seed(path=None):
    """The committed presets, in file order. Types are the file's, unaltered."""
    source = Path(path) if path is not None else SEED_PATH
    with source.open("r", encoding="utf-8") as handle:
        return json.load(handle)["presets"]


def seed_presets(connection: sqlite3.Connection, path=None) -> dict:
    """Insert the committed presets, idempotently.

    Running twice leaves two rows, not four, and does not rewrite a row that
    already matches -- ``updated_at`` is the source profile's own, so touching
    an unchanged row would move a timestamp that means something.

    Returns ``{"inserted": n, "updated": n, "unchanged": n}``.
    """
    report = {"inserted": 0, "updated": 0, "unchanged": 0}
    for preset in load_seed(path):
        row = (
            preset["name"],
            _encode_payload(preset["project"]),
            _encode_payload(preset["global"]),
            _timestamp(preset["created"]),
            _timestamp(preset["updated"]),
        )
        existing = connection.execute(
            "SELECT name, project_values, global_values, created_at, updated_at "
            "FROM setting_presets WHERE id = ?",
            (preset["id"],),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO setting_presets "
                "(id, name, project_values, global_values, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (preset["id"],) + row,
            )
            report["inserted"] += 1
        elif tuple(existing) == row:
            report["unchanged"] += 1
        else:
            connection.execute(
                "UPDATE setting_presets SET name = ?, project_values = ?, "
                "global_values = ?, created_at = ?, updated_at = ? WHERE id = ?",
                row + (preset["id"],),
            )
            report["updated"] += 1
    return report


def _row_to_preset(row):
    return {
        "id": row[0],
        "name": row[1],
        "project": json.loads(row[2]),
        "global": json.loads(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
    }


_SELECT = (
    "SELECT id, name, project_values, global_values, created_at, updated_at "
    "FROM setting_presets"
)


def list_presets(connection: sqlite3.Connection):
    """Every preset, payloads decoded, oldest first.

    Ordered by ``created_at`` rather than by id: the ids are opaque hex and
    sorting by them would present the two profiles in an order that means
    nothing to the person who saved them.
    """
    return [
        _row_to_preset(row)
        for row in connection.execute(_SELECT + " ORDER BY created_at, id")
    ]


def get_preset(connection: sqlite3.Connection, preset_id):
    """One preset, payloads decoded, or ``UnknownPreset``."""
    row = connection.execute(_SELECT + " WHERE id = ?", (preset_id,)).fetchone()
    if row is None:
        raise UnknownPreset(preset_id)
    return _row_to_preset(row)


def _is_default(value, default):
    """Same type and same value. ``type(...) is`` rather than ``isinstance``:
    ``True == 1`` in Python, and a toggle that compares equal to a slider
    position is how a bool gets stored where an int belongs."""
    return type(value) is type(default) and value == default


def _is_available(key, value, models_dir):
    """False only for a value whose option list is a live directory listing.

    200 of the 201 keys return True here without a filesystem question being
    asked. The exception is the DFM model selection, and its answer is about the
    machine rather than about the value.
    """
    entry = schema.entry(key)
    if entry.get("options_from") is None:
        return True
    return value in schema.resolved_entry(key, models_dir)["options"]


def apply_preset(
    connection: sqlite3.Connection, preset_id, project_id, models_dir=None
) -> dict:
    """Apply a preset to ``project_id``. Overrides only -- see the module docstring.

    Returns a report: how many keys were written and how many stale overrides
    were cleared at each tier, plus the names of any keys skipped because the
    value they carry is not currently an available option.

    The caller owns the transaction. Nothing here commits, so a failed write
    leaves the project with the settings it had rather than with half a preset.
    """
    preset = get_preset(connection, preset_id)
    report = {
        "project_written": 0,
        "project_cleared": 0,
        "global_written": 0,
        "global_cleared": 0,
        "unavailable": [],
    }

    for tier, (setter, clearer) in _WRITERS.items():
        for key in sorted(preset[tier]):
            value = preset[tier][key]
            default = schema.effective_default(key, models_dir)
            unavailable = not _is_available(key, value, models_dir)
            if unavailable:
                LOGGER.info(
                    "preset %s carries %s=%r, which is not an option on this "
                    "machine -- skipped, and any stale override cleared",
                    preset_id,
                    key,
                    value,
                )
                report["unavailable"].append(key)
            if unavailable or _is_default(value, default):
                if tier == "project":
                    cleared = clearer(connection, project_id, key)
                else:
                    cleared = clearer(connection, key)
                report[tier + "_cleared"] += int(cleared)
                continue
            if tier == "project":
                setter(connection, project_id, key, value, models_dir)
            else:
                setter(connection, key, value, models_dir)
            report[tier + "_written"] += 1

    return report
