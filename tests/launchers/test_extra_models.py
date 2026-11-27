"""Opt-in extra model files: the manifest, repair, and how the launchers pass it.

The design is one env var, ``VISOSWAP_EXTRA_MODELS``, read by ``manifest.default_entries``
so the startup check and ``repair`` see the same set. A new ``MODELS_SUBSET`` value was
deliberately avoided: ``bootstrap`` treats every subset except ``default`` as "all".

Every test here clears the variable itself; a leftover value would silently change what
"the default set" means for the next test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
from visoswap.models import bootstrap, manifest, models_data

L = Path(__file__).resolve().parents[2] / "tools" / "launchers"
sys.path.insert(0, str(L))

GFPGAN = "GFPGANv1.4"
LANDMARK = "FaceLandmark5"


@pytest.fixture(autouse=True)
def _clear_extra(monkeypatch):
    monkeypatch.delenv(manifest.EXTRA_MODELS_ENV, raising=False)


def _install(monkeypatch, scratch, spec):
    """A tiny manifest resolved under ``scratch``: (name, content, present)."""
    import hashlib

    records = []
    for name, content, present in spec:
        if present:
            (scratch / f"{name}.onnx").write_bytes(content)
        records.append(
            {
                "model_name": name,
                "local_path": f"./model_assets/{name}.onnx",
                "hash": hashlib.sha256(content).hexdigest(),
                "url": f"http://example.test/{name}.onnx",
            }
        )
    monkeypatch.setattr(models_data, "models_list", records)
    monkeypatch.setattr(models_data, "models_trt_list", [])


# ---------------------------------------------------------------------------
# manifest: the default set, and what an extra name may be


def test_unset_env_is_exactly_the_previous_default_set(monkeypatch):
    base_records = [{"model_name": n, "local_path": f"./model_assets/{n}.onnx", "hash": "x", "url": "u"}
                    for n in sorted(manifest.DEFAULT_SWAP_MODELS)]
    monkeypatch.setattr(models_data, "models_list", base_records)
    monkeypatch.setattr(models_data, "models_trt_list", [])

    assert {e.name for e in manifest.default_entries()} == set(manifest.DEFAULT_SWAP_MODELS)


def test_blank_env_adds_nothing_and_does_not_raise(monkeypatch):
    monkeypatch.setenv(manifest.EXTRA_MODELS_ENV, " , ,")
    assert manifest.extra_model_names() == []
    assert {e.name for e in manifest.default_entries()} == set(manifest.DEFAULT_SWAP_MODELS)


def test_extras_are_added_to_the_default_set(monkeypatch):
    base = {e.name for e in manifest.default_entries()}
    monkeypatch.setenv(manifest.EXTRA_MODELS_ENV, GFPGAN)
    after = {e.name for e in manifest.default_entries()}
    assert after == base | {GFPGAN}
    assert base < after


def test_unknown_name_raises_naming_it(monkeypatch):
    monkeypatch.setenv(manifest.EXTRA_MODELS_ENV, "NoSuchModel")
    with pytest.raises(ValueError) as caught:
        manifest.default_entries()
    assert "NoSuchModel" in str(caught.value)


def test_optional_entry_raises(monkeypatch, tmp_path):
    # no url -> not fetchable -> optional, so asking for it can only disappoint
    _install(monkeypatch, tmp_path, [("GFPGANv1.4", b"w", True)])
    monkeypatch.setattr(models_data, "models_list",
                        [{"model_name": "TensorRTCache", "local_path": "./model_assets/x.trt",
                          "hash": "h", "url": None}])
    monkeypatch.setattr(models_data, "models_trt_list", [])
    monkeypatch.setenv(manifest.EXTRA_MODELS_ENV, "TensorRTCache")
    with pytest.raises(ValueError) as caught:
        manifest.default_entries()
    assert "TensorRTCache" in str(caught.value)


def test_names_are_stripped_and_deduplicated(monkeypatch):
    monkeypatch.setenv(manifest.EXTRA_MODELS_ENV, " GFPGANv1.4 , GFPGANv1.4 ,,FaceLandmark5 ")
    assert manifest.extra_model_names() == [GFPGAN, LANDMARK]


def test_validate_extra_names_needs_no_torch():
    # the launchers call this before any remote action; it must not import torch
    assert manifest.validate_extra_names([]) == []
    assert manifest.validate_extra_names([GFPGAN]) == [GFPGAN]
    with pytest.raises(ValueError):
        manifest.validate_extra_names(["Nope"])


def test_subset_all_is_unaffected(monkeypatch):
    """default_entries is the only function the variable moves; ``all`` still means all."""
    monkeypatch.setenv(manifest.EXTRA_MODELS_ENV, GFPGAN)
    assert len(manifest.tracked()) >= len(manifest.default_entries())


# ---------------------------------------------------------------------------
# repair: the extra files are actually requested


def test_repair_asks_for_the_extra_file(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, [(GFPGAN, b"g", False)])
    monkeypatch.setenv(manifest.EXTRA_MODELS_ENV, GFPGAN)
    asked = []

    def recording(name, path, digest, url):
        asked.append(name)
        Path(path).write_bytes(b"g")
        return True

    result = bootstrap.repair(models_dir=tmp_path, mode=bootstrap.FULL, fetcher=recording)
    assert asked == [GFPGAN]
    assert result.ok


def test_repair_without_extras_does_not_ask_for_them(monkeypatch, tmp_path):
    """The same manifest, no variable: the extra is not part of the required set."""
    _install(monkeypatch, tmp_path, [(GFPGAN, b"g", False)])
    asked = []

    def recording(name, path, digest, url):
        asked.append(name)
        return True

    bootstrap.repair(models_dir=tmp_path, mode=bootstrap.FULL, fetcher=recording)
    assert GFPGAN not in asked


# ---------------------------------------------------------------------------
# the launchers


def test_merge_settings_keeps_replaces_and_clears():
    import vs_remote

    def merge(prev, extra):
        return vs_remote.merge_settings(dict(prev), "state file",
                                        {"subset": "default", "extra_models": extra, "concurrency": None, "profile": None})

    # None = keep what the VM runs with
    assert merge({"VISOSWAP_EXTRA_MODELS": "GFPGANv1.4"}, None)["VISOSWAP_EXTRA_MODELS"] == GFPGAN
    # a flag/config value replaces it
    assert merge({}, GFPGAN)["VISOSWAP_EXTRA_MODELS"] == GFPGAN
    # "none" clears it, and a cleared value must not survive in the saved state
    cleared = merge({"VISOSWAP_EXTRA_MODELS": "GFPGANv1.4"}, "none")
    assert "VISOSWAP_EXTRA_MODELS" not in cleared
    assert "none" not in json.dumps(cleared)


def test_merge_settings_key_is_tracked_for_previous_state():
    """The start script reads the running process env through KEYS; the var must be listed."""
    import vs_remote

    assert 'KEYS = [' in vs_remote.START
    keys_line = next(l for l in vs_remote.START.splitlines() if l.startswith("KEYS = ["))
    assert "VISOSWAP_EXTRA_MODELS" in keys_line


def test_colab_settings_overrides_flag_beats_config():
    import colab_launch as CL

    cfg = {"VISOSWAP_EXTRA_MODELS": "FaceLandmark5"}
    args = SimpleNamespace(concurrency=None, profile=None, extra_models=GFPGAN)
    assert CL.settings_overrides(args, cfg)["extra_models"] == GFPGAN

    args = SimpleNamespace(concurrency=None, profile=None, extra_models=None)
    assert CL.settings_overrides(args, cfg)["extra_models"] == LANDMARK

    args = SimpleNamespace(concurrency=None, profile=None, extra_models=None)
    assert CL.settings_overrides(args, {})["extra_models"] is None


def test_colab_models_step_is_told_about_the_extras():
    """The models step has its own env; without this nothing is downloaded."""
    import vs_remote

    rendered = vs_remote.render(vs_remote.MODELS, repo_dir="/r", token_file="/t", api_key_file="/k",
                                ref=None, repo_url="u", subset="default", restart=False, profile=None,
                                concurrency=None, extra_models=GFPGAN, wait=240, which=[], lines=10, poll=1)
    assert "VISOSWAP_EXTRA_MODELS" in rendered
    assert GFPGAN in rendered


def test_colab_models_step_never_sends_none(monkeypatch):
    import vs_remote

    rendered = vs_remote.render(vs_remote.MODELS, repo_dir="/r", token_file="/t", api_key_file="/k",
                                ref=None, repo_url="u", subset="default", restart=False, profile=None,
                                concurrency=None, extra_models="none", wait=240, which=[], lines=10, poll=1)
    # the assignment must sit inside the guard that rejects the word "none"
    assert '!= "none"' in rendered
    assert rendered.index("VISOSWAP_EXTRA_MODELS\"] = _extra") > rendered.index('!= "none"')


def test_flag_counts_as_a_knob():
    """A healthy backend must not report 'nothing to do' when only --extra-models is given."""
    source = (L / "colab_launch.py").read_text()
    line = next(l for l in source.splitlines() if "knobs = args.concurrency is not None" in l)
    assert "args.extra_models is not None" in line


class _Untouchable:
    """Any use means the launcher acted before it validated."""

    def __getattr__(self, name):
        raise AssertionError("remote action before validation: " + name)


@pytest.mark.parametrize("module, func", [("colab_launch", "launch"), ("colab_launch", "restart"), ("kaggle_launch", "launch")])
def test_config_typo_fails_before_any_remote_action(module, func):
    mod = __import__(module)
    args = mod.build_parser().parse_args([])
    with pytest.raises(mod.LaunchError) as caught:
        getattr(mod, func)(args, {"VISOSWAP_EXTRA_MODELS": "GFPGAN"}, _Untouchable(), _Untouchable(), _Untouchable())
    assert "GFPGAN" in str(caught.value)


@pytest.mark.parametrize("value", [GFPGAN, "none"])
def test_kaggle_flag_on_a_running_kernel_asks_for_restart(monkeypatch, value):
    import kaggle_launch as K

    monkeypatch.setattr(K, "provision", lambda *a, **k: ({"TUNNEL_MODE": "named"}, None))
    monkeypatch.setattr(K, "load_api_key", lambda *a: None)
    monkeypatch.setattr(K, "url_for", lambda cfg: "https://kernel.invalid")
    rep = SimpleNamespace(step=lambda *a: None, log=lambda *a: None)
    kg = SimpleNamespace(slug="u/k", state=lambda: "RUNNING", health=lambda url: True)
    args = K.build_parser().parse_args(["--extra-models", value])
    with pytest.raises(K.LaunchError) as caught:
        K.launch(args, {}, rep, rep, kg)
    assert "already running" in str(caught.value)
    assert "--extra-models" in str(caught.value)


def test_restart_runs_the_models_step_first():
    source = (L / "colab_launch.py").read_text()
    body = source.split("def restart(")[1].split("\ndef ")[0]
    assert "co.models(" in body
    assert body.index("co.models(") < body.index('co.step("start"')


def test_kaggle_injection_fills_a_copy_and_leaves_the_repo_notebook():
    import kaggle_launch as K

    repo_nb = REPO_ROOT / "visoswap_kaggle.ipynb"
    original = repo_nb.read_text()
    nb = json.loads(original)

    filled = K.inject_notebook(nb, "", "", None, False, None, None, GFPGAN)
    text = json.dumps(filled)
    assert f"EXTRA_MODELS = '{GFPGAN}'" in text
    # the repo notebook keeps '' and is not modified by injection
    assert "EXTRA_MODELS = ''" in original
    assert repo_nb.read_text() == original


def test_kaggle_injection_fails_loudly_when_the_line_is_missing():
    import kaggle_launch as K

    with pytest.raises(K.LaunchError):
        K.inject_notebook({"cells": [{"source": ["x = 1"]}]}, "", "", None, False, None, None, GFPGAN)


def test_kaggle_notebook_sets_the_env_before_repair():
    nb = json.loads((REPO_ROOT / "visoswap_kaggle.ipynb").read_text())
    model_cell = next("".join(c["source"]) for c in nb["cells"]
                      if "os.environ['MODELS_SUBSET'] = MODEL_SUBSET" in "".join(c["source"]))
    assert "EXTRA_MODELS = ''" in model_cell
    assert model_cell.index("os.environ['VISOSWAP_EXTRA_MODELS']") < model_cell.index("repair(models_dir")
