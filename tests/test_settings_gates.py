"""Gate evaluation: both mechanisms, both compound spellings, and its absence
from the resolution path.

The last one is the point of the file. A gate says what a user is shown. The
engine reads ``parameters[key]`` unconditionally and reads the parent toggle
separately as its own key, so a settings layer that filtered resolved values by
visibility would not be tidying the output -- it would be removing keys the swap
loop then raises on, several hundred frames after the control that caused it
went off screen.

The compound spellings are tested because both of them mean the opposite of what
they look like, measured at ``app/ui/widgets/actions/common_actions.py:137-162``
in the read-only checkout:

* ``'A|B'`` reads like OR. Upstream's loop starts at ``True`` and clears the
  flag on any unchecked parent, so it is an **AND**.
* ``'A, B'`` reads like a list of requirements. The loop body is a plain
  assignment, so each iteration throws away the previous answer and **only the
  last parent decides**.

Plan 03-01 recorded these in the schema as ``all`` and ``last``. Reproducing
them faithfully matters more than fixing them: a settings layer that disagreed
with the window about which controls are live would be a worse bug than the one
it corrected.
"""

import ast
import pathlib
import sqlite3

import pytest

from visoswap import schema
from visoswap.settings import db, gates, store

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def connection_for_gates(tmp_path):
    conn = sqlite3.connect(tmp_path / "project.db")
    db.apply_settings_schema(conn)
    yield conn
    conn.close()


def values_with(**overrides):
    """Every key at its schema default, with the named ones overridden."""
    values = {key: schema.effective_default(key) for key in schema.WIDGETS}
    values.update(overrides)
    return values


# --------------------------------------------------------------------------
# the premise: the real schema still has the gates this file exercises
# --------------------------------------------------------------------------


def test_the_measured_gate_shape_still_holds():
    """201 keys, 57 ungated, 140 toggle gates, 4 selection gates, no cycles.

    Measured in plan 03-01 and restated here because every test below picks its
    subject by querying this shape rather than by naming a key that was true
    once.
    """
    gated = [k for k in schema.WIDGETS if schema.WIDGETS[k]["gate"]]
    assert len(schema.WIDGETS) == 201
    assert len(gated) == 144
    mechanisms = {}
    rules = {}
    for key in gated:
        gate = schema.WIDGETS[key]["gate"]
        mechanisms[gate["mechanism"]] = mechanisms.get(gate["mechanism"], 0) + 1
        rules[gate["rule"]] = rules.get(gate["rule"], 0) + 1
    assert mechanisms == {"toggle": 140, "selection": 4}
    assert rules == {"single": 139, "all": 3, "last": 2}


def test_every_gate_parent_is_itself_a_key_with_the_shape_the_gate_assumes():
    """A gate naming a key that does not exist evaluates to nothing at all."""
    wrong = []
    for key, entry in schema.WIDGETS.items():
        gate = entry["gate"]
        if not gate:
            continue
        for parent in gate["parents"]:
            if parent not in schema.WIDGETS:
                wrong.append((key, parent, "no such key"))
            elif schema.WIDGETS[parent]["type"] != gate["mechanism"]:
                wrong.append((key, parent, schema.WIDGETS[parent]["type"]))
    assert not wrong, wrong


# --------------------------------------------------------------------------
# a simple toggle gate
# --------------------------------------------------------------------------


def a_single_toggle_gated_key():
    return next(
        key
        for key, entry in schema.WIDGETS.items()
        if entry["gate"]
        and entry["gate"]["mechanism"] == "toggle"
        and entry["gate"]["rule"] == "single"
        and entry["gate"]["required_value"] is True
        # A parent that is itself ungated, so this test measures one level.
        and not schema.WIDGETS[entry["gate"]["parents"][0]]["gate"]
    )


def test_a_toggle_gate_is_open_when_its_parent_is_on_and_closed_when_it_is_off():
    key = a_single_toggle_gated_key()
    parent = schema.WIDGETS[key]["gate"]["parents"][0]

    assert gates.is_visible(key, values_with(**{parent: True})) is True
    assert gates.is_visible(key, values_with(**{parent: False})) is False


