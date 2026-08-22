"""Pin the generated schema hard enough that an upstream change cannot slip past.

The counts here are **checksums against upstream, not inputs**. If one disagrees,
``D:/Visomaster`` is authoritative and this file should be updated -- but the
disagreement has to be reported loudly, because it means an upstream layout
change has landed and Phases 4 and 5 are planning against a stale key set. Every
count assertion below says so in its own failure message rather than trusting
that whoever hits it will remember.

Standard library and pytest only. This runs on the plain developer interpreter
where neither torch nor Qt is installed -- it reads the committed file and never
the layout dicts, which is the entire reason the file is committed.

Three traps are guarded deliberately, because each one passes a naive check:

1. **``isinstance(True, int)`` is True.** A type census that tests int before
   bool reports all 43 toggles as ints and looks perfectly healthy.
2. **A missing bound satisfies a presence check.** So the numeric assertions test
   for the *absence* of ``str`` bounds, not the presence of numeric ones; a key
   that lost its bounds entirely would sail through the other formulation.
3. **A compound gate is not one key name.** Three parents are spelled
   ``A|B`` upstream and two more ``A, B``. Any check that treats the whole string
   as a key name reports five dangling parents that are not dangling -- a false
   alarm loud enough that the next real one gets ignored.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "visoswap" / "schema" / "schema.json"

#: Measured against D:/Visomaster on 2026-08-22 by flattening all four layout
#: dicts. common 23 + swapper 103 + face_editor 42 = 168 project, settings = 33
#: global, no key in both.
EXPECTED_TOTAL = 201
EXPECTED_PROJECT = 168
EXPECTED_GLOBAL = 33

#: The five widget shapes and their populations. `step` is what separates a
#: slider from a line edit; `decimals` is what separates the float track from
#: the int track.
EXPECTED_TYPES = {
    "int": 93,
    "toggle": 43,
    "float": 42,
    "selection": 22,
    "text": 1,
}

EXPECTED_TOGGLE_GATES = 140
EXPECTED_SELECTION_GATES = 4
EXPECTED_EXEC_FUNCTIONS = 6

#: Keys per gate-chain depth. 57 ungated, 133 gated by an ungated parent, and 11
#: whose parent is itself gated. The deepest chain is 2 and there are no cycles.
EXPECTED_DEPTHS = {0: 57, 1: 133, 2: 11}

DYNAMIC_KEY = "DFMModelSelection"

STALE = (
    " -- if D:/Visomaster changed, it is authoritative and this number should "
    "be updated, but say so loudly in the plan summary: Phases 4 and 5 plan "
    "against these counts."
)


@pytest.fixture(scope="module")
def document():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def widgets(document):
    return document["widgets"]


def python_type_name(value):
    """Type name with bool tested before int. Trap 1."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    return type(value).__name__


def gate_parents(entry):
    """Every parent key a gate names, compound spellings resolved. Trap 3."""
    gate = entry.get("gate")
    return list(gate["parents"]) if gate else []


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


def test_the_file_records_where_it_came_from(document):
    """A frozen file with no provenance cannot be shown to be stale. T-03-07."""
    header = document["schema"]
    assert header["version"] == 1
    assert header["generated"]
    assert header["source"]
    assert "GPLv3" in header["licence"], (
        "the schema is derived from GPLv3 material and this note is the only "
        "place that is recorded -- schema.json is not a .py file, so the "
        "attribution gate never walks it"
    )


# --------------------------------------------------------------------------
# counts and tiers
# --------------------------------------------------------------------------


def test_the_schema_holds_every_settings_key(widgets):
    assert len(widgets) == EXPECTED_TOTAL, (
        "expected {} keys, found {}".format(EXPECTED_TOTAL, len(widgets)) + STALE
    )


