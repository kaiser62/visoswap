"""Migrate VisoMaster's saved profiles into a committed, typed preset seed.

One shot. Run it once, commit the output, and never need the source file again.

**No Qt.** Unlike ``tools/generate_schema.py``, which has to import layout
modules that build real widgets, this reads plain JSON and the already-committed
schema, so it runs on the plain developer interpreter::

    python tools/migrate_profiles.py
    VISOMASTER_PROFILES=/path/to/profiles.json python tools/migrate_profiles.py

Why the output is committed
---------------------------
``profiles.json`` exists on exactly one developer's disk. A user machine must
never need it, for the same reason it must never need the Qt layout dicts: the
data is the deliverable and the source of the data is not shippable. The output
is ``visoswap/settings/data/presets_seed.json``, and it is written **only when
its content changes** so that the header's date does not make regeneration
produce a diff every day -- the same rule, for the same reason, as the schema
generator's.

This is the one intended caller of the lenient converter
--------------------------------------------------------
``visoswap.settings.validate.coerce`` exists for this file and for nothing else.
Its docstring says so and ``tests/test_settings_validation.py`` enforces it
against ``visoswap/`` and ``backend/``. No second coercion rule is written here:
a preset typed by a rule of its own would disagree with everything else in the
phase about what a slider is, and the disagreement would not surface until a
value reached the engine.

What the source actually holds, measured before this was written
----------------------------------------------------------------
Two profiles. ``6a24b3aa71d7`` named ``A`` and ``55fc87c18307`` named
``with AUD``. Each carries 33 ``options`` -- the global tier, matching the
schema's global key set exactly -- and 168 ``parameters``, the project tier.

The values are **not** uniformly strings, which is the trap this migration is
built around. ``A`` holds 138 strings and 30 bools in ``parameters``;
``with AUD`` holds 136 strings, 30 bools and **2 ints**
(``SimilarityThresholdSlider`` is ``20`` and ``StrengthAmountSlider`` is ``150``,
while ``A`` spells the same two keys as strings). So a value cannot be typed by
looking at what type it currently is. It is typed by **shape**, through the
schema, exactly as the generator types the defaults -- which is what ``coerce``
does and why it exists.

Three surprises are reported, never absorbed
--------------------------------------------
A key the source has and the schema does not; a key the schema has and the
source does not; a value that fails strict validation after conversion. All
three mean the saved profiles and the schema disagree about what a setting is,
which is exactly what a migration is for finding. The measured expectation is
zero of each, and the counts are printed either way so that a clean run says so
out loud rather than by silence.

The one key migrated against an empty models directory
------------------------------------------------------
``DFMModelSelection``'s option list is a directory listing, so whether ``''`` --
which is what both profiles store, and is upstream's own "no DFM model chosen"
value -- is a legal option depends on which files sit on the migrating machine.
Letting that decide the committed bytes would make the seed a function of one
developer's disk contents rather than of the source profiles. It is therefore
migrated against a deliberately empty directory: the seed records what the
profiles stored, and whether a named model exists is a question for the machine
that applies the preset, not for the machine that migrated it.
"""

import datetime
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dump_engine_settings as fixture_generator  # noqa: E402

from visoswap import schema  # noqa: E402
from visoswap.settings import validate  # noqa: E402

OUTPUT_PATH = os.path.join(
    REPO_ROOT, "visoswap", "settings", "data", "presets_seed.json"
)

SEED_VERSION = 1

#: An explicit path to the profiles file, overriding the checkout-relative
#: default. Named after the source project, like ``VISOMASTER_DIR``.
PROFILES_ENV_VAR = "VISOMASTER_PROFILES"

PROFILES_FILENAME = "profiles.json"

LICENCE_NOTE = (
    "Derived from VisoMaster (https://github.com/visomaster/VisoMaster), which "
    "is licensed GPLv3; this derived file inherits that licence like the rest of "
    "the vendored tree. See NOTICE and LICENSE."
)

#: ``options`` is the global tier and ``parameters`` is the project tier. The
#: mapping is not a guess: both source objects hold exactly the schema's key set
#: for their tier, 33 and 168, with nothing left over on either side.
TIER_OF_SOURCE_FIELD = {"options": "global", "parameters": "project"}

EXIT_OK = 0
EXIT_FAILED = 1


def resolve_profiles_path():
    """The profiles file: the env override, else the VisoMaster checkout's own."""
    override = os.environ.get(PROFILES_ENV_VAR)
    if override:
        return os.path.abspath(override)
    return os.path.join(fixture_generator.resolve_visomaster_dir(), PROFILES_FILENAME)