def test_an_ungated_key_is_always_visible():
    ungated = next(k for k, e in schema.WIDGETS.items() if not e["gate"])
    assert gates.is_visible(ungated, values_with()) is True


# --------------------------------------------------------------------------
# a selection gate
# --------------------------------------------------------------------------


def test_a_selection_gate_matches_its_required_value_and_nothing_else():
    key = next(
        k
        for k, e in schema.WIDGETS.items()
        if e["gate"] and e["gate"]["mechanism"] == "selection"
    )
    gate = schema.WIDGETS[key]["gate"]
    parent = gate["parents"][0]
    required = gate["required_value"]
    other = next(
        option
        for option in schema.WIDGETS[parent]["options"]
        if option != required
    )

    assert gates.is_visible(key, values_with(**{parent: required})) is True
    assert gates.is_visible(key, values_with(**{parent: other})) is False


# --------------------------------------------------------------------------
# the two compound spellings
# --------------------------------------------------------------------------


def test_the_pipe_spelling_is_an_and_despite_reading_like_an_or():
    key = next(
        k for k, e in schema.WIDGETS.items() if e["gate"] and e["gate"]["rule"] == "all"
    )
    first, second = schema.WIDGETS[key]["gate"]["parents"]
    assert schema.WIDGETS[key]["gate"]["required_value"] is True

    # Both parents on, and every ancestor of those parents on too, so the
    # transitive half of the answer is not what is being measured here.
    ancestors = {}
    for parent in (first, second):
        for chained in _chain_of(parent):
            ancestors[chained] = True

    assert gates.is_visible(
        key, values_with(**dict(ancestors, **{first: True, second: True}))
    ) is True
    assert gates.is_visible(
        key, values_with(**dict(ancestors, **{first: True, second: False}))
    ) is False, "an OR would have shown this"
    assert gates.is_visible(
        key, values_with(**dict(ancestors, **{first: False, second: True}))
    ) is False


def test_the_comma_spelling_lets_only_the_last_parent_decide():
    key = next(
        k for k, e in schema.WIDGETS.items() if e["gate"] and e["gate"]["rule"] == "last"
    )
    first, last = schema.WIDGETS[key]["gate"]["parents"]
    assert schema.WIDGETS[key]["gate"]["required_value"] is True

    assert gates.is_visible(key, values_with(**{first: False, last: True})) is True, (
        "the first parent is discarded by upstream's loop and must be discarded "
        "here too -- reproducing the window's behaviour is the requirement"
    )
    assert gates.is_visible(key, values_with(**{first: True, last: False})) is False


# --------------------------------------------------------------------------
# transitive chains
# --------------------------------------------------------------------------


def _chain_of(key):
    """Every ancestor of ``key`` in its gate chain, nearest first."""
    found = []
    frontier = [key]
    while frontier:
        current = frontier.pop()
        gate = schema.WIDGETS[current]["gate"]
        if not gate:
            continue
        for parent in gates.deciding_parents(gate):
            if parent not in found:
                found.append(parent)
                frontier.append(parent)
    return found


def a_two_level_key():
    for key, entry in schema.WIDGETS.items():
        gate = entry["gate"]
        if not gate or gate["rule"] != "single" or gate["required_value"] is not True:
            continue
        parent = gate["parents"][0]
        grandparent_gate = schema.WIDGETS[parent]["gate"]
        if (
            grandparent_gate
            and grandparent_gate["mechanism"] == "toggle"
            and grandparent_gate["rule"] == "single"
            and grandparent_gate["required_value"] is True
        ):
            return key, parent, grandparent_gate["parents"][0]
    raise AssertionError("the schema has no two-level toggle chain any more")


def test_a_child_is_hidden_when_its_grandparent_is_off_even_though_its_parent_is_on():
    """The one deliberate divergence from upstream.

    Upstream evaluates a widget against its immediate parents' *values* and
    never asks whether those parents are on screen, so a control nested under a
    switched-off section can still be drawn. That is a defect, and this is where
    it is not reproduced.
    """
    key, parent, grandparent = a_two_level_key()

    assert gates.is_visible(
        key, values_with(**{parent: True, grandparent: True})
    ) is True
    assert gates.is_visible(
        key, values_with(**{parent: True, grandparent: False})
    ) is False
    assert gates.is_visible(
        key, values_with(**{parent: False, grandparent: True})
    ) is False


