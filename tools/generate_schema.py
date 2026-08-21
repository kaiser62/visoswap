"""Freeze VisoMaster's four layout dictionaries into one typed ``schema.json``.

Run on an interpreter that **has Qt**. Never imported by the test suite, and
never run on a user's machine.

Three of the four ``*_layout_data`` modules reach PySide6 on import, so the
layout dicts can only be read where Qt is installed. That is why this file lives
in ``tools/`` and not under ``visoswap/``: ``tests/conftest.py`` walks every
``.py`` under ``visoswap/`` and imports it with the seven Qt roots blocked, so a
generator placed there would fail the Phase 1 gate by construction. This is the
project's one Qt-touching step and its output is committed; nothing downstream
ever needs the layout dicts again.

**The shape rule is not restated here. It is imported.**
``tools/dump_engine_settings.shape_of`` is the single definition, and
``dump_engine_settings.coerce`` is the single default-coercion rule. Both scripts
type the same 201 keys off the same widget shapes, and a second copy of that rule
is the specific thing this import exists to prevent. ``engine_settings.json``
types the values the Phase 2 smoke test feeds the engine; ``schema.json`` types
the values everything after Phase 3 feeds it. If the two rules forked, the
divergence would not fail at load -- it would surface as a ``TypeError`` deep
inside inference, a long way from the file that caused it.
``tests/test_schema_fixture_agreement.py`` is what proves the import is really
shared.

Usage:
    "D:/Visomaster/dependencies/Python/python.exe" -B tools/generate_schema.py
"""

import datetime
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dump_engine_settings as fixture_generator

OUTPUT_PATH = os.path.join(REPO_ROOT, "visoswap", "schema", "schema.json")

SCHEMA_VERSION = 1

LICENCE_NOTE = (
    "Derived from VisoMaster (https://github.com/visomaster/VisoMaster), which "
    "is licensed GPLv3; this derived file inherits that licence like the rest of "
    "the vendored tree. See NOTICE and LICENSE."
)

#: ``app.ui.widgets.swapper_layout_data`` -> ``swapper``. The tab a key came
#: from is presentation metadata a renderer needs; the module path is not.
TAB_OF_MODULE = {
    "app.ui.widgets.common_layout_data": "common",
    "app.ui.widgets.swapper_layout_data": "swapper",
    "app.ui.widgets.face_editor_layout_data": "face_editor",
    "app.ui.widgets.settings_layout_data": "settings",
}

EXIT_OK = 0
EXIT_FAILED = 1


def read_tier(modules, tier):
    """Every key in ``modules`` as ``(name, spec, tab, group)``, in file order.

    Unlike the fixture generator's ``flatten``, the group level is kept: it is
    the collapsible box a control lands in, and Phase 5 renders it. It is also
    load-bearing for gating -- upstream only ever evaluates a gate against
    widgets in the *same* group.
    """
    rows = []
    for module_name, attribute in modules:
        layout = fixture_generator.load_layout(module_name, attribute)
        tab = TAB_OF_MODULE[module_name]
        for group, keys in layout.items():
            for name, spec in keys.items():
                rows.append((name, spec, tab, group, tier))
    return rows


def typed_default(name, spec, shape):
    """The typed default for one key, or ``None`` when it is dynamic.

    Coercion is ``dump_engine_settings.coerce`` -- the same call the Phase 2
    fixture makes -- so the two files cannot disagree about whether a key is an
    int or a str. The one divergence is deliberate and is right here: a callable
    default emits ``null`` rather than a value, because resolving it would bake
    this machine's directory listing into a committed file. The loader resolves
    it at load time instead.
    """
    if callable(spec.get("default")):
        return None
    return fixture_generator.coerce(name, spec, shape, [])


def build_entry(name, spec, tab, group, tier):
    shape = fixture_generator.shape_of(spec)
    return {
        # `type` is the shape name, verbatim. Never a substring of the key name:
        # the current web UI picks a control by testing the key for `Toggle` /
        # `Selection` / `Decimal`, which is why `ClipText` renders today as a
        # slider from 0 to 1000 over a text field.
        "type": shape,
        "tier": tier,
        "default": typed_default(name, spec, shape),
    }


def build_widgets(rows):
    widgets = {}
    for name, spec, tab, group, tier in rows:
        widgets[name] = build_entry(name, spec, tab, group, tier)
    return widgets


def read_existing_widgets(path):
    """The ``widgets`` object already on disk, or ``None`` if there is none."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle).get("widgets")
    except (OSError, ValueError):
        return None


def write_schema(path, widgets, visomaster_dir):
    """Write ``path``, but only when ``widgets`` actually changed.

    The header records a generation date and the source checkout path, and both
    of those would otherwise make regeneration produce a diff every day and on
    every machine -- which would defeat the byte-identical check that is this
    project's only guard against a frozen schema silently going stale (T-03-07).
    So the *content* is the thing compared, and the header is rewritten only when
    the content moves. The date therefore means "when this content last changed",
    which is the more useful of the two readings anyway.

    Returns True when the file was written.
    """
    if read_existing_widgets(path) == widgets:
        return False

    payload = {
        "schema": {
            "version": SCHEMA_VERSION,
            "generated": datetime.date.today().isoformat(),
            "source": visomaster_dir.replace(os.sep, "/"),
            "licence": LICENCE_NOTE,
        },
        "widgets": widgets,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # sort_keys so a regeneration diffs as the value that moved and never as a
    # reordering; explicit "\n" so the bytes do not depend on the platform's
    # newline translation.
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return True


def census(widgets):
    counts = {}
    for entry in widgets.values():
        counts[entry["type"]] = counts.get(entry["type"], 0) + 1
    return counts


def main():
    # Before any layout import: `settings_layout_data` builds real widgets on
    # import and would otherwise try to reach a display.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    visomaster_dir = fixture_generator.resolve_visomaster_dir()
    if not os.path.isdir(visomaster_dir):
        print("FAILED: no VisoMaster checkout at {}".format(visomaster_dir))
        return EXIT_FAILED
    print("visomaster: {}".format(visomaster_dir))
    if visomaster_dir not in sys.path:
        sys.path.insert(0, visomaster_dir)

    try:
        rows = read_tier(fixture_generator.PROJECT_MODULES, "project")
        rows += read_tier(fixture_generator.GLOBAL_MODULES, "global")
    except ImportError as exc:
        print(
            "FAILED: {}. This generator needs an interpreter WITH Qt -- three of "
            "the four layout modules reach PySide6 on import.".format(exc)
        )
        return EXIT_FAILED

    names = [row[0] for row in rows]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        print(
            "FAILED: a key appears in more than one tier, so its resolution "
            "order is undefined: {}".format(duplicates)
        )
        return EXIT_FAILED

    widgets = build_widgets(rows)
    written = write_schema(OUTPUT_PATH, widgets, visomaster_dir)

    project = [e for e in widgets.values() if e["tier"] == "project"]
    global_tier = [e for e in widgets.values() if e["tier"] == "global"]
    print(
        "{}: {} ({} keys: project={} global={} types={})".format(
            "WROTE" if written else "UNCHANGED",
            OUTPUT_PATH,
            len(widgets),
            len(project),
            len(global_tier),
            census(widgets),
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
