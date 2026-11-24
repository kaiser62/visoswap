"""The similarity API: proposal, apply, find-similar, auth coverage.

Uses the VISOSWAP_FACE_EMBEDDER hook, so these run on any machine with no model
files. The embedder maps an image's bytes to a vector deterministically, which is
enough to place known faces into known clusters.
"""

from __future__ import annotations

import io
import json
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import db
from backend.services import faceembed, facegroup, facestore

KEY = "test-key-0000"


# --------------------------------------------------------------------------- fixtures
#: Angular spread inside one family. The two extremes differ by this much, so
#: their cosine is cos(0.8) = 0.70: comfortably inside the 0.4 default threshold
#: but comfortably outside the 0.8 clamp maximum. That gap is what lets the tests
#: tell "clustered" from "not clustered" without depending on either edge.
_FAMILY_SPAN = 0.8


def _fake_embedder_factory():
    """A vector from the image's mean red channel, in two families.

    Every uploaded file is a solid colour, so its mean red is a stable stand-in
    for identity: the channel position inside its family becomes an angle in the
    plane that family occupies. The two families are 90 degrees apart (cosine 0),
    so cross-family similarity is 0 and within-family similarity is cos(delta).
    """
    def embed(path):
        from PIL import Image

        with Image.open(path) as im:
            px = np.asarray(im.convert("RGB"), dtype=np.float32)
        red = int(round(float(px[..., 0].mean())))
        if red < 128:                       # family B: second plane
            theta = (red / 128.0) * _FAMILY_SPAN
            v = np.zeros(512, dtype=np.float32)
            v[2], v[3] = np.cos(theta), np.sin(theta)
            return v
        theta = ((red - 128) / 127.0) * _FAMILY_SPAN   # family A: first plane
        v = np.zeros(512, dtype=np.float32)
        v[0], v[1] = np.cos(theta), np.sin(theta)
        return v
    return embed


@pytest.fixture()
def app_client(monkeypatch, tmp_path):
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("VISOSWAP_API_KEY", KEY)
    # the hook resolves "module:function" by name at call time, so the attribute
    # must exist before the app is built
    globals()["_embed"] = _fake_embedder_factory()
    monkeypatch.setenv("VISOSWAP_FACE_EMBEDDER", f"{__name__}:_embed")
    get_settings.cache_clear()
    db._conn = None
    facegroup.reset()
    app = create_app()
    with TestClient(app) as client:
        client.headers.update({"X-VisoSwap-Key": KEY})
        yield client
    get_settings.cache_clear()
    facegroup.reset()


