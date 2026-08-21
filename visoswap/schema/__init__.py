"""The frozen settings schema, and the only place a setting's type lives.

``schema.json`` is generated offline by ``tools/generate_schema.py`` on an
interpreter that has Qt, and committed. This loader reads it and nothing else.

**Standard library only, forever.** ``json`` and ``pathlib``, no third party and
nothing from the rest of this package. Two gates enforce it automatically:
``tests/test_qt_free.py`` imports every module under ``visoswap/`` with the seven
Qt binding roots and the backend package made unimportable, and
``tests/test_no_qt_source.py`` scans the source text. Both discover this file by
walking the tree, so neither had to be told it exists.

The schema replaces a type that used to live in a Qt window. Every ``default`` in
the upstream layout dicts is a string -- ``'60'``, ``'2.50'``, ``'50'`` -- and the
window coerced incoming values against its own widget table on the way past. Move
the type into the data and that table stops being the authority; this file is
what it becomes instead.
"""

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.json"

#: The two tiers, in resolution order from most specific to least. The face tier
#: has no schema presence -- any project-tier key may be overridden per face --
#: so it is not listed here.
TIERS = ("project", "global")


def _load(path=None):
    with open(path or SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


_DOCUMENT = _load()

#: Provenance: schema version, the date the content last changed, the source
#: checkout it was derived from, and the licence that derivation carries.
HEADER = _DOCUMENT["schema"]

#: ``{key: entry}`` for all 201 settings keys. The single authority on the type,
#: tier and default of every setting in the project.
WIDGETS = _DOCUMENT["widgets"]


class UnknownSettingsKey(KeyError):
    """Raised for a key the schema does not define.

    A key the schema has never heard of is either a typo or a stale client. Both
    silently do nothing in the system this replaces, which is exactly how the
    schema stays advisory rather than authoritative. Failing at the boundary is
    the whole point.
    """


def entry(key):
    """The schema entry for one key. Raises ``UnknownSettingsKey`` otherwise."""
    try:
        return WIDGETS[key]
    except KeyError:
        raise UnknownSettingsKey(key) from None


def tier_of(key):
    """``'project'`` or ``'global'``."""
    return entry(key)["tier"]


def type_of(key):
    """One of ``float``, ``int``, ``text``, ``selection``, ``toggle``.

    Derived from widget shape when the schema was generated, never from a
    substring of the key name. The name convention is wrong and untrustworthy:
    ``ClipText`` contains neither ``Toggle`` nor ``Selection`` and is not a
    slider, and the current web UI draws it as one from 0 to 1000.
    """
    return entry(key)["type"]


def default_of(key):
    """The typed default. ``None`` only for a key whose default is dynamic."""
    return entry(key)["default"]


def keys_in_tier(tier):
    """Every key belonging to ``tier``, sorted."""
    return sorted(k for k, e in WIDGETS.items() if e["tier"] == tier)


def defaults_for_tier(tier):
    """``{key: typed default}`` for one tier."""
    return {k: WIDGETS[k]["default"] for k in keys_in_tier(tier)}


def defaults():
    """The defaults projected as two flat tier mappings.

    ``(project_defaults, global_defaults)``. The two key sets are disjoint --
    measured across all four layout dicts, and pinned by
    ``tests/test_schema_generated.py`` -- so a caller may merge them without
    deciding a precedence.
    """
    return defaults_for_tier("project"), defaults_for_tier("global")
