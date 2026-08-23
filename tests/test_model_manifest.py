"""The manifest view over the vendored model lists.

Runs on the plain developer interpreter, where ``import tensorrt`` fails, so the
with-TensorRT case is exercised by injecting a stand-in module — and the test
asserts the stand-in actually took effect, because a test that silently exercised
the 56-entry path twice would pass while proving half of what it claims.

Two things this file guards (per plan 04-03): no literal model count (56, 6, 62,
12) appears as an expectation here or in ``manifest.py``, and ``required`` is
derived from a fetchable-and-consumable rule whose currently-optional output must
equal exactly the entries lacking a URL.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from visoswap.models import manifest, models_data
from visoswap.schema import DEFAULT_MODELS_DIR, resolve_models_dir


def test_tracked_set_size_matches_the_vendored_lists():
    """``tracked`` reads the lists at call time; size equals their sum.

    Asserted as a relationship, not an absolute number, so the roadmap's
    "derived from the manifest at runtime, never a hardcoded count" is a
    property of the code rather than a promise.
    """
    entries = manifest.tracked()
    assert len(entries) == len(models_data.models_list) + len(models_data.models_trt_list)
    assert len(entries) > 0


def test_every_entry_exposes_name_path_digest_and_fetchability():
    directory = resolve_models_dir()
    for entry in manifest.tracked():
        assert isinstance(entry.name, str) and entry.name
        # Absolute, inside the models directory.
        assert entry.path.is_absolute()
        assert entry.path.is_relative_to(directory)
        # A valid sha256 hex digest.
        assert len(entry.digest) == 64
        int(entry.digest, 16)
        # fetchable mirrors the presence of a URL.
        assert entry.fetchable == (entry.url is not None and bool(entry.url))


def test_required_derived_from_rule_optional_equals_no_url():
    """The optional class is exactly the entries lacking a URL.

    The rule is ``fetchable and consumable``. The optional/required split is
    keyed by *path*, not name -- on an interpreter with TensorRT, the trt
    ``LivePortraitMotionExtractor`` (optional) and the onnx one (required) share
    a ``model_name`` but are different entries. This is what pins the rule rather
    than a list of six names: the day a seventh unfetchable entry appears
    upstream it is classified by the rule, not missed.
    """
    optional = manifest.optional_entries()
    required = manifest.required_entries()
    opt_paths = {e.path for e in optional}
    req_paths = {e.path for e in required}
    # Disjoint, exhaustive.
    assert opt_paths.isdisjoint(req_paths)
    assert len(opt_paths) + len(req_paths) == len({e.path for e in manifest.tracked()})

    unfetchable = [e for e in manifest.tracked() if not e.fetchable]
    assert {e.path for e in optional} == {e.path for e in unfetchable}
    # Every required entry is fetchable (and therefore consumable).
    assert all(e.fetchable for e in required)
    # The optional entries are exactly the ones no URL exists for.
    assert all(e.url is None for e in optional)


def test_resolution_never_escapes_the_models_directory(monkeypatch):
    """A manifest entry with traversal cannot make us stat a file elsewhere."""
    from visoswap.models import manifest as m

    # Plant a malicious local_path in the vendored list.
    evil = {
        "model_name": "evil",
        "local_path": "./model_assets/../../outside.onnx",
        "hash": "0" * 64,
        "url": "http://example.test/evil.onnx",
    }
    monkeypatch.setattr(models_data, "models_list", list(models_data.models_list) + [evil])

    with pytest.raises(ValueError, match="outside the models directory"):
        m.tracked()


def test_models_dir_resolution_argument_wins(monkeypatch, tmp_path):
    """Explicit argument beats MODELS_DIR beats the repository default."""
    from visoswap.models import manifest as m

    arg_dir = tmp_path / "arg"
    arg_dir.mkdir()
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "env"))

    for entry in m.tracked(models_dir=arg_dir):
        assert entry.path.is_relative_to(arg_dir.resolve())


def test_models_dir_resolution_env_used_when_no_argument(monkeypatch, tmp_path):
    from visoswap.models import manifest as m

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    monkeypatch.setenv("MODELS_DIR", str(env_dir))

    for entry in m.tracked():
        assert entry.path.is_relative_to(env_dir.resolve())


def test_models_dir_resolution_default_when_neither(monkeypatch):
    from visoswap.models import manifest as m

    monkeypatch.delenv("MODELS_DIR", raising=False)
    for entry in m.tracked():
        assert entry.path.is_relative_to(DEFAULT_MODELS_DIR.resolve())


def test_tensorrt_stand_in_actually_took_effect(monkeypatch):
    """Inject a fake ``tensorrt`` and prove the manifest reads it at call time.

    This runs on both interpreters: plain python (TensorRT absent) and
    ``.venv-clean`` (TensorRT present). On either, replacing the ``tensorrt``
    module with a stand-in and reloading ``models_data`` must change
    ``models_trt_list`` -- proving the with-TensorRT branch is genuinely
    exercised rather than silently exercising the empty branch twice.
    """
    before = len(models_data.models_trt_list)

    class FakeTensorRT:
        __version__ = "10.6.0"

    monkeypatch.setitem(sys.modules, "tensorrt", FakeTensorRT)
    importlib.reload(models_data)

    try:
        trt_count = len(models_data.models_trt_list)
        assert trt_count > 0, "the tensorrt stand-in did not take effect"
        # tracked() reads the lists at call time, so it reflects the reload.
        assert len(manifest.tracked()) == len(models_data.models_list) + trt_count
    finally:
        monkeypatch.undo()
        importlib.reload(models_data)
        # The real environment is restored.
        assert len(models_data.models_trt_list) == before


def test_no_literal_model_count_in_manifest_module():
    """AST check: manifest.py hardcodes no model count."""
    import ast

    tree = ast.parse(Path(manifest.__file__).read_text(encoding="utf-8"))
    bad = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value in (56, 6, 62, 12)
    ]
    assert not bad, "manifest.py hardcodes a model count: {!r}".format(bad)
