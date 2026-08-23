"""The source-embedding memo's contract, driven through the sealed runner.

Phase 05.1 plan 01: ``Engine._source_embedding_store`` computes a source
image's store once per ``(realpath, st_mtime_ns, st_size)`` per bind instead of
once per frame. The probes run inside ``_engine_runner.py --source-cache`` --
the same sealed subprocess every engine test goes through -- and this module
asserts each probe separately so one failure names itself.

**Nothing here skips.** Missing media, a missing settings fixture and a missing
model link are all ``ASSET_MISSING`` from the runner and therefore a nonzero
exit here: a missing asset is a failure, not a skip, exactly as
``tests/test_engine_smoke.py`` states it -- a skipped identity check is a cache
that looks trustworthy and has proven nothing.
"""

from pathlib import Path

import pytest

from tests.conftest import run_engine_runner

TESTS_DIR = Path(__file__).resolve().parent


def _parse(detail: str) -> dict[str, str]:
    """``repeat=same rewrite=different ...`` -> a mapping."""
    fields = {}
    for word in detail.split():
        key, sep, value = word.partition("=")
        if sep:
            fields[key] = value
    return fields


@pytest.fixture(scope="session")
def source_cache_run(engine_python):
    """Run the probe mode once and share the result.

    Session-scoped because the run costs a model load, and every assertion
    below is about that one run rather than about a fresh one.
    """
    code, output = run_engine_runner(engine_python, ["--source-cache"])
    label, _, rest = output.partition(":")
    mode, _, detail = rest.partition(":")
    return {
        "code": code,
        "output": output,
        "label": label,
        "mode": mode,
        "detail": detail,
        "fields": _parse(detail),
    }


def test_the_probe_run_exits_clean(source_cache_run):
    assert source_cache_run["code"] == 0, (
        "the sealed source-cache run did not exit CLEAN:\n  {}\n"
        "ASSET_MISSING (3) means a fixture or media file is not on disk -- see "
        "docs/engine-test-assets.md. ENGINE_ERROR (4) means a probe raised.".format(
            source_cache_run["output"]
        )
    )
    assert source_cache_run["label"] == "CLEAN", source_cache_run["output"]
    assert source_cache_run["mode"] == "source-cache", source_cache_run["output"]


def test_repeat_calls_return_the_identical_object(source_cache_run):
    """One unmodified file is embedded once per bind, not once per call."""
    assert source_cache_run["fields"].get("repeat") == "same", (
        "two consecutive calls for one untouched source returned different "
        "objects -- the memo is not serving hits, so every frame still pays "
        "the full four-call detection sequence:\n  {}".format(
            source_cache_run["output"]
        )
    )


def test_an_in_place_rewrite_recomputes_the_store(source_cache_run):
    """The stat rides in the key: same path, new bytes -> new store."""
    assert source_cache_run["fields"].get("rewrite") == "different", (
        "a rewritten file at the same path was served from the memo -- the key "
        "is missing its mtime/size components, so an in-place face replacement "
        "would keep the old identity forever:\n  {}".format(
            source_cache_run["output"]
        )
    )


def test_a_rebind_recomputes_the_store(source_cache_run):
    """The memo must not survive Engine.load()."""
    assert source_cache_run["fields"].get("rebind") == "different", (
        "a store computed before Engine.load() survived the rebind -- an "
        "embedding resolved under one project's parameter tier would leak into "
        "the next bind:\n  {}".format(source_cache_run["output"])
    )


def test_the_memo_reports_a_visible_entry_count(source_cache_run):
    entries = source_cache_run["fields"].get("entries")
    assert entries is not None and int(entries) >= 1, (
        "the runner reported no usable entries count ({}); the memo's growth "
        "(threat T-05.1-01-04) must stay visible to the report line".format(entries)
    )
