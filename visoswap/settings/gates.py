"""Which controls a renderer should show. **Nothing else, and nowhere else.**

READ THIS BEFORE CALLING ANYTHING HERE
--------------------------------------
**Resolution does not call this module and must never call it.** A key whose
gate is closed still resolves to a value. The engine reads
``parameters[key]`` unconditionally -- there is no branch in the swap loop that
asks whether a control was on screen -- and it reads the parent toggle itself,
separately, as its own key. Filtering resolved values by visibility would not
tidy the output; it would change what gets swapped, by removing keys the engine
then raises a ``KeyError`` on deep inside the frame loop.

Gating is presentational. It says what a user is shown, not what the engine is
given. ``tests/test_settings_gates.py`` closes a gate, resolves the child key
and asserts the value comes back anyway, and a second test asserts by AST that
``store.py`` does not import this module at all -- because a rule that is only
written down is a rule that gets refactored away.

The two mechanisms, as upstream actually implements them
--------------------------------------------------------
Measured at ``app/ui/widgets/actions/common_actions.py:104-162`` in the
read-only checkout, and normalised into the schema by plan 03-01. This module
reads the normalised gate and never re-parses upstream's punctuation.

* **toggle** -- the gate is satisfied when the parents' combined checked state
  equals ``required_value``. Three combining rules, and two of them are not
  what their spelling suggests:

  - ``single``: one parent, and its state is the combined state.
  - ``all``: spelled ``'A|B'`` upstream. The pipe reads like OR. **It is an
    AND**: the loop starts at ``True`` and clears the flag on any unchecked
    parent. Three keys use it.
  - ``last``: spelled ``'A, B'`` upstream. The loop body is a plain assignment,
    so each iteration discards the previous one's answer and **only the last
    parent decides**. Two keys use it. That is upstream's behaviour, recorded
    as measured rather than improved: a settings layer that disagreed with the
    window about which controls are live would be a worse bug than the one it
    fixed.

* **selection** -- the gate is satisfied when the parent selection's current
  value equals ``required_value`` exactly. Four keys use it, all four on the
  swapper model selection.

Transitive visibility, and which parents count
-----------------------------------------------
A key is visible when its own gate is satisfied **and** every parent that
decides that gate is itself visible. Upstream does not do this: it evaluates one
widget against its immediate parents' *values* and never asks whether those
parents are on screen, so a control nested under a switched-off section can
still be drawn. That is a real defect and this is the deliberate divergence from
upstream in this file -- the only one.

"Every parent that decides" is meant precisely. Under the ``last`` rule the
earlier parents are discarded from the value test, so they are discarded from
the visibility chain too, rather than half-counting: one notion of which parents
matter, used for both questions. In the committed schema the choice is not
observable -- neither ``last`` key has a gated parent -- so
``tests/test_settings_gates.py`` pins it against a hand-built schema, where it
is.

Measured shape of the real data: 201 keys, 57 ungated, 133 at depth 1, 11 at
depth 2, and no cycles. The cycle guard below is therefore a change detector
rather than a routine path -- if it ever fires, upstream's layout dicts grew a
loop and the loud failure is the entire point.

Standard library only, and nothing from this package except ``visoswap.schema``.
"""

from visoswap import schema

__all__ = [
    "is_visible",
    "visible_keys",
    "deciding_parents",
    "GateCycle",
    "MissingGateValue",
]


class GateCycle(ValueError):
    """Raised when a gate chain revisits a key it has already passed through.

    Not a defensive flourish. The chain is walked recursively and a loop would
    otherwise recurse until the interpreter gave up, with a stack trace naming
    every key in the cycle a hundred times and the actual fact -- that two
    controls each hide the other -- nowhere in it.
    """


class MissingGateValue(KeyError):
    """Raised when the values mapping has no entry for a gate parent.

    ``store.resolve_all`` returns every key in the schema, always, so a caller
    passing that mapping can never hit this. A caller who passed a partial dict
    can, and the answer would otherwise be silently wrong rather than absent.
    """


def _gate_of(key, widgets):
    try:
        return widgets[key].get("gate")
    except KeyError:
        raise schema.UnknownSettingsKey(key) from None


def deciding_parents(gate):
    """The parents whose values actually decide this gate.

    Every parent except under the ``last`` rule, where upstream's loop discards
    all but the final one. See the module docstring.
    """
    if gate is None:
        return []
    if gate["rule"] == "last":
        return list(gate["parents"][-1:])
    return list(gate["parents"])


def _value_of(key, values):
    try:
        return values[key]
    except KeyError:
        raise MissingGateValue(
            "no value for {}, which decides a gate".format(key)
        ) from None


def _own_gate_satisfied(gate, values):
    parents = deciding_parents(gate)
    if gate["mechanism"] == "selection":
        # One parent by construction, and plain equality against its current
        # value -- upstream compares `requiredSelectionValue` with the combo
        # box's `currentText()`.
        return _value_of(parents[-1], values) == gate["required_value"]

    # Toggle. `single` and `last` both reduce to one parent by the time they
    # reach here; `all` is the AND that its pipe spelling disguises.
    combined = True
    for parent in parents:
        if not _value_of(parent, values):
            combined = False
    return combined == gate["required_value"]


def _visible(key, values, widgets, chain):
    if key in chain:
        raise GateCycle(
            "gate chain revisits {}: {}".format(key, " -> ".join(chain + (key,)))
        )
    gate = _gate_of(key, widgets)
    if gate is None:
        return True
    if not _own_gate_satisfied(gate, values):
        return False
    onward = chain + (key,)
    return all(
        _visible(parent, values, widgets, onward)
        for parent in deciding_parents(gate)
    )


def is_visible(key, values, widgets=None):
    """Whether a renderer should draw ``key``, given a mapping of current values.

    ``values`` is what ``store.resolve_all`` returns: every key in the schema
    with its effective value. ``widgets`` defaults to the committed schema and
    exists so a test can hand in a hand-built one.

    **This answer must never reach the engine.** See the module docstring.
    """
    return _visible(key, values, widgets or schema.WIDGETS, ())


def visible_keys(values, widgets=None):
    """The sorted subset of keys a renderer should draw. Same warning applies."""
    widgets = widgets or schema.WIDGETS
    return sorted(k for k in widgets if is_visible(k, values, widgets))
