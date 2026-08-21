"""The schema and the Phase 2 engine fixture must agree on every typed default.

Two files, two scripts, **one shape rule**. ``tools/dump_engine_settings.py``
owns ``shape_of`` and ``coerce``; ``tools/generate_schema.py`` imports both
rather than restating them. This test is what proves that import is really shared
rather than merely intended -- a copied rule looks identical on the day it is
copied and diverges on the day upstream adds a widget shape.

**If this test fails, the fixture and the schema have forked.**
``tests/fixtures/engine_settings.json`` types the values the Phase 2 smoke test
feeds the engine; ``visoswap/schema/schema.json`` types the values everything
after Phase 3 feeds it. A fork therefore does not fail at load. It fails as a
``TypeError`` deep inside inference, on one key, in a stack that names a tensor
operation and not the file that mistyped it. Regenerate both from the same
interpreter before changing anything else.

The one permitted divergence is ``DFMModelSelection``, and it is deliberate: the
fixture resolves its callable default to the empty string, while the schema emits
``null`` and resolves it against the models directory at load. Baking a directory
listing into a committed file is what plan 03-01 exists to avoid.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "visoswap" / "schema" / "schema.json"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "engine_settings.json"

DYNAMIC_KEY = "DFMModelSelection"


@pytest.fixture(scope="module")
def schema_widgets():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)["widgets"]


@pytest.fixture(scope="module")
def fixture_values():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    merged = {}
    merged.update(payload["project"])
    merged.update(payload["global"])
    return merged, payload


def test_both_files_describe_the_same_key_set(schema_widgets, fixture_values):
    merged, _ = fixture_values
    only_schema = sorted(set(schema_widgets) - set(merged))
    only_fixture = sorted(set(merged) - set(schema_widgets))
    assert not only_schema and not only_fixture, (
        "the two generators disagree about which keys exist.\n"
        "  only in schema.json: {}\n"
        "  only in engine_settings.json: {}".format(only_schema, only_fixture)
    )


def test_both_files_agree_about_which_tier_each_key_is_in(schema_widgets, fixture_values):
    _, payload = fixture_values
    wrong = [
        (key, schema_widgets[key]["tier"], "project" in payload and key in payload["project"])
        for key in payload["project"]
        if schema_widgets[key]["tier"] != "project"
    ]
    wrong += [
        (key, schema_widgets[key]["tier"], "global")
        for key in payload["global"]
        if schema_widgets[key]["tier"] != "global"
    ]
    assert not wrong, wrong


def test_every_non_dynamic_default_agrees_in_value_and_in_type(
    schema_widgets, fixture_values
):
    merged, _ = fixture_values
    disagreements = []
    for key, entry in schema_widgets.items():
        if key == DYNAMIC_KEY:
            continue
        theirs = merged[key]
        mine = entry["default"]
        # bool before int: `isinstance(True, int)` is True, so a toggle that had
        # decayed into the integer 1 in one file and stayed True in the other
        # would compare equal and pass a value-only check.
        if type(mine) is not type(theirs) or mine != theirs:
            disagreements.append((key, entry["type"], mine, theirs))
    assert not disagreements, (
        "schema.json and engine_settings.json disagree on {} keys "
        "(key, type, schema, fixture):\n{}".format(
            len(disagreements), "\n".join(map(str, disagreements))
        )
    )


def test_the_dynamic_key_is_the_only_permitted_divergence(schema_widgets, fixture_values):
    merged, _ = fixture_values
    assert schema_widgets[DYNAMIC_KEY]["default"] is None
    assert merged[DYNAMIC_KEY] == "", (
        "the fixture resolves the callable default to the empty string; if that "
        "changed, this exemption is hiding a real fork"
    )
