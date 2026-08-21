"""Extract a typed settings pair from VisoMaster's layout dicts, once, offline.

Run on an interpreter that **has Qt**. Never imported by the test suite.

Three of the four ``*_layout_data`` modules reach PySide6 on import -- measured
with the same seven-root block the Phase 1 gate uses, only
``face_editor_layout_data`` comes through clean. So the layout dicts cannot be
read by any Qt-free test, and the settings they define have to be extracted once
here and checked in as ``tests/fixtures/engine_settings.json``.

The interesting part is not the flattening, it is the **typing**. Every
``default`` in those dicts is a string: ``ClipAmountSlider`` is ``'50'``,
``SimilarityThresholdSlider`` is ``'60'``, ``FaceEditorCropScaleDecimalSlider``
is ``'2.50'``. ``profiles.json`` carries the same 168 keys with 138 string
values. Qt widgets accept strings and coerce internally, so nothing upstream ever
had to care. The engine does: hand it ``'50'`` and it fails deep inside a tensor
op with a ``TypeError``, a long way from the load that caused it.

So the design's "typed default where the widget has one" is resolved from **widget
shape**, not from the Python type of ``default``. Shape is fully mechanical:

===========================================  ======  =====
shape                                        count   type
===========================================  ======  =====
``decimals`` + min/max/step                      42  float
min/max/step, no ``decimals``                    93  int
min/max, **no** ``step``, no ``decimals``         1  str    (``ClipText``)
an ``options`` list                              22  str
none of the above                                43  bool
===========================================  ======  =====

``ClipText`` is the shape the four-shape rule misses. Its ``min_value: '0'`` /
``max_value: '1000'`` are character-count bounds on a line edit, not slider
bounds, and its default is ``''``. Typing it as an int is not merely wrong --
``int(float(''))`` raises, so the generator would die rather than mislead. The
distinguishing signal is ``step``: a slider has one, a text box does not.

Usage:
    "D:/Visomaster/dependencies/Python/python.exe" -B tools/dump_engine_settings.py
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUTPUT_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "engine_settings.json")

VISOMASTER_ENV_VAR = "VISOMASTER_DIR"
DEFAULT_VISOMASTER_DIR = os.path.join("D:", os.sep, "Visomaster")

#: Merge order for the project tier. Later modules win on a collision -- there
#: are none today (measured: zero overlap across all four), but the order is
#: fixed so that if upstream ever introduces one, the result is deterministic
#: rather than dependent on how this tuple happened to be written.
PROJECT_MODULES = (
    ("app.ui.widgets.common_layout_data", "COMMON_LAYOUT_DATA"),
    ("app.ui.widgets.swapper_layout_data", "SWAPPER_LAYOUT_DATA"),
    ("app.ui.widgets.face_editor_layout_data", "FACE_EDITOR_LAYOUT_DATA"),
)

GLOBAL_MODULES = (("app.ui.widgets.settings_layout_data", "SETTINGS_LAYOUT_DATA"),)

EXIT_OK = 0
EXIT_FAILED = 1


def resolve_visomaster_dir() -> str:
    return os.path.abspath(
        os.environ.get(VISOMASTER_ENV_VAR) or DEFAULT_VISOMASTER_DIR
    )


def load_layout(module_name, attribute):
    """Import a layout module and return its dict. Qt must be importable."""
    module = __import__(module_name, fromlist=[attribute])
    return getattr(module, attribute)


def flatten(layout) -> dict:
    """``{group: {key: spec}}`` -> ``{key: spec}``.

    The group level is presentation only -- it decides which collapsible box a
    widget lands in. Nothing in the engine reads it, and the design's two tiers
    are flat, so it is dropped here rather than carried and ignored downstream.
    """
    flat = {}
    for keys in layout.values():
        flat.update(keys)
    return flat


def shape_of(spec) -> str:
    """The widget shape of one key's spec. Total: every spec gets a shape."""
    bounded = "min_value" in spec and "max_value" in spec
    if bounded and "step" in spec:
        # A slider. `decimals` is what separates the float track from the int
        # track, and it never appears without min/max/step.
        return "float" if "decimals" in spec else "int"
    if bounded:
        # A line edit whose bounds count characters. `ClipText` is the only one.
        return "text"
    if "options" in spec:
        return "selection"
    return "toggle"