def test_the_two_tiers_have_the_measured_populations(widgets):
    project = [k for k, e in widgets.items() if e["tier"] == "project"]
    global_tier = [k for k, e in widgets.items() if e["tier"] == "global"]
    assert (len(project), len(global_tier)) == (EXPECTED_PROJECT, EXPECTED_GLOBAL), (
        "expected {}/{}, found {}/{}".format(
            EXPECTED_PROJECT, EXPECTED_GLOBAL, len(project), len(global_tier)
        )
        + STALE
    )
    assert len(project) + len(global_tier) == len(widgets), (
        "a key carries a tier that is neither project nor global"
    )


def test_the_two_tiers_do_not_intersect(widgets):
    """``visoswap/settings/store.py`` documents this as the reason its chain is
    unambiguous. This test is what keeps that docstring true."""
    project = {k for k, e in widgets.items() if e["tier"] == "project"}
    global_tier = {k for k, e in widgets.items() if e["tier"] == "global"}
    assert not (project & global_tier), (
        "keys in both tiers, so resolution order is undefined for them: "
        "{}".format(sorted(project & global_tier))
    )


def test_every_entry_carries_the_full_presentation_surface(widgets):
    """Phase 5 renders from these fields and nothing else."""
    required = ("type", "tier", "tab", "group", "label", "help", "level")
    missing = {
        key: [f for f in required if f not in entry]
        for key, entry in widgets.items()
        if any(f not in entry for f in required)
    }
    assert not missing, missing

    assert {e["tab"] for e in widgets.values()} == {
        "common",
        "swapper",
        "face_editor",
        "settings",
    }
    assert all(isinstance(e["level"], int) for e in widgets.values()), (
        "level is the string '1'/'2'/'3' upstream; it is emitted as an int here "
        "so a renderer can compare it without parsing"
    )


# --------------------------------------------------------------------------
# types
# --------------------------------------------------------------------------


def test_the_type_histogram_matches_the_measured_widget_shapes(widgets):
    histogram = {}
    for entry in widgets.values():
        histogram[entry["type"]] = histogram.get(entry["type"], 0) + 1
    assert histogram == EXPECTED_TYPES, (
        "expected {}, found {}".format(EXPECTED_TYPES, histogram) + STALE
    )


def test_no_numeric_entry_carries_a_string_bound_or_default(widgets):
    """Trap 2: assert the *absence* of ``str``, not the presence of a number.

    Upstream stores ``min_value`` and ``max_value`` as strings while ``step`` is
    already numeric, so a single spec routinely disagrees with itself about type.
    A key that lost its bounds entirely would satisfy "the bounds are numbers";
    it does not satisfy this.
    """
    offenders = []
    for key, entry in widgets.items():
        if entry["type"] not in ("int", "float"):
            continue
        for field in ("default", "minimum", "maximum", "step", "decimals"):
            value = entry.get(field)
            if isinstance(value, str):
                offenders.append((key, field, value))
    assert not offenders, (
        "numeric entries carrying string values -- the exact defect this phase "
        "exists to remove:\n{}".format(offenders)
    )


def test_every_numeric_entry_has_all_of_its_bounds(widgets):
    """The other half of trap 2: absent is not the same as correct."""
    missing = []
    for key, entry in widgets.items():
        if entry["type"] == "int":
            fields = ("minimum", "maximum", "step")
        elif entry["type"] == "float":
            fields = ("minimum", "maximum", "step", "decimals")
        else:
            continue
        for field in fields:
            if field not in entry:
                missing.append((key, field))
    assert not missing, missing

    assert not any("decimals" in e for k, e in widgets.items() if e["type"] != "float"), (
        "decimals is what separates the float track from the int track; on a "
        "non-float entry it is meaningless"
    )


def test_int_entries_are_ints_and_float_entries_are_floats(widgets):
    wrong = []
    for key, entry in widgets.items():
        if entry["type"] == "int":
            for field in ("default", "minimum", "maximum", "step"):
                if python_type_name(entry[field]) != "int":
                    wrong.append((key, field, entry[field]))
        elif entry["type"] == "float":
            for field in ("default", "minimum", "maximum", "step"):
                if not isinstance(entry[field], float):
                    wrong.append((key, field, entry[field]))
    assert not wrong, wrong


