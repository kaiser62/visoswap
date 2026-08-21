"""The two deferred engine paths must stay recorded, in all three places.

Phase 2 exercises LivePortrait and *only* LivePortrait. The CLIPseg text-masking
path and the DFM swap path were descoped by the project owner
(``02-DECISION-deferred-paths.md``) because neither has weights on any reachable
machine. Nothing here re-litigates that. This file exists to stop the *record* of
it from quietly evaporating.

Three places have to keep agreeing:

1. ``docs/engine-path-coverage.md`` -- a final, deferred verdict per path, naming
   the specific absent asset and naming Phase 4 as the owner.
2. ``docs/engine-extra-assets.md`` -- a matching absence entry for that asset.
3. ``.planning/WINDOWS.md`` -- ledger item 1 waived, in **both** the markdown
   table and the JSON block, for a reason that names both assets and Phase 4.

Everything is **parametrised over both paths**, deliberately. A test shaped
around CLIPseg with DFM bolted on the side would half-pass when someone drops the
DFM row, and a half-passing test is worse than no test: it looks like coverage.

This test reads text files. It imports no engine module, constructs no model and
runs no inference, so it is Qt-free and weights-free by construction and costs
milliseconds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

COVERAGE_DOC = REPO_ROOT / "docs" / "engine-path-coverage.md"
EXTRA_ASSETS_DOC = REPO_ROOT / "docs" / "engine-extra-assets.md"
LEDGER = REPO_ROOT / ".planning" / "WINDOWS.md"

LEDGER_ITEM_ID = 1
OWNING_PHASE_PATTERN = re.compile(r"phase\s*4", re.IGNORECASE)


@dataclass(frozen=True)
class DeferredPath:
    """One descoped engine path and the asset whose absence descoped it."""

    key: str
    #: How the path is named in a coverage table row.
    row_label: re.Pattern[str]
    #: The specific absent asset. This is the thing that must never go unnamed.
    asset: re.Pattern[str]
    #: Human description, for assertion messages only.
    asset_name: str


DEFERRED_PATHS = [
    DeferredPath(
        key="clipseg",
        row_label=re.compile(r"clipseg", re.IGNORECASE),
        asset=re.compile(r"rd64-uni-refined\.pth", re.IGNORECASE),
        asset_name="rd64-uni-refined.pth",
    ),
    DeferredPath(
        key="dfm",
        row_label=re.compile(r"\bDFM\b"),
        # `\b` after `dfm` is what keeps this from matching
        # `EngineContext.dfm_models_data`, which is a different thing entirely --
        # a missing *populator*, not a missing model file. See
        # test_dfm_asset_pattern_does_not_alias_the_context_field.
        asset=re.compile(r"\.dfm\b", re.IGNORECASE),
        asset_name="the .dfm extension",
    ),
]

PATH_IDS = [p.key for p in DEFERRED_PATHS]


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    assert path.is_file(), (
        f"{path} does not exist. This is not a test-environment problem: the "
        f"deferral record lives in this file, and without it the descoping of "
        f"CLIPseg and DFM is undocumented."
    )
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} exists but is empty."
    return text


def _table_rows(markdown: str) -> list[list[str]]:
    """Every markdown table row, as a list of stripped cells."""
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= {"-", ":", " "} and c for c in cells):
            continue  # separator row
        rows.append(cells)
    return rows


def _coverage_row(path: DeferredPath) -> str:
    """The whole coverage-table row for one path, re-joined as text."""
    matches = [
        row for row in _table_rows(_read(COVERAGE_DOC)) if row and path.row_label.search(row[0])
    ]
    assert matches, (
        f"docs/engine-path-coverage.md has no status-table row whose first cell "
        f"names {path.key}. The deferral of that path is no longer recorded in "
        f"the coverage document."
    )
    assert len(matches) == 1, (
        f"docs/engine-path-coverage.md has {len(matches)} status-table rows "
        f"naming {path.key}; expected exactly one, so that 'the row says X' is "
        f"an unambiguous claim."
    )
    return " | ".join(matches[0])


def _ledger_json_entry(item_id: int) -> dict:
    text = _read(LEDGER)
    block = re.search(r"````+json\s*(.*?)\s*````+", text, re.DOTALL)
    assert block, ".planning/WINDOWS.md has no fenced JSON block."
    entries = json.loads(block.group(1))
    matches = [e for e in entries if e.get("id") == item_id]
    assert matches, f"Ledger JSON block has no entry with id {item_id}."
    return matches[0]


def _ledger_table_row(item_id: int) -> list[str]:
    matches = [
        row for row in _table_rows(_read(LEDGER)) if row and row[0] == str(item_id)
    ]
    assert matches, f"Ledger markdown table has no row with id {item_id}."
    assert len(matches) == 1, f"Ledger markdown table has {len(matches)} rows with id {item_id}."
    return matches[0]


def _ledger_table_field(item_id: int, header: str) -> str:
    headers = _table_rows(_read(LEDGER))
    header_row = next((r for r in headers if r and r[0] == "id"), None)
    assert header_row, "Ledger markdown table has no header row starting with 'id'."
    assert header in header_row, f"Ledger table has no '{header}' column; got {header_row}."
    return _ledger_table_row(item_id)[header_row.index(header)]


# --------------------------------------------------------------------------
# Non-vacuity: the fixtures this file reasons over must be real
# --------------------------------------------------------------------------


def test_both_documents_and_the_ledger_exist_and_are_substantive():
    """Guard against every later assertion passing over an empty or absent file."""
    for path in (COVERAGE_DOC, EXTRA_ASSETS_DOC, LEDGER):
        text = _read(path)
        assert len(text) > 500, f"{path} is only {len(text)} chars; that is not a record."


def test_exactly_two_paths_are_parametrised():
    """Two paths were descoped. If that count changes, this file must change too."""
    assert len(DEFERRED_PATHS) == 2, (
        "The decision descoped exactly two paths: CLIPseg and DFM. A third entry "
        "here, or a missing one, means the parametrisation no longer mirrors the "
        "decision it is supposed to pin."
    )
    assert {p.key for p in DEFERRED_PATHS} == {"clipseg", "dfm"}


def test_dfm_asset_pattern_does_not_alias_the_context_field():
    """`.dfm` must mean a model file, never `EngineContext.dfm_models_data`.

    Anti-aliasing guard. The DFM deferral has two halves -- absent `.dfm` files
    *and* an absent populator for `EngineContext.dfm_models_data` -- and a regex
    that matched the second while claiming to check the first would report the
    absent-asset name as recorded when it was not.
    """
    dfm = next(p for p in DEFERRED_PATHS if p.key == "dfm")
    assert not dfm.asset.search("EngineContext.dfm_models_data")
    assert not dfm.asset.search("model_assets/dfm_models/")
    assert dfm.asset.search("any `.dfm` file")
    assert dfm.asset.search("Human-Face.dfm")


# --------------------------------------------------------------------------
# 1. The coverage document
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_coverage_row_marks_the_path_deferred(path: DeferredPath):
    row = _coverage_row(path)
    assert re.search(r"deferred", row, re.IGNORECASE), (
        f"The {path.key} row in docs/engine-path-coverage.md no longer says "
        f"'deferred'. Row: {row[:400]}"
    )


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_coverage_row_names_the_absent_asset(path: DeferredPath):
    row = _coverage_row(path)
    assert path.asset.search(row), (
        f"The {path.key} row in docs/engine-path-coverage.md no longer names "
        f"{path.asset_name}. A deferral that does not name what is missing is "
        f"not actionable by whoever picks it up."
    )


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_coverage_row_names_phase_4_as_owner(path: DeferredPath):
    row = _coverage_row(path)
    assert OWNING_PHASE_PATTERN.search(row), (
        f"The {path.key} row in docs/engine-path-coverage.md no longer names "
        f"Phase 4 as the owning phase. An unowned deferral is an abandoned one."
    )


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_coverage_row_cites_the_decision_of_record(path: DeferredPath):
    row = _coverage_row(path)
    assert "02-DECISION-deferred-paths" in row, (
        f"The {path.key} row in docs/engine-path-coverage.md no longer cites "
        f"02-DECISION-deferred-paths.md. Without the citation the deferral reads "
        f"as an oversight rather than a decision."
    )


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_coverage_row_says_the_path_is_left_intact(path: DeferredPath):
    """Deferred is not deleted. Re-enabling must never mean re-vendoring."""
    row = _coverage_row(path)
    assert re.search(r"vendored and left intact", row, re.IGNORECASE), (
        f"The {path.key} row in docs/engine-path-coverage.md no longer states "
        f"that the path stays vendored and left intact."
    )


# --------------------------------------------------------------------------
# 2. The extra-asset document
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_extra_assets_doc_has_an_entry_for_the_asset(path: DeferredPath):
    headings = [
        line.strip()
        for line in _read(EXTRA_ASSETS_DOC).splitlines()
        if line.strip().startswith("###")
    ]
    assert headings, "docs/engine-extra-assets.md has no `###` asset entries at all."
    matching = [h for h in headings if path.asset.search(h)]
    assert matching, (
        f"docs/engine-extra-assets.md has no entry heading naming "
        f"{path.asset_name}. Headings found: {headings}"
    )


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_extra_assets_doc_gives_the_asset_a_re_enablement_path(path: DeferredPath):
    text = _read(EXTRA_ASSETS_DOC)
    assert re.search(r"^##\s*Re-enablement", text, re.MULTILINE), (
        "docs/engine-extra-assets.md has lost its Re-enablement section."
    )
    section = text.split("## Re-enablement", 1)[1]
    assert path.asset.search(section) or path.row_label.search(section), (
        f"The Re-enablement section of docs/engine-extra-assets.md says nothing "
        f"about {path.key}. Recording an absence without recording how to undo "
        f"it just documents a dead end."
    )


def test_extra_assets_doc_records_the_latent_clip_download():
    """The ~335 MB ViT-B/16 fetch is the input to a deserializer, not a size note.

    Descoping CLIPseg does not make this safe. ``face_masks.py:254`` constructs
    ``CLIPDensePredT``, which reaches ``clipseg.py:91`` -> ``clip.load`` -> the
    download into ``~/.cache/clip`` and then ``clip.py:134``/``clip.py:141``,
    all **before** ``face_masks.py:256`` looks for the missing
    ``rd64-uni-refined.pth``. If this stops being written down, the next reader
    concludes the missing file protects them. It does not.
    """
    text = _read(EXTRA_ASSETS_DOC)
    for needle in ("ViT-B/16", "~/.cache/clip", "clip.py:141", "ClipEnableToggle"):
        assert needle in text, (
            f"docs/engine-extra-assets.md no longer mentions {needle!r}, which is "
            f"part of the ordering argument for why the latent CLIP download is "
            f"not made safe by the CLIPseg deferral."
        )


def test_extra_assets_doc_claims_no_measured_digest():
    """Nothing was downloaded, so nothing has a measured hash. Say so, keep saying so."""
    text = _read(EXTRA_ASSETS_DOC)
    assert re.search(r"no measured digest", text, re.IGNORECASE), (
        "docs/engine-extra-assets.md no longer carries its 'no measured digest' "
        "disclaimer. Any digest in that file is read out of vendored source, not "
        "measured from an artifact, and a reader who forgets that will trust a "
        "number nobody verified."
    )


def test_extra_assets_doc_carries_the_phase_4_forward_note():
    text = _read(EXTRA_ASSETS_DOC)
    assert "BACKEND-01" in text, (
        "docs/engine-extra-assets.md no longer names BACKEND-01. The forward-carry "
        "note -- that the model-completeness check derives its set from the "
        "manifest and therefore cannot see any of these assets -- is the whole "
        "point of writing them down for Phase 4."
    )
    assert OWNING_PHASE_PATTERN.search(text)


# --------------------------------------------------------------------------
# 3. The Broken Windows ledger
# --------------------------------------------------------------------------


def test_ledger_item_1_is_waived_in_both_representations():
    """The table and the JSON block are two copies. Hand-editing one diverges them."""
    assert _ledger_json_entry(LEDGER_ITEM_ID)["status"] == "waived"
    assert _ledger_table_field(LEDGER_ITEM_ID, "status") == "waived"


def test_ledger_item_1_is_waived_not_fixed():
    """Marking it fixed would assert something untrue.

    The item says "Phase 2 exercises all three". Phase 2 exercises one.
    """
    assert _ledger_json_entry(LEDGER_ITEM_ID)["status"] != "fixed", (
        "Ledger item 1 is marked fixed. Its own description claims Phase 2 "
        "exercises all three paths; Phase 2 exercises LivePortrait alone, so "
        "'fixed' asserts something false. It must be waived."
    )


def test_ledger_counts_are_consistent():
    text = _read(LEDGER)
    counts = {
        key: int(re.search(rf"^{key}:\s*(\d+)", text, re.MULTILINE).group(1))
        for key in ("open_count", "waived_count", "fixed_count", "total_count")
    }
    entries = json.loads(re.search(r"````+json\s*(.*?)\s*````+", text, re.DOTALL).group(1))
    assert counts["open_count"] == sum(1 for e in entries if e["status"] == "open")
    assert counts["waived_count"] == sum(1 for e in entries if e["status"] == "waived")
    assert counts["total_count"] == len(entries)
    assert counts["open_count"] == 0, (
        "The ledger has open items. /gsd-ship blocks while open_count > 0."
    )


@pytest.mark.parametrize("path", DEFERRED_PATHS, ids=PATH_IDS)
def test_ledger_waiver_reason_names_the_absent_asset(path: DeferredPath):
    reason = _ledger_json_entry(LEDGER_ITEM_ID)["reason"]
    assert reason.strip(), "Ledger item 1 is waived with an empty reason."
    assert path.asset.search(reason), (
        f"The waiver reason for ledger item 1 does not name {path.asset_name}. "
        f"A waiver that does not say what is missing is a waiver nobody can "
        f"re-open on evidence."
    )


def test_ledger_waiver_reason_names_phase_4():
    reason = _ledger_json_entry(LEDGER_ITEM_ID)["reason"]
    assert OWNING_PHASE_PATTERN.search(reason), (
        "The waiver reason for ledger item 1 does not name Phase 4 as the owning "
        "phase."
    )


def test_ledger_waiver_reason_matches_between_table_and_json():
    """The table truncates nothing on our side; both copies must start the same."""
    json_reason = _ledger_json_entry(LEDGER_ITEM_ID)["reason"]
    table_reason = _ledger_table_field(LEDGER_ITEM_ID, "reason")
    assert table_reason, "The ledger table row for item 1 has an empty reason cell."
    normalised_json = " ".join(json_reason.split())
    normalised_table = " ".join(table_reason.split())
    assert normalised_table in normalised_json or normalised_json in normalised_table, (
        "The ledger's markdown table and JSON block disagree about item 1's "
        "reason. They are two representations of one record; edit them only "
        "through `gsd-tools windows`."
    )
