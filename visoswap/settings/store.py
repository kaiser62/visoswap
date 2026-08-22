"""Read and write settings across the three tiers.

Resolution order, most specific first: **face, project, global, schema default.**

The chain is unambiguous because the two tiers in the schema share no keys. That
is measured across all four layout dicts -- 168 project, 33 global, zero overlap
-- and it is not an assumption:
``tests/test_schema_generated.py::test_the_two_tiers_do_not_intersect`` is what
keeps it true as upstream changes. A project-tier key can therefore never carry a
global override, and a global-tier key can never carry a project or face one, so
no tier can shadow another in a way that depends on which was written first.

Each tier stores **only overrides**. Absence of a row means "inherit", and there
is no sentinel value that also means it -- an override to the same value as the
default is a real override, and clearing it is a delete.

Every value is JSON-encoded on write and decoded on read, so an int comes back an
int. That single fact is why this exists: today a value set in the UI reaches the
engine as the string it was rendered from, and the engine fails on it deep inside
a tensor operation rather than at the boundary that accepted it.

Gating is **not** consulted here. A gated key whose parent toggle is off still
resolves to a value, because the engine reads ``parameters[key]`` unconditionally
and reads the parent toggle separately; the gate in the schema tells a renderer
what to hide and nothing more. Filtering values by gate state here would change
engine behaviour.

**Every write and every read passes through ``validate``** -- the global tier,
the project tier and the face tier alike, and one unguarded path is the whole
hole. On a write a rejection means nothing was stored. On a read it means the
row was already wrong before this process started, which since writes are
guarded can only mean a migration or a hand edit; ``CorruptStoredSetting`` names
the tier so the two are distinguishable.

Every statement binds its parameters. None interpolates a project id, a face key
or a settings key into SQL text (T-03-01).
"""

import json
import sqlite3

from visoswap import schema
from visoswap.settings import validate

__all__ = [
    "resolve",
    "resolve_all",
    "resolve_parameters",
    "resolve_control",
    "get_override",
    "set_global",
    "set_project",
    "set_face",
    "clear_global",
    "clear_project",
    "clear_face",
    "CorruptStoredSetting",
    "UnknownSettingsKey",
    "WrongTier",
]

UnknownSettingsKey = schema.UnknownSettingsKey


class CorruptStoredSetting(validate.InvalidSettingValue):
    """Raised when a value already in the database fails validation on read.

    Distinct from the write-side rejection because the two mean opposite things.
    A rejection on write is the boundary working: the caller offered something
    wrong and nothing was stored. A rejection on **read** means the wrong value
    is already on disk -- and since every write path validates, it can only have
    arrived through a migration or a hand edit of the database. The message
    names the tier the offending row sits in, because the value itself says
    nothing about where it came from and the tier is the whole diagnosis.

    It subclasses the write-side error so a caller that only knows about invalid
    values still catches it.
    """


class WrongTier(ValueError):
    """Raised when a key is written at a tier the schema does not put it in.

    A global-tier key written into a project's overrides would sit there and
    never be read, because resolution consults the tiers the schema assigns.
    Silently accepting it is how a setting appears to save and then does not
    apply -- the failure mode this phase exists to remove.
    """


def _encode(value):
    return json.dumps(value)


def _decode(raw):
    return json.loads(raw)


def _decode_checked(raw, key, tier, models_dir=None):
    """Decode one stored row and hold it to the same rules a write is held to.

    Reads validate as well as writes because the check was not always here: a
    row written by plan 03-03's migration, by an older build, or by hand has
    never passed the write-side gate, and a value that is wrong on disk is
    exactly as damaging as one that is wrong in flight -- more so, because it is
    persistent.
    """
    value = _decode(raw)
    try:
        return validate.validate_stored(key, value, models_dir)
    except validate.InvalidSettingValue as error:
        raise CorruptStoredSetting(
            "the {} tier holds a value for {} that the schema refuses: {}".format(
                tier, key, error
            )
        ) from None


def _check_key(key, required_tier=None):
    """Reject an unknown key, and optionally one written at the wrong tier."""
    entry = schema.entry(key)
    if required_tier is not None and entry["tier"] != required_tier:
        raise WrongTier(
            "{} is a {} key; it cannot be stored at the {} tier".format(
                key, entry["tier"], required_tier
            )
        )
    return entry


def _scalar(connection: sqlite3.Connection, sql, params):
    row = connection.execute(sql, params).fetchone()
    return row


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def set_global(connection: sqlite3.Connection, key, value, models_dir=None) -> None:
    """Store a global-tier override."""
    _check_key(key, "global")
    value = validate.validate(key, value, models_dir)
    connection.execute(
        "INSERT INTO global_settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (key, _encode(value)),
    )


def set_project(
    connection: sqlite3.Connection, project_id, key, value, models_dir=None
) -> None:
    """Store a project-tier override."""
    _check_key(key, "project")
    value = validate.validate(key, value, models_dir)
    connection.execute(
        "INSERT INTO project_settings (project_id, key, value, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(project_id, key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (project_id, key, _encode(value)),
    )


def set_face(
    connection: sqlite3.Connection, project_id, face_key, key, value, models_dir=None
) -> None:
    """Store a face-tier override -- the most specific tier there is."""
    _check_key(key, "project")
    value = validate.validate(key, value, models_dir)
    connection.execute(
        "INSERT INTO face_settings (project_id, face_key, key, value, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(project_id, face_key, key) DO UPDATE SET "
        "value = excluded.value, updated_at = excluded.updated_at",
        (project_id, face_key, key, _encode(value)),
    )


def clear_global(connection: sqlite3.Connection, key) -> bool:
    """Remove a global override. Returns True if there was one."""
    _check_key(key)
    cursor = connection.execute("DELETE FROM global_settings WHERE key = ?", (key,))
    return cursor.rowcount > 0