def test_no_toggle_carries_a_default_that_is_not_a_bool(widgets):
    """Trap 1 in the other direction: ``1`` would pass ``isinstance(x, int)``."""
    wrong = [
        (key, entry["default"])
        for key, entry in widgets.items()
        if entry["type"] == "toggle" and python_type_name(entry["default"]) != "bool"
    ]
    assert not wrong, wrong


def test_cliptext_is_the_one_text_entry_and_carries_character_bounds(widgets):
    """The five-shape rule exists for this key alone.

    ``ClipText`` carries ``min_value``/``max_value`` with **no** ``step``: they
    are character-count bounds on a line edit, not slider bounds. A four-shape
    rule does not mislabel it, it crashes -- ``int(float(''))`` raises. The
    current web UI has no such rule and draws it as a slider from 0 to 1000 over
    a text field, which is the concrete failure the explicit ``type`` prevents.
    """
    text_keys = [k for k, e in widgets.items() if e["type"] == "text"]
    assert text_keys == ["ClipText"], text_keys + [STALE]

    entry = widgets["ClipText"]
    assert entry["default"] == ""
    assert type(entry["default"]) is str
    assert entry["min_length"] == 0
    assert entry["max_length"] == 1000
    assert "minimum" not in entry and "maximum" not in entry, (
        "emitting character bounds as `minimum`/`maximum` is precisely the "
        "confusion that makes a renderer draw a slider over a text box"
    )
    assert "step" not in entry


def test_every_literal_option_list_contains_its_own_default(widgets):
    offenders = []
    for key, entry in widgets.items():
        if entry["type"] != "selection":
            continue
        options = entry.get("options")
        if options is None:
            continue
        if entry["default"] not in options:
            offenders.append((key, entry["default"], options))
    assert not offenders, offenders


def test_only_the_dynamic_key_has_no_literal_option_list(widgets):
    """Roadmap criterion 2, asserted about the file on disk rather than the loader.

    A frozen directory listing would be stale the moment a model file is added
    or removed, so the one key whose options come from a directory carries
    ``null`` here and is resolved at load.
    """
    dynamic = sorted(
        k
        for k, e in widgets.items()
        if e["type"] == "selection" and e.get("options") is None
    )
    assert dynamic == [DYNAMIC_KEY], dynamic

    entry = widgets[DYNAMIC_KEY]
    assert entry["options"] is None
    assert entry["default"] is None
    assert entry["options_from"] == (
        "app.helpers.miscellaneous.get_dfm_models_selection_values"
    )
    assert entry["default_from"] == (
        "app.helpers.miscellaneous.get_dfm_models_default_value"
    )


def test_no_other_entry_has_a_null_default(widgets):
    nulls = sorted(k for k, e in widgets.items() if e["default"] is None)
    assert nulls == [DYNAMIC_KEY], (
        "a null default means 'resolved at load'; on any other key it means the "
        "generator failed to type it: {}".format(nulls)
    )


# --------------------------------------------------------------------------
# gating
# --------------------------------------------------------------------------


def test_the_two_gate_mechanisms_have_the_measured_populations(widgets):
    toggles = [k for k, e in widgets.items() if (e.get("gate") or {}).get("mechanism") == "toggle"]
    selections = [
        k for k, e in widgets.items() if (e.get("gate") or {}).get("mechanism") == "selection"
    ]
    assert len(toggles) == EXPECTED_TOGGLE_GATES, (
        "expected {} toggle gates, found {}".format(EXPECTED_TOGGLE_GATES, len(toggles))
        + STALE
    )
    assert len(selections) == EXPECTED_SELECTION_GATES, (
        "expected {} selection gates, found {}".format(
            EXPECTED_SELECTION_GATES, len(selections)
        )
        + STALE
    )
    assert not set(toggles) & set(selections)


def test_every_toggle_gate_requires_its_parents_checked(widgets):
    """``requiredToggleValue`` is ``True`` for all 140 upstream, without exception."""
    wrong = [
        (k, e["gate"]["required_value"])
        for k, e in widgets.items()
        if (e.get("gate") or {}).get("mechanism") == "toggle"
        and e["gate"]["required_value"] is not True
    ]
    assert not wrong, wrong