def coerce(name, spec, shape, callable_defaults):
    """The typed value for one key, derived from its shape.

    ``int`` goes through ``float()`` first on purpose: ``int('2.50')`` raises,
    and upstream writes decimal strings into keys that are otherwise integral.
    Coercing by shape rather than by the type of ``default`` is the whole point
    -- reading the type of ``default`` would just give back ``str`` 138 times.
    """
    default = spec.get("default")

    if callable(default):
        # `DFMModelSelection` alone: its `default` and `options` are
        # `get_dfm_models_default_value` / `get_dfm_models_selection_values`,
        # which scan a models directory the engine does not own. Resolving it
        # here would bake this machine's (empty) dfm_models listing into a
        # checked-in file. It resolves to empty and is reported, not dropped:
        # plan 02-04 needs to know the key starts empty, and absent and empty
        # are different facts.
        callable_defaults.append(name)
        return ""

    if shape == "float":
        return float(default)
    if shape == "int":
        return int(float(default))
    if shape == "toggle":
        if not isinstance(default, bool):
            raise TypeError(
                "{} has no bounds and no options, so it is a toggle, but its "
                "default is {!r}. A new widget shape has appeared -- add it to "
                "shape_of() rather than letting it fall through to bool.".format(
                    name, default
                )
            )
        return default
    # selection and text both carry their value as the string the engine
    # compares or displays. `str()` rather than a bare pass-through so a stray
    # non-string default cannot smuggle a non-JSON-native type into the file.
    return str(default)


def build_tier(modules, callable_defaults) -> dict:
    tier = {}
    for module_name, attribute in modules:
        flat = flatten(load_layout(module_name, attribute))
        for name, spec in flat.items():
            tier[name] = coerce(name, spec, shape_of(spec), callable_defaults)
    return tier


def census(values) -> dict:
    """Type counts, bool tested before int -- ``isinstance(True, int)`` is True."""
    counts = {}
    for value in values:
        if isinstance(value, bool):
            kind = "bool"
        elif isinstance(value, int):
            kind = "int"
        elif isinstance(value, float):
            kind = "float"
        else:
            kind = type(value).__name__
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def main() -> int:
    # Before any layout import: `settings_layout_data` builds real widgets on
    # import and would otherwise try to reach a display.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    visomaster_dir = resolve_visomaster_dir()
    if not os.path.isdir(visomaster_dir):
        print("FAILED: no VisoMaster checkout at {}".format(visomaster_dir))
        return EXIT_FAILED
    print(
        "visomaster: {} (from {})".format(
            visomaster_dir,
            VISOMASTER_ENV_VAR if os.environ.get(VISOMASTER_ENV_VAR) else "default",
        )
    )
    if visomaster_dir not in sys.path:
        sys.path.insert(0, visomaster_dir)

    callable_defaults = []
    try:
        project = build_tier(PROJECT_MODULES, callable_defaults)
        global_tier = build_tier(GLOBAL_MODULES, callable_defaults)
    except ImportError as exc:
        print(
            "FAILED: {}. This generator needs an interpreter WITH Qt -- three of "
            "the four layout modules reach PySide6 on import.".format(exc)
        )
        return EXIT_FAILED

    overlap = sorted(set(project) & set(global_tier))
    if overlap:
        print("FAILED: keys in both tiers, resolution order undefined: {}".format(overlap))
        return EXIT_FAILED

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    payload = {"project": project, "global": global_tier}
    # sort_keys so a regeneration diffs as a value change rather than as a
    # reordering; explicit "\n" so the file is byte-identical regardless of the
    # platform's default newline translation.
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")

    combined = list(project.values()) + list(global_tier.values())
    print(
        "WROTE: {} (project={} global={} types={})".format(
            OUTPUT_PATH, len(project), len(global_tier), census(combined)
        )
    )
    print(
        "callable defaults resolved to empty string: {}".format(
            ", ".join(sorted(callable_defaults)) or "none"
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
