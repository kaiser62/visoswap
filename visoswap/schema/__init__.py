"""The frozen settings schema, and the only place a setting's type lives.

``schema.json`` is generated offline by ``tools/generate_schema.py`` on an
interpreter that has Qt, and committed. This loader reads it and nothing else.

One key is **not** frozen. ``DFMModelSelection``'s option list is a directory
listing, so baking it in would stale the file the moment a model file is added or
removed. The generator emits ``null`` for its options and default plus the name of
the upstream function that would have produced them, and this module runs the scan
itself -- see ``dfm_models`` below.

**Standard library only, forever.** ``json``, ``pathlib``, ``logging`` and ``os``;
no third party and nothing from the rest of this package. Two gates enforce it:
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
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.json"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Where the weights live. The vendored engine still uses the relative string
#: ``'./model_assets'`` until Phase 4 makes it env-driven, so this default is
#: anchored to the repository root rather than to the process working directory
#: -- resolving it from the CWD is how a scan silently finds nothing.
DEFAULT_MODELS_DIR = REPO_ROOT / "model_assets"

MODELS_DIR_ENV = "MODELS_DIR"

#: Upstream's ``DFM_MODELS_PATH`` is ``./model_assets/dfm_models``.
DFM_SUBDIR = "dfm_models"

#: The two extensions upstream keeps.
DFM_EXTENSIONS = (".dfm", ".onnx")

#: The one key whose options are a directory listing.
DYNAMIC_KEY = "DFMModelSelection"

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


# --------------------------------------------------------------------------
# the one dynamic option list
# --------------------------------------------------------------------------

#: ``{resolved directory: {filename: path}}``. The scan runs once per directory
#: rather than once per read (T-03-05): the schema is read on every resolution
#: and a per-read listing of a large models directory would be paid 201 times for
#: one answer.
_DFM_CACHE = {}


def clear_dfm_cache():
    """Forget every cached listing. For tests, and for a deliberate rescan."""
    _DFM_CACHE.clear()


def resolve_models_dir(models_dir=None):
    """The models directory, normalised: argument, then ``MODELS_DIR``, then the
    repository-relative default the vendored engine already uses.

    Never a request field (T-03-06). The path is normalised here and only its
    direct entries are ever listed; nothing from the listing is opened.
    """
    chosen = models_dir or os.environ.get(MODELS_DIR_ENV) or DEFAULT_MODELS_DIR
    return Path(chosen).expanduser().resolve()


def dfm_models(models_dir=None):
    """``{filename: path}`` for every DFM model on disk. Empty when there are none.

    This is the same listing that populates the engine context's DFM model
    metadata -- the field Phase 1's context surface flagged as unpopulated and
    plan 02-02 assigned to Phase 4's model bootstrap. **Phase 4 wires this
    mapping into the engine context rather than writing a second scan.** That is
    why this returns the whole mapping and not just the option list.

    Two deliberate differences from upstream's ``get_dfm_models_data``:

    * The result is **sorted**. Upstream returns entries in whatever order the
      filesystem yields, which is not stable across machines, and an option list
      whose order depends on the filesystem makes two identical installs render
      differently. The sort is an improvement, made on purpose.
    * A missing directory is **reported**, not swallowed. Upstream's serializer
      catches the exception and substitutes an empty list, which is why a
      missing models directory today produces an empty dropdown and no error
      anywhere. "The directory has no models" and "the directory does not exist"
      are different facts and only one of them is the user's to fix, so the
      directory that was tried is logged at warning level.
    """
    directory = resolve_models_dir(models_dir) / DFM_SUBDIR
    cached = _DFM_CACHE.get(directory)
    if cached is not None:
        return dict(cached)

    if not directory.is_dir():
        LOGGER.warning(
            "no DFM models directory at %s -- the DFM model list will be empty. "
            "Set %s to the directory holding model_assets, or pass models_dir.",
            directory,
            MODELS_DIR_ENV,
        )
        found = {}
    else:
        found = {
            child.name: str(child)
            for child in sorted(directory.iterdir(), key=lambda p: p.name)
            if child.name.lower().endswith(DFM_EXTENSIONS) and child.is_file()
        }
        if not found:
            LOGGER.info("DFM models directory %s holds no models", directory)

    _DFM_CACHE[directory] = found
    return dict(found)


def _resolved_dynamic(models_dir=None):
    """``(options, default)`` for the dynamic key.

    The default is the first option, or ``''`` when there are none. That is
    upstream's behaviour, and the empty string is a real, meaningful value here
    -- "no DFM model selected" -- rather than a failure marker.
    """
    options = sorted(dfm_models(models_dir))
    return options, (options[0] if options else "")


def resolved_entry(key, models_dir=None):
    """The schema entry for ``key`` with any dynamic parts filled in.

    Identical to ``entry(key)`` for 200 of the 201 keys.
    """
    found = entry(key)
    if key != DYNAMIC_KEY:
        return found
    options, default = _resolved_dynamic(models_dir)
    resolved = dict(found)
    resolved["options"] = options
    resolved["default"] = default
    return resolved


def effective_default(key, models_dir=None):
    """The default a caller should actually use, dynamic key included.

    ``visoswap/settings/store.py`` ends its resolution chain here rather than at
    the raw ``default`` field, so the one key whose default is a directory
    listing is not the one key that resolves to ``None``.
    """
    return resolved_entry(key, models_dir)["default"]


def load(models_dir=None):
    """``{key: entry}`` with every dynamic option list resolved.

    Callers that render controls want this; callers that only need a type can
    read ``WIDGETS`` directly and skip the directory scan. The scan is not done
    at import: every module under ``visoswap/`` is imported by the Phase 1 gate,
    and an import that touches the filesystem is an import that can fail for a
    reason that has nothing to do with what the gate is measuring.
    """
    return {key: resolved_entry(key, models_dir) for key in WIDGETS}