def test_every_selection_gate_names_the_swap_model(widgets):
    gates = {
        k: e["gate"]
        for k, e in widgets.items()
        if (e.get("gate") or {}).get("mechanism") == "selection"
    }
    for key, gate in gates.items():
        assert gate["parents"] == ["SwapModelSelection"], (key, gate)
        assert gate["rule"] == "single", (key, gate)
        assert isinstance(gate["required_value"], str), (key, gate)


def test_every_gate_parent_names_a_key_that_exists(widgets):
    """Trap 3. Resolve the compound spellings or five parents look dangling."""
    dangling = []
    for key, entry in widgets.items():
        for parent in gate_parents(entry):
            if parent not in widgets:
                dangling.append((key, parent))
    assert not dangling, dangling


def test_a_gate_parent_is_always_the_control_type_its_mechanism_implies(widgets):
    wrong = []
    for key, entry in widgets.items():
        gate = entry.get("gate")
        if not gate:
            continue
        expected = "toggle" if gate["mechanism"] == "toggle" else "selection"
        for parent in gate["parents"]:
            if widgets[parent]["type"] != expected:
                wrong.append((key, parent, widgets[parent]["type"]))
    assert not wrong, wrong


def test_the_compound_gate_spellings_are_normalised_to_their_measured_rule(widgets):
    """The two undocumented compound forms, and what upstream actually does.

    Measured in ``app/ui/widgets/actions/common_actions.py`` (the ``'Toggle' in
    parent_widget_name`` branch), not inferred from the punctuation:

    - ``A|B`` -- the evaluator starts at True and clears it if **any** parent is
      unchecked. Despite the punctuation, that is an AND. Rule ``all``.
    - ``A, B`` -- the evaluator assigns, rather than combines, on each pass of
      the loop, so only the **last** parent has any effect at all. That is a bug
      upstream, and it is recorded as ``last`` rather than quietly improved into
      the ``all`` a reader would expect, because a renderer that hides a control
      upstream shows is a behaviour change no one asked for.
    """
    by_rule = {}
    for key, entry in widgets.items():
        gate = entry.get("gate")
        if gate:
            by_rule.setdefault(gate["rule"], []).append(key)

    assert sorted(by_rule["last"]) == [
        "OccluderXSegBlurSlider",
        "RestoreEyesMouthBlurSlider",
    ], sorted(by_rule.get("last", [])) + [STALE]
    assert sorted(by_rule["all"]) == [
        "FaceExpressionNormalizeLipsThresholdDecimalSlider",
        "FaceExpressionRetargetingEyesMultiplierDecimalSlider",
        "FaceExpressionRetargetingLipsMultiplierDecimalSlider",
    ], sorted(by_rule.get("all", [])) + [STALE]

    for key in by_rule["last"] + by_rule["all"]:
        assert len(widgets[key]["gate"]["parents"]) == 2, key
    for key in by_rule["single"]:
        assert len(widgets[key]["gate"]["parents"]) == 1, key


def test_no_gate_chain_is_cyclic_and_the_deepest_is_two(widgets):
    def depth(key, seen):
        assert key not in seen, "cyclic gate chain: {}".format(seen + (key,))
        parents = gate_parents(widgets[key])
        if not parents:
            return 0
        return 1 + max(depth(p, seen + (key,)) for p in parents)

    depths = {}
    for key in widgets:
        d = depth(key, ())
        depths[d] = depths.get(d, 0) + 1

    assert max(depths) == 2, depths
    assert depths == EXPECTED_DEPTHS, (
        "expected {}, found {}".format(EXPECTED_DEPTHS, depths) + STALE
    )


# --------------------------------------------------------------------------
# the dropped side effects
# --------------------------------------------------------------------------


