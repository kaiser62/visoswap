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

**Gating, and what the two compound spellings actually do.** Upstream gates a
control with either ``parentToggle`` (140 keys) or ``parentSelection`` (4 keys),
and this generator normalises both into one ``gate`` object per entry. A bare
``parentToggle`` names one parent. Two undocumented compound spellings exist, and
their rules were read out of upstream's evaluator -- the ``'Toggle' in
parent_widget_name`` branch of ``app/ui/widgets/actions/common_actions.py`` --
rather than inferred from the punctuation, because the punctuation is misleading:

  - ``'A|B'`` -> rule ``all``. The evaluator starts at True and clears it if
    **any** parent is unchecked. The pipe reads like OR; the code is an AND.
    3 keys, all ``FaceExpression...`` sliders.
  - ``'A, B'`` -> rule ``last``. The evaluator *assigns* rather than combines on
    each pass of its loop, so only the last parent has any effect whatsoever.
    That is a bug upstream. It is recorded as measured rather than quietly
    improved into the ``all`` a reader would expect: a renderer that hides a
    control upstream shows is a behaviour change nobody asked for, and it would
    be invisible in a diff of this file. 2 keys.
  - a selection gate is always one parent and an equality against a string. All
    4 name ``SwapModelSelection``.

The gate is **presentational**. It belongs here so a renderer can hide a control,
and ``visoswap/settings/store.py`` never consults it: the engine reads
``parameters[key]`` unconditionally and reads the parent toggle separately, so
filtering values by gate state would change engine behaviour.

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


def qualified_name(function):
    return "{}.{}".format(function.__module__, function.__qualname__)


def number(shape, raw):
    """One bound, coerced by the same shape rule that types the default.

    ``min_value`` and ``max_value`` are strings upstream while ``step`` is
    already numeric, so a single spec routinely disagrees with itself about the
    type of its own bounds.
    """
    return float(raw) if shape == "float" else int(float(raw))


def build_gate(spec):
    """The one uniform gate object, or ``None`` for an ungated key."""
    if "parentSelection" in spec:
        return {
            "mechanism": "selection",
            "parents": [spec["parentSelection"].strip()],
            "rule": "single",
            "required_value": spec.get("requiredSelectionValue"),
        }
    if "parentToggle" not in spec:
        return None

    raw = spec["parentToggle"]
    if "," in raw:
        # See the module docstring: upstream assigns rather than combines, so
        # only the last parent decides. Recorded as measured, not improved.
        parents, rule = [p.strip() for p in raw.split(",")], "last"
    elif "|" in raw:
        # The pipe reads like OR. Upstream's loop is an AND.
        parents, rule = [p.strip() for p in raw.split("|")], "all"
    else:
        parents, rule = [raw.strip()], "single"
    return {
        "mechanism": "toggle",
        "parents": parents,
        "rule": rule,
        "required_value": spec.get("requiredToggleValue"),
    }


def build_entry(name, spec, tab, group, tier):
    shape = fixture_generator.shape_of(spec)
    entry = {
        # `type` is the shape name, verbatim. Never a substring of the key name:
        # the current web UI picks a control by testing the key for `Toggle` /
        # `Selection` / `Decimal`, which is why `ClipText` renders today as a
        # slider from 0 to 1000 over a text field.
        "type": shape,
        "tier": tier,
        "tab": tab,
        "group": group,
        "label": spec["label"],
        "help": spec["help"],
        # `level` is the string '1'/'2'/'3' upstream. Emitted as an int so a
        # renderer showing "advanced settings only" can compare rather than parse.
        "level": int(spec["level"]),
        "default": typed_default(name, spec, shape),
        "gate": build_gate(spec),
    }

    if shape in ("int", "float"):
        entry["minimum"] = number(shape, spec["min_value"])
        entry["maximum"] = number(shape, spec["max_value"])
        entry["step"] = number(shape, spec["step"])
    if shape == "float":
        entry["decimals"] = int(spec["decimals"])
    if shape == "text":
        # Character-count bounds on a line edit, NOT slider bounds. Emitting
        # these as `minimum`/`maximum` is exactly the confusion that makes the
        # current renderer draw a slider from 0 to 1000 over a text field.
        entry["min_length"] = int(spec["min_value"])
        entry["max_length"] = int(spec["max_value"])
    if shape == "selection":
        options = spec["options"]
        if callable(options):
            # The one directory listing. Freezing it would stale the file the
            # moment a model file is added or removed, so the resolver is named
            # and `visoswap/schema` runs the scan itself at load time.
            entry["options"] = None
            entry["options_from"] = qualified_name(options)
        else:
            entry["options"] = list(options)
    if callable(spec.get("default")):
        entry["default_from"] = qualified_name(spec["default"])

    if "exec_function" in spec:
        # Upstream attached a side effect to six keys, and the current
        # serializer drops every key starting with `exec_` -- which is how they
        # went missing without anyone noticing. Naming the dropped function is
        # what makes the gap visible and typed. `exec_function_args` is `[]` for
        # all six, so it is not emitted: an empty list on every entry is noise
        # that reads like a feature.
        entry["exec_function"] = qualified_name(spec["exec_function"])

    return entry


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
