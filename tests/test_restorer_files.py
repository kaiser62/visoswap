"""A missing restorer file must be named before a run, not discovered mid-frame.

Two failure shapes this pins, both observed on this machine:

* alignment ``Reference`` with no ``res50.onnx`` printed an exception into the
  log and returned the face untouched, so a run "succeeded" while restoring
  nothing;
* ``Original`` / ``Blend`` with no ``GFPGANv1.4.onnx`` died inside ONNX Runtime
  with a raw path, which is no more actionable.

``models_data`` reads ``MODELS_DIR`` at import, so these tests inject a model
list whose paths point at a ``tmp_path`` rather than patching the environment.
No model is loaded, nothing is hashed, nothing is downloaded.
"""

from __future__ import annotations

import asyncio

import pytest

from visoswap.models import models_data
from visoswap.models.restorer_files import (
    missing_restorer_files,
    required_restorer_models,
)

GFPGAN = "GFPGANv1.4"
LANDMARK = "FaceLandmark5"


def _settings(**overrides):
    """Restorer slot 1 on, GFPGAN, aligned the way the field default does it."""
    settings = {
        "FaceRestorerEnableToggle": True,
        "FaceRestorerTypeSelection": "GFPGAN-v1.4",
        "FaceRestorerDetTypeSelection": "Reference",
        "FaceRestorerEnable2Toggle": False,
        "FaceRestorerType2Selection": "GFPGAN-v1.4",
        "FaceRestorerDetType2Selection": "Reference",
    }
    settings.update(overrides)
    return settings


@pytest.fixture
def model_list(tmp_path, monkeypatch):
    """A model list whose restorer paths live in ``tmp_path``; nothing on disk yet."""
    entries = [
        {"model_name": GFPGAN, "local_path": str(tmp_path / "GFPGANv1.4.onnx")},
        {"model_name": LANDMARK, "local_path": str(tmp_path / "res50.onnx")},
        {"model_name": "GPENBFR256", "local_path": str(tmp_path / "GPEN-BFR-256.onnx")},
        {"model_name": "Inswapper128", "local_path": str(tmp_path / "inswapper.onnx")},
    ]
    monkeypatch.setattr(models_data, "models_list", entries, raising=True)
    return entries


def _touch(entries, name):
    for entry in entries:
        if entry["model_name"] == name:
            with open(entry["local_path"], "wb") as handle:
                handle.write(b"not a real weight")
            return
    raise AssertionError(f"no entry named {name}")


def test_restorers_off_needs_nothing(model_list, tmp_path):
    settings = _settings(FaceRestorerEnableToggle=False, FaceRestorerEnable2Toggle=False)
    assert required_restorer_models(settings) == []
    assert missing_restorer_files(settings) == []


def test_original_without_gfpgan_names_it(model_list):
    _touch(model_list, LANDMARK)  # res50 present: proves the miss is GFPGAN
    settings = _settings(FaceRestorerDetTypeSelection="Original")
    missing = missing_restorer_files(settings)
    assert len(missing) == 1
    assert missing[0].endswith("GFPGANv1.4.onnx")


def test_reference_with_gfpgan_but_no_landmark_names_res50(model_list):
    _touch(model_list, GFPGAN)
    missing = missing_restorer_files(_settings(FaceRestorerDetTypeSelection="Reference"))
    assert len(missing) == 1
    assert missing[0].endswith("res50.onnx")


def test_original_does_not_need_res50(model_list):
    _touch(model_list, GFPGAN)
    # Only GFPGAN is on disk; Original must not ask for the landmark model.
    assert missing_restorer_files(_settings(FaceRestorerDetTypeSelection="Original")) == []
    assert required_restorer_models(_settings(FaceRestorerDetTypeSelection="Original")) == [GFPGAN]


def test_reference_with_both_present_is_clean(model_list):
    _touch(model_list, GFPGAN)
    _touch(model_list, LANDMARK)
    assert missing_restorer_files(_settings(FaceRestorerDetTypeSelection="Reference")) == []


def test_slot_two_is_checked(model_list):
    _touch(model_list, GFPGAN)
    _touch(model_list, LANDMARK)
    settings = _settings(
        FaceRestorerEnable2Toggle=True,
        FaceRestorerType2Selection="GPEN-256",
        FaceRestorerDetType2Selection="Original",
    )
    missing = missing_restorer_files(settings)
    assert len(missing) == 1
    assert missing[0].endswith("GPEN-BFR-256.onnx")


def test_slot_two_off_is_not_checked(model_list):
    _touch(model_list, GFPGAN)
    _touch(model_list, LANDMARK)
    settings = _settings(
        FaceRestorerEnable2Toggle=False,
        FaceRestorerType2Selection="GPEN-256",
        FaceRestorerDetType2Selection="Original",
    )
    assert missing_restorer_files(settings) == []


def test_every_selectable_type_maps_to_a_real_model():
    """The schema's restorer types and the model names must not drift apart."""
    import json
    from pathlib import Path

    from visoswap.models.restorer_files import TYPE_TO_MODEL

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "visoswap" / "schema" / "schema.json").read_text()
    )
    options = schema["widgets"]["FaceRestorerTypeSelection"]["options"]
    assert set(options) == set(TYPE_TO_MODEL), (
        "restorer types in schema.json and TYPE_TO_MODEL disagree: "
        f"{sorted(set(options) ^ set(TYPE_TO_MODEL))}"
    )
    real = {entry["model_name"] for entry in models_data.models_list}
    assert set(TYPE_TO_MODEL.values()) <= real


def test_validate_raises_naming_the_file(model_list, monkeypatch):
    from backend.services.engine_backend import EngineFrameGenerator

    _touch(model_list, LANDMARK)  # res50 there, GFPGAN missing
    generator = EngineFrameGenerator("CPU")
    generator._project = {"id": "p" * 32}
    monkeypatch.setattr(
        generator, "_resolve_project_settings", lambda: _settings(FaceRestorerDetTypeSelection="Original")
    )

    with pytest.raises(ValueError) as caught:
        asyncio.run(generator.validate())

    message = str(caught.value)
    assert "GFPGANv1.4.onnx" in message
    assert "restorer" in message.lower()


def test_validate_skips_without_a_project(model_list):
    """No project id means no settings tier, so the restorer check cannot apply."""
    from backend.services.engine_backend import EngineFrameGenerator

    generator = EngineFrameGenerator("CPU")
    generator._project = None

    # Nothing is on disk and the restorer defaults would demand two files, yet
    # validate must pass: with no project there is no project settings tier.
    assert generator._resolve_project_settings() == {}
    asyncio.run(generator.validate())