def test_the_six_dropped_side_effects_are_named(widgets):
    """Upstream attached an ``exec_function`` to six keys; the current serializer
    drops every key starting with that prefix, which is how they went missing
    without anyone noticing. Naming them is what makes the gap visible.
    """
    recorded = {
        key: entry["exec_function"]
        for key, entry in widgets.items()
        if "exec_function" in entry
    }
    assert len(recorded) == EXPECTED_EXEC_FUNCTIONS, (
        "expected {} keys with a side effect, found {}: {}".format(
            EXPECTED_EXEC_FUNCTIONS, len(recorded), sorted(recorded)
        )
        + STALE
    )
    assert recorded == {
        "ProvidersPrioritySelection": (
            "app.ui.widgets.actions.control_actions.change_execution_provider"
        ),
        "ThemeSelection": "app.ui.widgets.actions.control_actions.change_theme",
        "VideoPlaybackCustomFpsToggle": (
            "app.ui.widgets.actions.control_actions.set_video_playback_fps"
        ),
        "ViewFaceCompareEnableToggle": (
            "app.ui.widgets.actions.layout_actions.fit_image_to_view_onchange"
        ),
        "ViewFaceMaskEnableToggle": (
            "app.ui.widgets.actions.layout_actions.fit_image_to_view_onchange"
        ),
        "nThreadsSlider": (
            "app.ui.widgets.actions.control_actions.change_threads_number"
        ),
    }, recorded


def test_no_entry_carries_empty_side_effect_arguments(widgets):
    """``exec_function_args`` is ``[]`` for all six upstream. An empty list on
    every entry is noise that reads like a feature."""
    assert not [k for k, e in widgets.items() if "exec_function_args" in e]


# --------------------------------------------------------------------------
# the dynamic option list, resolved at load rather than at freeze
# --------------------------------------------------------------------------


@pytest.fixture()
def clean_schema_cache(monkeypatch):
    """A loader with no cached listing and no ambient ``MODELS_DIR``."""
    from visoswap import schema

    monkeypatch.delenv(schema.MODELS_DIR_ENV, raising=False)
    schema.clear_dfm_cache()
    yield schema
    schema.clear_dfm_cache()


def test_a_populated_models_directory_yields_sorted_options(clean_schema_cache, tmp_path):
    """Two accepted extensions in, one rejected extension ignored, sorted.

    Sorted is a deliberate improvement on upstream, which returns whatever order
    ``os.listdir`` yields -- not stable across filesystems, so two machines with
    the same files would render the dropdown differently.
    """
    schema = clean_schema_cache
    models = tmp_path / "model_assets" / schema.DFM_SUBDIR
    models.mkdir(parents=True)
    (models / "zeta.dfm").write_bytes(b"")
    (models / "alpha.onnx").write_bytes(b"")
    (models / "notes.txt").write_bytes(b"")

    entry = schema.resolved_entry(DYNAMIC_KEY, tmp_path / "model_assets")

    assert entry["options"] == ["alpha.onnx", "zeta.dfm"]
    assert entry["default"] == "alpha.onnx"
    assert schema.dfm_models(tmp_path / "model_assets") == {
        "alpha.onnx": str(models / "alpha.onnx"),
        "zeta.dfm": str(models / "zeta.dfm"),
    }


def test_an_absent_models_directory_is_empty_and_does_not_raise(
    clean_schema_cache, tmp_path, caplog
):
    """Upstream swallows the exception and substitutes ``[]``, so a missing
    directory today produces an empty dropdown and no error anywhere. The list is
    still empty here -- that is the only thing a caller can do -- but the
    directory that was tried is surfaced, because "has no models" and "does not
    exist" are different facts and only one is the user's to fix.
    """
    schema = clean_schema_cache
    missing = tmp_path / "nowhere"

    with caplog.at_level("WARNING", logger="visoswap.schema"):
        entry = schema.resolved_entry(DYNAMIC_KEY, missing)

    assert entry["options"] == []
    assert entry["default"] == ""
    assert type(entry["default"]) is str
    assert str(missing / schema.DFM_SUBDIR) in caplog.text, caplog.text


