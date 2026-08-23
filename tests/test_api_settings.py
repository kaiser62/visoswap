"""Contract tests for the settings read API (plan 05-01).

Covers GET /api/schema, GET /api/projects/{id}/settings and GET /api/presets
against a temp SQLite database through FastAPI TestClient, plus the read-API
hardening: dynamic DFM options, value typing across all 201 keys, malformed-id
handling, preset payload sanity, and the path-aware readiness helper.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import db

#: The five-value type vocabulary from the schema.
TYPES = {"toggle", "int", "float", "selection", "text"}

#: A project-tier toggle and int, chosen by type (never by key-name substrings in
#: the frontend; here the type is read from the schema entry).
TOGGLE_KEY = "AutoColorEnableToggle"
INT_KEY = "AutoColorBlendAmountSlider"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """A TestClient against the real app with an isolated temp database.

    Repoints DATABASE_URL to a temp file, forces the Database singleton to
    reconnect, and sets MODELS_VERIFY_MODE=fast so the Phase 4 model bootstrap
    gate runs a fast presence check (it still requires a complete model set).
    """
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///{}".format(tmp_path / "app.db"))
    get_settings.cache_clear()
    db._conn = None
    app = create_app()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _create_project(client) -> str:
    resp = client.post("/api/projects", json={"name": "test project"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _schema_widgets(client) -> dict:
    resp = client.get("/api/schema")
    assert resp.status_code == 200, resp.text
    return resp.json()["widgets"]


# ---------------------------------------------------------------------------
# GET /api/schema
# ---------------------------------------------------------------------------


def test_schema_returns_201_typed_entries(client):
    widgets = _schema_widgets(client)
    assert len(widgets) == 201
    for entry in widgets.values():
        assert entry["type"] in TYPES, "entry type outside vocabulary: {}".format(entry["type"])
    # The schema header is present.
    resp = client.get("/api/schema")
    assert resp.json()["schema"]


# ---------------------------------------------------------------------------
# GET /api/projects/{id}/settings
# ---------------------------------------------------------------------------


def test_project_settings_resolve_201_typed_values(client):
    project_id = _create_project(client)
    resp = client.get("/api/projects/{}/settings".format(project_id))
    assert resp.status_code == 200, resp.text
    values = resp.json()["values"]
    assert len(values) == 201
    # A known toggle resolves to a Python bool, a known int to an int.
    assert isinstance(values[TOGGLE_KEY], bool)
    assert isinstance(values[INT_KEY], int)


def test_project_settings_unknown_project_returns_404(client):
    resp = client.get("/api/projects/{}/settings".format("a" * 32))
    assert resp.status_code == 404, resp.text


def test_project_settings_malformed_id_returns_400(client):
    # A 32-char id that passes the path length check but is not uuid4 hex —
    # rejected by validate_project_id, not FastAPI's length validation.
    resp = client.get("/api/projects/{}/settings".format("z" * 32))
    assert resp.status_code == 400, resp.text


def test_every_settings_value_type_matches_its_entry(client):
    project_id = _create_project(client)
    widgets = _schema_widgets(client)
    values = client.get("/api/projects/{}/settings".format(project_id)).json()["values"]
    for key, value in values.items():
        entry_type = widgets[key]["type"]
        if entry_type == "toggle":
            assert isinstance(value, bool), "{} should be bool, got {!r}".format(key, value)
        elif entry_type == "int":
            assert isinstance(value, int) and not isinstance(value, bool), key
        elif entry_type == "float":
            assert isinstance(value, (int, float)) and not isinstance(value, bool), key
        elif entry_type in ("selection", "text"):
            assert isinstance(value, str), "{} should be str, got {!r}".format(key, value)


def test_schema_carries_dfm_options_key(client):
    widgets = _schema_widgets(client)
    dfm = widgets["DFMModelSelection"]
    assert "options" in dfm, "DFMModelSelection must always carry an options key"
    assert isinstance(dfm["options"], list)


# ---------------------------------------------------------------------------
# GET /api/presets
# ---------------------------------------------------------------------------


def test_presets_lists_the_two_migrated_presets(client):
    resp = client.get("/api/presets")
    assert resp.status_code == 200, resp.text
    presets = resp.json()["presets"]
    assert [p["name"] for p in presets] == ["A", "with AUD"]
    assert len(presets) == 2


def test_preset_payload_keys_are_schema_keys(client):
    widgets = _schema_widgets(client)
    presets = client.get("/api/presets").json()["presets"]
    for preset in presets:
        for tier in ("project", "global"):
            for key in preset.get(tier, {}):
                assert key in widgets, "preset carries a non-schema key: {}".format(key)


# ---------------------------------------------------------------------------
# readiness helper re-initializes on a different db path
# ---------------------------------------------------------------------------


def test_readiness_helper_is_path_aware(client, tmp_path, monkeypatch):
    # A second, different database path in the same test run still answers 200.
    project_id = _create_project(client)
    assert client.get("/api/projects/{}/settings".format(project_id)).status_code == 200

    monkeypatch.setenv("DATABASE_URL", "sqlite:///{}".format(tmp_path / "second.db"))
    get_settings.cache_clear()
    db._conn = None
    with TestClient(create_app()) as c2:
        pid2 = _create_project(c2)
        assert c2.get("/api/projects/{}/settings".format(pid2)).status_code == 200