def clear_project(connection: sqlite3.Connection, project_id, key) -> bool:
    """Remove a project override. Returns True if there was one."""
    _check_key(key)
    cursor = connection.execute(
        "DELETE FROM project_settings WHERE project_id = ? AND key = ?",
        (project_id, key),
    )
    return cursor.rowcount > 0


def clear_face(connection: sqlite3.Connection, project_id, face_key, key) -> bool:
    """Remove a face override. Returns True if there was one."""
    _check_key(key)
    cursor = connection.execute(
        "DELETE FROM face_settings WHERE project_id = ? AND face_key = ? AND key = ?",
        (project_id, face_key, key),
    )
    return cursor.rowcount > 0


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


def get_override(
    connection, key, project_id=None, face_key=None, tier="project", models_dir=None
):
    """One tier's stored value, decoded and validated, or ``None`` when no row exists.

    ``None`` here means "no override", not "an override whose value is null" --
    no schema default is null except the dynamic key's, and nothing writes null.

    A row that fails validation raises ``CorruptStoredSetting`` naming ``tier``.
    """
    _check_key(key)
    if tier == "global":
        row = _scalar(
            connection, "SELECT value FROM global_settings WHERE key = ?", (key,)
        )
    elif tier == "project":
        row = _scalar(
            connection,
            "SELECT value FROM project_settings WHERE project_id = ? AND key = ?",
            (project_id, key),
        )
    elif tier == "face":
        row = _scalar(
            connection,
            "SELECT value FROM face_settings "
            "WHERE project_id = ? AND face_key = ? AND key = ?",
            (project_id, face_key, key),
        )
    else:
        raise ValueError("unknown tier: {!r}".format(tier))
    if row is None:
        return None
    return _decode_checked(row[0], key, tier, models_dir)


def resolve(connection, key, project_id=None, face_key=None, models_dir=None):
    """The effective value of ``key``: face, then project, then global, then default.

    ``project_id`` and ``face_key`` narrow the search. Omitting ``face_key`` skips
    the face tier; omitting ``project_id`` skips the project and face tiers both,
    which is what a caller with no project open wants.

    The returned value carries the type the schema gives it. That is the whole
    contract: a caller never has to know whether the value came from an override
    or from the default to know what type it is.
    """
    schema.entry(key)

    if project_id is not None and face_key is not None:
        stored = get_override(
            connection, key, project_id, face_key, tier="face", models_dir=models_dir
        )
        if stored is not None:
            return stored

    if project_id is not None:
        stored = get_override(
            connection, key, project_id, tier="project", models_dir=models_dir
        )
        if stored is not None:
            return stored

    stored = get_override(connection, key, tier="global", models_dir=models_dir)
    if stored is not None:
        return stored

    # `effective_default` rather than the raw `default` field: one key's default
    # is a directory listing, and ending the chain at the frozen field would
    # make that the one key resolution returns None for.
    return schema.effective_default(key, models_dir)


def resolve_all(connection, project_id=None, face_key=None, models_dir=None):
    """``{key: effective value}`` for every key in the schema.

    Three queries rather than 201 round trips: each tier is read whole and the
    chain is applied in memory. The schema is 201 keys and a project's overrides
    are a small fraction of that, so this stays trivially cheap while a per-key
    loop would not.
    """
    values = {k: schema.effective_default(k, models_dir) for k in schema.WIDGETS}

    for row in connection.execute("SELECT key, value FROM global_settings"):
        if row[0] in values:
            values[row[0]] = _decode_checked(row[1], row[0], "global", models_dir)

    if project_id is not None:
        for row in connection.execute(
            "SELECT key, value FROM project_settings WHERE project_id = ?",
            (project_id,),
        ):
            if row[0] in values:
                values[row[0]] = _decode_checked(row[1], row[0], "project", models_dir)

    if project_id is not None and face_key is not None:
        for row in connection.execute(
            "SELECT key, value FROM face_settings "
            "WHERE project_id = ? AND face_key = ?",
            (project_id, face_key),
        ):
            if row[0] in values:
                values[row[0]] = _decode_checked(row[1], row[0], "face", models_dir)

    return values


def _tier_slice(connection, tier, project_id=None, face_key=None, models_dir=None):
    resolved = resolve_all(connection, project_id, face_key, models_dir)
    return {k: resolved[k] for k in schema.keys_in_tier(tier)}


def resolve_parameters(connection, project_id, face_key=None, models_dir=None):
    """The project tier as one flat mapping of **all 168 keys**, for one face.

    This is not an abstraction Phase 4 has to adapt. It is literally the value
    that goes into the engine context's per-face parameters mapping under that
    face's own key: ``frame_worker.py:147`` reads
    ``self.parameters[target_face.face_id]`` and three further sites index it
    the same way. The caller stores this dict under the face's key.

    **Every key is present, always.** The engine reads its keys
    unconditionally, so a sparse result is not a graceful fallback to a
    default -- it is a ``KeyError`` raised inside the swap loop, several
    hundred frames after the setting that was missing stopped being on screen.

    Gating is not consulted. See ``visoswap/settings/gates.py`` for why a
    closed gate must not remove a key from this mapping.
    """
    return _tier_slice(connection, "project", project_id, face_key, models_dir)


def resolve_control(connection, models_dir=None):
    """The global tier as one flat mapping of **all 33 keys**.

    The engine context's control mapping: ``frame_worker.py:139`` reads the
    recognition model and similarity type out of it. It is not per project and
    not per face, which is exactly why it is a separate call rather than a
    filter applied to the same dict.
    """
    return _tier_slice(connection, "global", None, None, models_dir)