def test_an_empty_models_directory_is_not_reported_as_missing(
    clean_schema_cache, tmp_path, caplog
):
    schema = clean_schema_cache
    models = tmp_path / "model_assets" / schema.DFM_SUBDIR
    models.mkdir(parents=True)

    with caplog.at_level("WARNING", logger="visoswap.schema"):
        entry = schema.resolved_entry(DYNAMIC_KEY, tmp_path / "model_assets")

    assert entry["options"] == []
    assert entry["default"] == ""
    assert not caplog.records, (
        "an existing but empty directory is a normal state, not a warning: "
        "{}".format(caplog.text)
    )


def test_the_models_directory_comes_from_the_argument_then_the_environment(
    clean_schema_cache, tmp_path, monkeypatch
):
    """Never from a request field (T-03-06)."""
    schema = clean_schema_cache
    from_env = tmp_path / "from-env"
    (from_env / schema.DFM_SUBDIR).mkdir(parents=True)
    (from_env / schema.DFM_SUBDIR / "env.dfm").write_bytes(b"")

    from_arg = tmp_path / "from-arg"
    (from_arg / schema.DFM_SUBDIR).mkdir(parents=True)
    (from_arg / schema.DFM_SUBDIR / "arg.dfm").write_bytes(b"")

    monkeypatch.setenv(schema.MODELS_DIR_ENV, str(from_env))
    assert schema.resolved_entry(DYNAMIC_KEY)["options"] == ["env.dfm"]
    assert schema.resolved_entry(DYNAMIC_KEY, from_arg)["options"] == ["arg.dfm"]

    monkeypatch.delenv(schema.MODELS_DIR_ENV)
    assert schema.resolve_models_dir() == schema.DEFAULT_MODELS_DIR.resolve()


def test_load_resolves_the_dynamic_key_and_leaves_the_other_two_hundred_alone(
    clean_schema_cache, tmp_path, widgets
):
    schema = clean_schema_cache
    models = tmp_path / "model_assets" / schema.DFM_SUBDIR
    models.mkdir(parents=True)
    (models / "only.dfm").write_bytes(b"")

    loaded = schema.load(tmp_path / "model_assets")

    assert len(loaded) == len(widgets)
    assert loaded[DYNAMIC_KEY]["options"] == ["only.dfm"]
    assert loaded[DYNAMIC_KEY]["default"] == "only.dfm"
    for key in widgets:
        if key != DYNAMIC_KEY:
            assert loaded[key] == widgets[key], key

    assert schema.WIDGETS[DYNAMIC_KEY]["options"] is None, (
        "load() must not mutate the frozen document -- the next caller with a "
        "different models directory would silently get this one's listing"
    )


def test_resolution_ends_at_the_resolved_default_not_the_frozen_null(
    clean_schema_cache, tmp_path
):
    """The dynamic key must not be the one key that resolves to ``None``."""
    import sqlite3

    from visoswap.settings import db, store

    schema = clean_schema_cache
    models = tmp_path / "model_assets" / schema.DFM_SUBDIR
    models.mkdir(parents=True)
    (models / "only.dfm").write_bytes(b"")

    connection = sqlite3.connect(tmp_path / "project.db")
    db.apply_settings_schema(connection)
    try:
        value = store.resolve(
            connection,
            DYNAMIC_KEY,
            "project-a",
            models_dir=tmp_path / "model_assets",
        )
        assert value == "only.dfm"

        # A second model on disk, so the override names an option that actually
        # exists in the same directory the read resolves against. Plan 03-02
        # made the write side check membership, and the previous spelling wrote
        # a name nothing answered to while validating it against the ambient
        # repository models directory rather than this one -- which passed only
        # for as long as that directory stayed empty.
        (models / "zzz-chosen.dfm").write_bytes(b"")
        schema.clear_dfm_cache()

        store.set_project(
            connection,
            "project-a",
            DYNAMIC_KEY,
            "zzz-chosen.dfm",
            models_dir=tmp_path / "model_assets",
        )
        assert (
            store.resolve(
                connection,
                DYNAMIC_KEY,
                "project-a",
                models_dir=tmp_path / "model_assets",
            )
            == "zzz-chosen.dfm"
        )
    finally:
        connection.close()