def test_the_measured_chain_depth_is_two_and_there_are_eleven_at_that_depth():
    """Plan 03-02's prose says eight. It is eleven -- counted, not recalled.

    The number matters only as a change detector, but a change detector that
    disagrees with the tree it is detecting changes in is worse than none.
    """
    depths = {}
    for key in schema.WIDGETS:
        depths[key] = len(_levels(key))
    census = {}
    for depth in depths.values():
        census[depth] = census.get(depth, 0) + 1
    assert census == {0: 57, 1: 133, 2: 11}, census


def _levels(key, seen=()):
    gate = schema.WIDGETS[key]["gate"]
    if not gate:
        return []
    deepest = []
    for parent in gates.deciding_parents(gate):
        candidate = _levels(parent, seen + (key,))
        if len(deepest) < len(candidate):
            deepest = candidate
    return [key] + deepest


# --------------------------------------------------------------------------
# a cycle
# --------------------------------------------------------------------------


def test_a_cyclic_gate_raises_rather_than_recursing_forever():
    """The real schema has no cycle, so the cycle is built by hand.

    If this ever fires against the committed schema instead, upstream's layout
    dicts grew a loop and the loud failure is the entire point.
    """
    def gate_on(parent):
        return {
            "mechanism": "toggle",
            "rule": "single",
            "required_value": True,
            "parents": [parent],
        }

    widgets = {
        "AToggle": {"type": "toggle", "gate": gate_on("BToggle")},
        "BToggle": {"type": "toggle", "gate": gate_on("AToggle")},
    }
    values = {"AToggle": True, "BToggle": True}

    with pytest.raises(gates.GateCycle) as raised:
        gates.is_visible("AToggle", values, widgets)
    assert "AToggle" in str(raised.value)


def test_a_key_gated_on_itself_raises_too():
    """The one-step cycle, which a visited-set that only looked at parents misses."""
    widgets = {
        "AToggle": {
            "type": "toggle",
            "gate": {
                "mechanism": "toggle",
                "rule": "single",
                "required_value": True,
                "parents": ["AToggle"],
            },
        }
    }
    with pytest.raises(gates.GateCycle):
        gates.is_visible("AToggle", {"AToggle": True}, widgets)


# --------------------------------------------------------------------------
# and the whole reason the module is quarantined
# --------------------------------------------------------------------------


def test_a_gated_off_key_still_resolves_to_its_value(connection_for_gates):
    """Asserted, not asserted about.

    The engine reads a gated key's value unconditionally and reads the parent
    toggle separately. Filtering by visibility would change what gets swapped.
    """
    key, parent, _grandparent = a_two_level_key()
    connection = connection_for_gates

    store.set_project(connection, "project-a", parent, False)
    store.set_project(connection, "project-a", key, _an_override_for(key))

    values = store.resolve_all(connection, "project-a")
    assert gates.is_visible(key, values) is False, "the premise: the gate is shut"
    assert values[key] == _an_override_for(key)
    assert store.resolve(connection, key, "project-a") == _an_override_for(key)
    assert key in store.resolve_parameters(connection, "project-a")


def _an_override_for(key):
    entry = schema.resolved_entry(key)
    if entry["type"] == "toggle":
        return not entry["default"]
    if entry["type"] == "text":
        return "anything"
    if entry["type"] == "selection":
        return next(o for o in entry["options"] if o != entry["default"])
    step = entry["step"]
    candidate = entry["default"] + step
    if entry["maximum"] < candidate:
        candidate = entry["default"] - step
    return float(candidate) if entry["type"] == "float" else int(candidate)


def test_the_resolution_path_does_not_import_the_gate_evaluator():
    """A rule that is only written down is a rule that gets refactored away."""
    tree = ast.parse(
        (REPO_ROOT / "visoswap" / "settings" / "store.py").read_text(encoding="utf-8")
    )
    imported = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not [m for m in imported if m.rsplit(".", 1)[-1] == "gates"], imported

    source = (REPO_ROOT / "visoswap" / "settings" / "store.py").read_text(
        encoding="utf-8"
    )
    assert "is_visible" not in source
    assert "visible_keys" not in source