def read_profiles(path):
    """The source profiles, or a ``FileNotFoundError`` that names the path.

    Naming the path matters more than it looks: the default is one developer's
    absolute Windows path, and "not found" without it sends the next reader
    looking in the repository.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "no profiles file at {}. Set {} to point at one, or run this on the "
            "machine that has the VisoMaster checkout. The seed is committed "
            "precisely so that nobody else has to.".format(path, PROFILES_ENV_VAR)
        )
    with open(path, "r", encoding="utf-8") as handle:
        profiles = json.load(handle)
    if not isinstance(profiles, list):
        raise ValueError(
            "{} holds a {}; the profiles file is a list of profile objects".format(
                path, type(profiles).__name__
            )
        )
    return profiles


def convert_tier(source_values, tier, models_dir):
    """``(converted, report)`` for one tier of one profile.

    ``report`` carries the three disagreements this migration exists to surface:
    ``unknown`` keys, ``missing`` keys and ``invalid`` values.
    """
    expected = set(schema.keys_in_tier(tier))
    converted = {}
    report = {"unknown": [], "missing": [], "invalid": []}

    for key, value in source_values.items():
        if key not in expected:
            report["unknown"].append(key)
            continue
        try:
            converted[key] = validate.coerce(key, value, models_dir)
        except validate.InvalidSettingValue as error:
            report["invalid"].append((key, value, str(error)))

    report["missing"] = sorted(expected - set(source_values))
    return converted, report


def convert_profile(profile, models_dir):
    """``(preset, report)`` for one source profile."""
    preset = {
        "id": profile["id"],
        "name": profile["name"],
        "created": profile["created"],
        "updated": profile["updated"],
    }
    combined = {"unknown": [], "missing": [], "invalid": []}
    for field, tier in TIER_OF_SOURCE_FIELD.items():
        converted, report = convert_tier(profile.get(field, {}), tier, models_dir)
        preset[tier] = converted
        for kind in combined:
            combined[kind].extend([(field, item) for item in report[kind]])
    return preset, combined


def read_existing_presets(path):
    """The presets already committed, or ``None``. Used to write only on change."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle).get("presets")
    except (OSError, ValueError, AttributeError):
        return None


def write_seed(path, presets, source_path):
    """Write ``path``, but only when ``presets`` actually changed.

    Same rule as the schema generator's, for the same reason: the header records
    a date and the source path, and rewriting it unconditionally would make
    ``git diff --exit-code`` fail every day and on every machine -- defeating the
    only staleness check this file has.

    Returns True when the file was written.
    """
    if read_existing_presets(path) == presets:
        return False

    payload = {
        "seed": {
            "version": SEED_VERSION,
            "generated": datetime.date.today().isoformat(),
            "source": source_path.replace(os.sep, "/"),
            "licence": LICENCE_NOTE,
        },
        "presets": presets,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return True


def main():
    profiles_path = resolve_profiles_path()
    try:
        profiles = read_profiles(profiles_path)
    except (FileNotFoundError, ValueError) as error:
        print("FAILED: {}".format(error))
        return EXIT_FAILED
    print("profiles: {} ({} found)".format(profiles_path, len(profiles)))

    presets = []
    totals = {"unknown": 0, "missing": 0, "invalid": 0}
    failed = False

    # An empty directory on purpose -- see the module docstring. The schema's
    # own listing cache is cleared afterwards so nothing downstream inherits it.
    with tempfile.TemporaryDirectory(prefix="visoswap-migration-") as models_dir:
        for profile in profiles:
            preset, report = convert_profile(profile, models_dir)
            presets.append(preset)
            print(
                "  {} {!r}: project={} global={} unknown={} missing={} invalid={}".format(
                    preset["id"],
                    preset["name"],
                    len(preset["project"]),
                    len(preset["global"]),
                    len(report["unknown"]),
                    len(report["missing"]),
                    len(report["invalid"]),
                )
            )
            for kind in totals:
                totals[kind] += len(report[kind])
                for item in report[kind][:10]:
                    print("    {}: {}".format(kind, item))
            if report["unknown"] or report["missing"] or report["invalid"]:
                failed = True
    schema.clear_dfm_cache()

    print(
        "totals: unknown={unknown} missing={missing} invalid={invalid}".format(**totals)
    )
    if failed:
        print(
            "FAILED: the saved profiles and the schema disagree about what a "
            "setting is. Nothing was written -- a seed built over a "
            "disagreement carries it into every project that takes a preset."
        )
        return EXIT_FAILED

    written = write_seed(OUTPUT_PATH, presets, profiles_path)
    print(
        "{}: {} ({} presets)".format(
            "WROTE" if written else "UNCHANGED", OUTPUT_PATH, len(presets)
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