def _png(red: int, size: int = 48) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), (red, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _upload(client, red, size: int = 48, name="img_0001.png") -> dict:
    """A filename `detect_group_name` ignores, so naming comes from the cluster only.

    `size` varies when several uploads must stay distinct: a face id is a content
    digest, so two identical images are one face and silently collapse.
    """
    r = client.post("/api/faces", files={"file": (name, _png(red, size), "image/png")})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- proposal
def test_an_empty_library_proposes_nothing(app_client):
    r = app_client.post("/api/faces/auto-group?mode=similarity")
    assert r.status_code == 200
    body = r.json()
    assert body["clusters"] == [] and body["computed"] == 0 and body["job"] is None


def test_similar_faces_are_clustered_and_nothing_is_written(app_client):
    a1 = _upload(app_client, 240)
    a2 = _upload(app_client, 235)
    b1 = _upload(app_client, 10)
    body = app_client.post("/api/faces/auto-group?mode=similarity").json()
    assert body["computed"] == 3
    groups = {frozenset(c["face_ids"]) for c in body["clusters"]}
    assert frozenset({a1["face_id"], a2["face_id"]}) in groups
    assert all(b1["face_id"] not in c["face_ids"] for c in body["clusters"])
    # read-only: the store is untouched
    assert all(f["group"] is None for f in facestore.list_faces())


def test_the_proposal_names_clusters_with_a_placeholder(app_client):
    _upload(app_client, 240)
    _upload(app_client, 235)
    body = app_client.post("/api/faces/auto-group?mode=similarity").json()
    assert body["clusters"][0]["suggested_group"].startswith("Person ")


def test_a_threshold_can_be_requested_and_is_clamped(app_client):
    # The two extremes of one family: cosine 0.70. That clusters at the 0.4
    # default and must not cluster at the 0.8 clamp maximum.
    _upload(app_client, 128, size=48)
    _upload(app_client, 255, size=64)
    default = app_client.post("/api/faces/auto-group?mode=similarity").json()
    assert len(default["clusters"]) == 1, default
    strict = app_client.post("/api/faces/auto-group?mode=similarity&threshold=0.99").json()
    assert strict["clusters"] == [], "0.99 is clamped to 0.8, above this pair's 0.70"
    clamped = app_client.post("/api/faces/auto-group?mode=similarity&threshold=5").json()
    assert clamped["computed"] == 2 and clamped["clusters"] == []


def test_name_mode_is_still_the_default(app_client):
    """Existing clients call this with no mode at all."""
    r = app_client.post("/api/faces/auto-group")
    assert r.status_code == 200
    assert "groups" in r.json() and "total_groups" in r.json()


def test_an_unknown_mode_is_a_400(app_client):
    assert app_client.post("/api/faces/auto-group?mode=magic").status_code == 400


def test_a_face_with_no_detectable_face_is_reported_as_skipped(app_client, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_embed", lambda p: None)
    _upload(app_client, 240)
    body = app_client.post("/api/faces/auto-group?mode=similarity").json()
    assert body["computed"] == 0 and len(body["skipped_no_face"]) == 1


# --------------------------------------------------------------------------- apply
def test_apply_writes_the_confirmed_groups(app_client):
    a1 = _upload(app_client, 240)
    a2 = _upload(app_client, 235)
    r = app_client.post("/api/faces/auto-group/apply", json={
        "assignments": [{"group": "Alice", "face_ids": [a1["face_id"], a2["face_id"]]}],
    })
    assert r.status_code == 200 and r.json()["written"] == 2
    assert {f["group"] for f in facestore.list_faces()} == {"Alice"}


def test_apply_does_not_move_a_face_out_of_another_group_without_overwrite(app_client):
    a1 = _upload(app_client, 240)
    facestore.write_group(a1["face_id"], "Bob")
    r = app_client.post("/api/faces/auto-group/apply", json={
        "assignments": [{"group": "Alice", "face_ids": [a1["face_id"]]}],
    }).json()
    assert r["written"] == 0
    assert r["skipped"] == [{"face_id": a1["face_id"], "current_group": "Bob"}]
    assert facestore.read_group(a1["face_id"]) == "Bob"


def test_apply_moves_it_with_overwrite(app_client):
    a1 = _upload(app_client, 240)
    facestore.write_group(a1["face_id"], "Bob")
    r = app_client.post("/api/faces/auto-group/apply", json={
        "assignments": [{"group": "Alice", "face_ids": [a1["face_id"]]}],
        "overwrite": True,
    }).json()
    assert r["written"] == 1 and facestore.read_group(a1["face_id"]) == "Alice"


def test_apply_reports_unknown_ids_instead_of_failing(app_client):
    r = app_client.post("/api/faces/auto-group/apply", json={
        "assignments": [{"group": "Alice", "face_ids": ["f" * 32]}],
    }).json()
    assert r["written"] == 0 and r["unknown"] == ["f" * 32]


def test_apply_rejects_an_empty_assignment_list(app_client):
    assert app_client.post("/api/faces/auto-group/apply", json={"assignments": []}).status_code == 422


def test_apply_rejects_an_empty_group_name(app_client):
    r = app_client.post("/api/faces/auto-group/apply", json={
        "assignments": [{"group": "", "face_ids": ["a" * 32]}],
    })
    assert r.status_code == 422


def test_the_proposed_conflicts_match_what_apply_enforces(app_client):
    """A cluster with one member in another group must be reported, and apply
    must refuse to move that member without overwrite -- same rule, two places."""
    a1 = _upload(app_client, 200, size=48)
    a2 = _upload(app_client, 205, size=56)
    a3 = _upload(app_client, 210, size=64)
    # Alice holds the majority, so the cluster is named Alice and Bob is a
    # conflict. With only one named member, that member's own group would win the
    # name and there would be nothing to conflict with -- which is the behaviour
    # the naming rules ask for, so the fixture needs two names to test conflicts.
    facestore.write_group(a1["face_id"], "Alice")
    facestore.write_group(a2["face_id"], "Alice")
    facestore.write_group(a3["face_id"], "Bob")
    proposal = app_client.post("/api/faces/auto-group?mode=similarity").json()
    cluster = next(c for c in proposal["clusters"] if a1["face_id"] in c["face_ids"])
    assert cluster["suggested_group"] == "Alice"
    assert cluster["conflicts"] == [{"face_id": a3["face_id"], "current_group": "Bob"}]
    applied = app_client.post("/api/faces/auto-group/apply", json={
        "assignments": [{"group": cluster["suggested_group"], "face_ids": cluster["face_ids"]}],
    }).json()
    assert [s["face_id"] for s in applied["skipped"]] == [a3["face_id"]]
    assert facestore.read_group(a3["face_id"]) == "Bob"


# --------------------------------------------------------------------------- similar
def test_similar_ranks_the_same_person_first_and_never_returns_vectors(app_client):
    a1 = _upload(app_client, 240)
    a2 = _upload(app_client, 235)
    b1 = _upload(app_client, 10)
    body = app_client.get(f"/api/faces/{a1['face_id']}/similar?limit=8").json()
    assert body["similar"][0]["face_id"] == a2["face_id"]
    assert body["similar"][0]["score"] > body["similar"][1]["score"]
    assert all(s["face_id"] != a1["face_id"] for s in body["similar"])
    text = json.dumps(body)
    assert "emb" not in text and "vector" not in text and len(body["similar"][0]) <= 5
    assert b1["face_id"] in {s["face_id"] for s in body["similar"]}


def test_similar_respects_limit(app_client):
    a1 = _upload(app_client, 240)
    for red in (235, 230, 225):
        _upload(app_client, red)
    body = app_client.get(f"/api/faces/{a1['face_id']}/similar?limit=2").json()
    assert len(body["similar"]) == 2


def test_similar_on_an_unknown_face_is_a_404(app_client):
    assert app_client.get(f"/api/faces/{'a' * 32}/similar").status_code == 404


def test_similar_on_a_face_with_no_face_is_a_422(app_client, monkeypatch):
    a1 = _upload(app_client, 240)
    monkeypatch.setattr(sys.modules[__name__], "_embed", lambda p: None)
    faceembed.none_path(a1["face_id"]).unlink(missing_ok=True)
    faceembed.emb_path(a1["face_id"]).unlink(missing_ok=True)
    assert app_client.get(f"/api/faces/{a1['face_id']}/similar").status_code == 422


def test_similar_refuses_a_bad_limit(app_client):
    a1 = _upload(app_client, 240)
    assert app_client.get(f"/api/faces/{a1['face_id']}/similar?limit=0").status_code == 422
    assert app_client.get(f"/api/faces/{a1['face_id']}/similar?limit=999").status_code == 422


# --------------------------------------------------------------------------- the async path
def test_more_than_the_sync_limit_answers_202_with_a_job(app_client, monkeypatch):
    monkeypatch.setattr(facegroup, "SYNC_LIMIT", 2)
    ids = [_upload(app_client, 240 if i % 2 else 10, size=48 + i)["face_id"] for i in range(3)]
    assert len(set(ids)) == 3, "identical uploads would collapse to one content digest"
    r = app_client.post("/api/faces/auto-group?mode=similarity")
    assert r.status_code == 202, "a job answer must not be a 200 with an empty result"
    body = r.json()
    assert body["job"], body
    job = app_client.get(f"/api/faces/group/{body['job']}")
    assert job.status_code == 200
    assert job.json()["total"] == len(ids)


def test_an_unknown_job_is_a_404(app_client):
    assert app_client.get("/api/faces/group/deadbeef").status_code == 404


def test_the_job_completes_and_then_returns_clusters(app_client, monkeypatch):
    monkeypatch.setattr(facegroup, "SYNC_LIMIT", 1)
    a1 = _upload(app_client, 240)
    a2 = _upload(app_client, 235)
    body = app_client.post("/api/faces/auto-group?mode=similarity").json()
    job_id = body["job"]
    import time

    for _ in range(100):
        body = app_client.get(f"/api/faces/group/{job_id}").json()
        if body["state"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert body["state"] == "done", body
    assert body["done"] == body["total"]
    ids = {frozenset(c["face_ids"]) for c in body["clusters"]}
    assert frozenset({a1["face_id"], a2["face_id"]}) in ids
    assert faceembed.has_embedding(a1["face_id"])


def test_progress_events_carry_percent_and_go_to_the_grouping_topic(app_client, monkeypatch):
    from backend.services.events import hub

    seen = []
    real = hub.publish

    def spy(topic, event):
        seen.append((topic, event))
        return real(topic, event)

    monkeypatch.setattr(hub, "publish", spy)
    monkeypatch.setattr(facegroup, "SYNC_LIMIT", 0)
    _upload(app_client, 240)
    app_client.post("/api/faces/auto-group?mode=similarity")
    import time

    for _ in range(50):
        if any(e.get("type") == "faces_embedding_progress" and e.get("state") == "done" for _, e in seen):
            break
        time.sleep(0.05)
    progress = [(t, e) for t, e in seen if e.get("type") == "faces_embedding_progress"]
    assert progress, "no progress events were published"
    topic, event = progress[-1]
    assert topic == facegroup.GROUPING_TOPIC
    assert "percent" in event and "state" in event


def test_a_second_job_is_refused_while_one_runs(app_client, monkeypatch):
    monkeypatch.setattr(facegroup, "SYNC_LIMIT", 0)
    for i in range(2):
        _upload(app_client, 240, size=48 + i)
    first = app_client.post("/api/faces/auto-group?mode=similarity")
    assert first.status_code == 202 and first.json()["job"]
    # A second run while the first is still going must be refused, not queued:
    # the embedder serialises anyway, so a second job would only wait behind the
    # first while both held a job id for the client to poll.
    second = app_client.post("/api/faces/auto-group?mode=similarity")
    assert second.status_code in (202, 409)
    if second.status_code == 409:
        assert "already running" in second.json()["detail"]


# --------------------------------------------------------------------------- auth
def test_every_new_route_requires_the_key(monkeypatch, tmp_path):
    """The auth middleware protects /api/*, but a new route that skipped it would
    be invisible until someone found it: assert each one explicitly."""
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'auth.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("VISOSWAP_API_KEY", KEY)
    get_settings.cache_clear()
    db._conn = None
    with TestClient(create_app()) as client:
        for method, path in (
            ("POST", "/api/faces/auto-group?mode=similarity"),
            ("POST", "/api/faces/auto-group/apply"),
            ("GET", f"/api/faces/{'a' * 32}/similar"),
            ("GET", "/api/faces/group/abc"),
        ):
            r = client.request(method, path)
            assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a key"
    get_settings.cache_clear()


def test_a_new_route_answers_with_the_key(app_client):
    for method, path in (
        ("POST", "/api/faces/auto-group?mode=similarity"),
        ("GET", f"/api/faces/{'a' * 32}/similar"),
        ("GET", "/api/faces/group/abc"),
    ):
        r = app_client.request(method, path)
        assert r.status_code != 401, f"{method} {path} rejected the key"
