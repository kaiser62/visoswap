"""The face tier keyed by embedding: does it survive a reload, and does it agree?

Two claims live here and they fail in opposite directions, so both are needed.

**Survival.** A face detected again after a reload has an embedding that is close
but not identical to the one that was stored -- a different frame, a crop a pixel
over. It must resolve to the settings stored for that face. A matcher that always
says "same face" passes that perfectly and is catastrophic, so the negative
direction is asserted with the same weight: an embedding far enough away must
resolve to a *different* identity and must not see the first one's overrides.

**Agreement.** ``visoswap/settings/faces.py`` transcribes two things from the
engine: the similarity formula and the face-key digest. Both transcriptions can
drift, and both drift quietly -- a similarity on the wrong scale is still a
plausible number, a digest over the wrong bytes is still a plausible hex string.
The agreement tests run the engine's own code, in a subprocess on the engine
interpreter, over the *same* vectors this side scores, and compare.

**Nothing here skips.** A missing engine interpreter is a failure, not a skip.
Phase 1 established that a missing dependency must never read as a pass, and this
is the sharpest case of that rule in the project: a skipped agreement check on the
one thing that could silently attach one person's settings to another person reads
green while proving nothing at all.
"""

import json
import math
import random
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.conftest import ENGINE_PYTHON_ENV_VAR, resolve_engine_python
from visoswap.settings import db, faces, store

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "tests" / "_similarity_runner.py"

PROJECT = "project-a"
MODEL = "Inswapper128ArcFace"
OTHER_MODEL = "SimSwap512ArcFace"

#: 512 is the real dimension of every recognition embedding the engine produces.
DIMENSION = 512

#: The scores here are float32 arithmetic on one side and float64 on the other,
#: over vectors narrowed to float32 on both. Measured worst case across the pairs
#: below is 6e-6 on a 0-to-100 scale. 1e-4 is comfortably above that and still
#: one part in a million of the scale -- far tighter than the integer threshold
#: the score is ultimately compared against.
TOLERANCE = 1e-4


def _vectors(seed, dimension=DIMENSION):
    generator = random.Random(seed)
    return [generator.uniform(-1.0, 1.0) for _ in range(dimension)]


def _nudged(vector, seed, scale):
    """``vector`` plus bounded noise. The reload case, and the stranger case."""
    generator = random.Random(seed)
    return [value + generator.uniform(-scale, scale) for value in vector]


#: One person, seen once.
BASE = _vectors(20260822)

#: The same person, detected again after a reload: close, not identical.
NEAR = _nudged(BASE, 1, 0.02)

#: Somebody else.
FAR = _vectors(19700101)


@pytest.fixture()
def connection(tmp_path):
    """A real on-disk SQLite database with the settings DDL applied.

    On disk rather than ``:memory:`` for the reason
    ``tests/test_settings_resolution.py`` gives: the blob column and its
    round-trip are exactly the sort of thing that can differ between a file-backed
    database and an in-memory one, and this file stores blobs.
    """
    conn = sqlite3.connect(tmp_path / "project.db")
    db.apply_settings_schema(conn)
    yield conn
    conn.close()


# --------------------------------------------------------------------------
# the premise
# --------------------------------------------------------------------------


def test_the_threshold_is_the_swap_pipelines_own_key(connection):
    """Not a new constant. The coupling is the design, so pin it."""
    from visoswap import schema

    entry = schema.entry(faces.SIMILARITY_THRESHOLD_KEY)
    assert entry["tier"] == "project", entry
    assert entry["type"] == "int", entry
    assert entry["default"] == 60, entry

    assert faces.threshold_for(connection, PROJECT) == 60

    store.set_project(connection, PROJECT, faces.SIMILARITY_THRESHOLD_KEY, 90)
    assert faces.threshold_for(connection, PROJECT) == 90, (
        "loosening or tightening the swap pipeline's similarity threshold must "
        "move the settings-identity threshold with it -- one notion of 'same "
        "face' in the application, deliberately, not two that can disagree"
    )


def test_the_metric_is_a_similarity_on_a_hundred_point_scale():
    """The single most expensive misreading available in this project.

    ``findCosineDistance`` is named for a distance and returns a similarity. A
    reader who assumes 0-to-1 reads the default threshold of 60 as very tight;
    it is in fact ``cos >= 0.2``, which is loose.
    """
    unit = [1.0, 0.0, 0.0]
    assert faces.cosine_similarity(unit, unit) == pytest.approx(faces.IDENTICAL)
    assert faces.cosine_similarity(unit, [0.0, 1.0, 0.0]) == pytest.approx(
        faces.ORTHOGONAL
    )
    assert faces.cosine_similarity(unit, [-1.0, 0.0, 0.0]) == pytest.approx(
        faces.OPPOSITE
    )
    assert faces.IDENTICAL == 100.0 and faces.ORTHOGONAL == 50.0


def test_the_metric_ignores_magnitude():
    """A vector and a multiple of it are the same direction, hence the same face."""
    scaled = [value * 7.25 for value in BASE]
    assert faces.cosine_similarity(BASE, scaled) == pytest.approx(
        faces.IDENTICAL, abs=TOLERANCE
    )


def test_the_fixtures_bracket_the_default_threshold():
    """Guard the premise of every survival test below.

    If ``NEAR`` ever stopped clearing the threshold or ``FAR`` ever started
    clearing it, the reload tests would still pass or still fail for reasons
    having nothing to do with the code under test.
    """
    near_score = faces.cosine_similarity(BASE, NEAR)
    far_score = faces.cosine_similarity(BASE, FAR)
    assert near_score >= 60, near_score
    assert near_score < 100.0, (
        "NEAR must not be an exact duplicate of BASE, or the test proves only "
        "that identical vectors match: {}".format(near_score)
    )
    assert far_score < 60, far_score


def test_an_empty_embedding_is_refused_rather_than_stored():
    """An empty vector scores nan against everything and would match nothing."""
    with pytest.raises(ValueError):
        faces.pack_embedding([])


def test_a_degenerate_vector_scores_nan_and_therefore_matches_nothing():
    """Zero-length input: numpy warns and yields nan; this yields nan silently.

    ``nan`` fails every ``>=`` comparison, which is the behaviour that matters --
    a degenerate stored row must match nothing rather than everything.
    """
    score = faces.cosine_similarity([0.0, 0.0], [1.0, 1.0])
    assert math.isnan(score)
    assert not (score >= 0), "nan must fail the threshold comparison"


# --------------------------------------------------------------------------
# the blob
# --------------------------------------------------------------------------


def test_an_embedding_round_trips_through_float32_bytes():
    packed = faces.pack_embedding(BASE)
    assert len(packed) == DIMENSION * 4, (
        "the dimension is recovered from the byte length, so four bytes per "
        "value is a storage contract and not an implementation detail"
    )
    restored = faces.unpack_embedding(packed)
    assert len(restored) == DIMENSION
    for original, value in zip(BASE, restored):
        assert value == pytest.approx(original, abs=1e-6)


def test_a_truncated_blob_is_reported_rather_than_silently_shortened():
    with pytest.raises(faces.EmbeddingMismatch):
        faces.unpack_embedding(faces.pack_embedding(BASE)[:-1])


def test_the_key_is_derived_from_the_model_as_well_as_the_vector():
    """Same vector, different recogniser, different identity. They are not
    comparable and must not collide."""
    assert faces.face_key_for(MODEL, BASE) != faces.face_key_for(OTHER_MODEL, BASE)
    assert faces.face_key_for(MODEL, BASE) == faces.face_key_for(MODEL, list(BASE))
    assert len(faces.face_key_for(MODEL, BASE)) == 32


def test_no_index_ordinal_or_counter_appears_in_a_key():
    """The roadmap forbids an index-derived key, so assert the shape of one.

    Detection order is what upstream and the reference web UI key on; a key
    derived only from content cannot encode it, and registering the same faces
    in the opposite order must produce the same two keys.
    """
    first = faces.face_key_for(MODEL, BASE)
    second = faces.face_key_for(MODEL, FAR)
    assert {first, second} == {
        faces.face_key_for(MODEL, FAR),
        faces.face_key_for(MODEL, BASE),
    }
    for key in (first, second):
        assert set(key) <= set("0123456789abcdef"), key


# --------------------------------------------------------------------------
# survival across a reload -- roadmap criterion 3's face half
# --------------------------------------------------------------------------


def _register(connection, vector, model=MODEL):
    return faces.register_identity(
        connection, PROJECT, model, vector, faces.face_key_for(model, vector)
    )


def test_a_face_seen_again_after_a_reload_finds_its_own_settings(connection):
    """The whole point of the tier: same person, later session, same overrides."""
    stored_key = _register(connection, BASE)
    store.set_face(connection, PROJECT, stored_key, "SimilarityThresholdSlider", 35)

    # A reload: detection runs again and produces a *different* vector for the
    # same person. Exact-digest keying loses here; threshold matching does not.
    assert faces.face_key_for(MODEL, NEAR) != stored_key

    reloaded_key = faces.resolve_identity(connection, PROJECT, MODEL, NEAR)
    assert reloaded_key == stored_key
    assert (
        store.resolve(connection, "SimilarityThresholdSlider", PROJECT, reloaded_key)
        == 35
    )


def test_a_different_face_gets_a_different_identity_and_no_inherited_override(
    connection,
):
    """The other direction. A matcher that returns the same key for everything
    passes the reload test perfectly."""
    stored_key = _register(connection, BASE)
    store.set_face(connection, PROJECT, stored_key, "SimilarityThresholdSlider", 35)

    assert faces.resolve_identity(connection, PROJECT, MODEL, FAR) is None

    stranger_key = _register(connection, FAR)
    assert stranger_key != stored_key
    assert (
        store.resolve(connection, "SimilarityThresholdSlider", PROJECT, stranger_key)
        == 60
    ), "the stranger must fall through to the schema default, not inherit"


def test_registering_the_same_person_twice_yields_one_identity(connection):
    stored_key = _register(connection, BASE)
    again = _register(connection, NEAR)
    assert again == stored_key

    rows = connection.execute(
        "SELECT count(*) FROM project_faces WHERE project_id = ?", (PROJECT,)
    ).fetchone()[0]
    assert rows == 1, "a near-duplicate created a second identity row"


def test_a_match_never_rewrites_the_stored_embedding(connection):
    """Otherwise an identity drifts across a session, one sub-threshold step at
    a time, until it is closer to somebody else than to the face it started as --
    and nothing ever exceeds the threshold in a single step, so nothing reports
    it."""
    stored_key = _register(connection, BASE)
    before = connection.execute(
        "SELECT embedding FROM project_faces WHERE face_key = ?", (stored_key,)
    ).fetchone()[0]

    _register(connection, NEAR)

    after = connection.execute(
        "SELECT embedding FROM project_faces WHERE face_key = ?", (stored_key,)
    ).fetchone()[0]
    assert bytes(after) == bytes(before)


def test_the_threshold_actually_gates_the_match(connection):
    """Tighten it and the reload stops being recognised. Loosen it and a
    stranger starts being. That is the coupling, demonstrated rather than
    described."""
    stored_key = _register(connection, BASE)

    store.set_project(connection, PROJECT, faces.SIMILARITY_THRESHOLD_KEY, 100)
    assert faces.resolve_identity(connection, PROJECT, MODEL, NEAR) is None

    store.set_project(connection, PROJECT, faces.SIMILARITY_THRESHOLD_KEY, 1)
    assert faces.resolve_identity(connection, PROJECT, MODEL, FAR) == stored_key


def test_the_best_match_wins_not_the_first_row(connection):
    """With the threshold loosened enough for two identities to clear it,
    first-past-the-post would hand the settings to whichever key sorted first."""
    base_key = _register(connection, BASE)
    far_key = _register(connection, FAR)
    assert base_key != far_key

    store.set_project(connection, PROJECT, faces.SIMILARITY_THRESHOLD_KEY, 1)
    assert faces.resolve_identity(connection, PROJECT, MODEL, NEAR) == base_key
    assert faces.resolve_identity(connection, PROJECT, MODEL, FAR) == far_key


def test_an_identity_stored_under_another_recogniser_is_a_miss_not_an_error(
    connection,
):
    """Four recognition models, four unrelated vector spaces. A cross-model
    score carries no information at all -- and a project whose recogniser was
    changed mid-flight is in a normal state, not a faulty one."""
    _register(connection, BASE, model=MODEL)

    assert faces.resolve_identity(connection, PROJECT, OTHER_MODEL, BASE) is None
    assert faces.stored_identities(connection, PROJECT, OTHER_MODEL) == []
    assert len(faces.stored_identities(connection, PROJECT, MODEL)) == 1


def test_identities_do_not_leak_between_projects(connection):
    _register(connection, BASE)
    assert (
        faces.resolve_identity(connection, "project-b", MODEL, NEAR) is None
    ), "another project's identity was matched"


def test_a_hostile_project_id_and_model_name_round_trip_as_data(connection):
    """T-03-10. The model-name filter is bound too, not only the project id."""
    hostile_project = "p'); DROP TABLE project_faces; --"
    hostile_model = "m'); DROP TABLE face_settings; --"

    key = faces.register_identity(
        connection,
        hostile_project,
        hostile_model,
        BASE,
        faces.face_key_for(hostile_model, BASE),
    )
    assert (
        faces.resolve_identity(connection, hostile_project, hostile_model, NEAR) == key
    )

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for expected in db.SETTINGS_TABLES:
        assert expected in tables, "{} was dropped -- SQL was interpolated".format(
            expected
        )


def test_forgetting_an_identity_takes_its_overrides_with_it(connection):
    stored_key = _register(connection, BASE)
    store.set_face(connection, PROJECT, stored_key, "SimilarityThresholdSlider", 35)

    assert faces.forget_identity(connection, PROJECT, stored_key) is True
    assert faces.forget_identity(connection, PROJECT, stored_key) is False

    left = connection.execute(
        "SELECT count(*) FROM face_settings WHERE face_key = ?", (stored_key,)
    ).fetchone()[0]
    assert left == 0, (
        "the override outlived the identity it was keyed to -- unreachable "
        "storage that no query will ever return"
    )


def test_the_similarity_function_is_replaceable(connection):
    """The seam Phase 4 uses to delete the duplicate. If it is not honoured,
    passing the engine's own method would change nothing and the duplication
    would become permanent."""
    calls = []

    def always_match(first, second):
        calls.append((len(first), len(second)))
        return 100.0

    _register(connection, BASE)
    assert (
        faces.resolve_identity(
            connection, PROJECT, MODEL, FAR, similarity=always_match
        )
        is not None
    )
    assert calls, "the injected similarity was never called"


# --------------------------------------------------------------------------
# agreement with the engine's own code
# --------------------------------------------------------------------------


def _agreement_cases():
    """The vector pairs and identities both sides are asked about.

    Built here and written to a file the subprocess reads, rather than
    regenerated independently on each side. Two generators that agree today are
    two generators that can stop agreeing.
    """
    return {
        "pairs": [
            [BASE, BASE],
            [BASE, NEAR],
            [BASE, FAR],
            [BASE, [-value for value in BASE]],
            [BASE, [value * 3.5 for value in BASE]],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            [_vectors(7, 4), _vectors(8, 4)],
        ],
        "identities": [
            {"model": MODEL, "vector": BASE},
            {"model": OTHER_MODEL, "vector": BASE},
            {"model": MODEL, "vector": FAR},
        ],
    }


def test_an_engine_interpreter_is_reachable():
    """The precondition of every agreement test below, asserted as its own test.

    Without this the only symptom of a missing engine interpreter is four fixture
    errors, which read as harness trouble. This reads as what it is: the check
    that cannot be skipped could not be run.
    """
    engine_python = resolve_engine_python()
    assert engine_python.exists(), (
        "no engine interpreter at {}. Set {} to an interpreter carrying torch "
        "and numpy. This is deliberately not a skip: the agreement check is the "
        "only thing standing between a transcribed similarity formula and one "
        "person's settings being served for another person's face, and a "
        "skipped one reads green while proving nothing.".format(
            engine_python, ENGINE_PYTHON_ENV_VAR
        )
    )


@pytest.fixture(scope="session")
def engine_agreement(tmp_path_factory):
    """Run the engine's own similarity and face key over the shared cases.

    Session-scoped: the subprocess pays an import of torch, onnxruntime and
    torchvision, and every assertion below is about the same single run.

    A missing engine interpreter **fails**. ``resolve_engine_python`` falls back
    to the interpreter running pytest, which has no torch, and that path reports
    ``DEPS_MISSING`` -- also a failure. Both readings are deliberate.
    """
    engine_python = resolve_engine_python()
    if not engine_python.exists():
        pytest.fail(
            "no engine interpreter at {}. This check is not skippable: it is the "
            "only thing standing between a transcribed similarity formula and "
            "one person's settings being served for another person's face. Set "
            "{} to an interpreter carrying torch and numpy.".format(
                engine_python, ENGINE_PYTHON_ENV_VAR
            )
        )

    cases_path = tmp_path_factory.mktemp("similarity") / "cases.json"
    cases_path.write_text(json.dumps(_agreement_cases()), encoding="utf-8")

    proc = subprocess.run(
        [str(engine_python), "-B", str(RUNNER), str(cases_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    output = (proc.stdout or "").strip()
    if proc.stderr.strip():
        output = (output + "\n[stderr] " + proc.stderr.strip()).strip()

    report = None
    for line in output.splitlines():
        if line.startswith(("CLEAN:", "DEPS_MISSING:", "ENGINE_ERROR:")):
            report = line
    return {"code": proc.returncode, "output": output, "report": report}


def test_the_engine_side_of_the_agreement_check_ran_clean(engine_agreement):
    assert engine_agreement["code"] == 0, (
        "the similarity runner did not exit CLEAN:\n{}\n"
        "DEPS_MISSING (2) means the interpreter it ran on has no torch or no "
        "numpy -- point {} at the engine interpreter.".format(
            engine_agreement["output"], ENGINE_PYTHON_ENV_VAR
        )
    )
    assert engine_agreement["report"], engine_agreement["output"]
    assert engine_agreement["report"].startswith("CLEAN:similarity:"), engine_agreement[
        "output"
    ]


@pytest.fixture(scope="session")
def engine_result(engine_agreement):
    label, _, rest = engine_agreement["report"].partition(":")
    _, _, detail = rest.partition(":")
    assert label == "CLEAN", engine_agreement["output"]
    return json.loads(detail)


def test_the_pure_python_similarity_agrees_with_the_engines_own_method(engine_result):
    """The load-bearing test in this file.

    Disagreement here does not mean a rounding problem. It means the formula was
    read wrong, and every face-tier lookup in the project is suspect.
    """
    cases = _agreement_cases()["pairs"]
    engine_scores = engine_result["scores"]
    assert len(engine_scores) == len(cases), engine_result

    disagreements = []
    for (first, second), engine_score in zip(cases, engine_scores):
        # Narrowed to float32 on this side too: that is what the store holds and
        # what the engine was handed, so anything else compares the
        # transcription against numbers the engine never saw.
        mine = faces.cosine_similarity(
            faces.unpack_embedding(faces.pack_embedding(first)),
            faces.unpack_embedding(faces.pack_embedding(second)),
        )
        if abs(mine - engine_score) > TOLERANCE:
            disagreements.append((len(first), mine, engine_score))

    assert not disagreements, (
        "the pure-Python similarity disagrees with ModelsProcessor."
        "findCosineDistance:\n"
        + "\n".join(
            "  dim={} ours={!r} engine={!r} delta={!r}".format(
                dimension, mine, theirs, mine - theirs
            )
            for dimension, mine, theirs in disagreements
        )
        + "\nThis is not a tolerance problem. The engine returns 100 - "
        "(1 - cos) * 50, a similarity on a 0-to-100 scale, and a "
        "reimplementation on any other scale looks correct and matches at a "
        "completely different tightness."
    )


def test_the_face_key_agrees_with_the_engines_own_face_id(engine_result):
    """If these diverge, the engine hands the store a key it has never seen and
    every face override silently stops resolving -- which is indistinguishable,
    from the outside, from the settings not saving at all."""
    identities = _agreement_cases()["identities"]
    engine_ids = engine_result["face_ids"]
    assert len(engine_ids) == len(identities), engine_result

    mismatches = [
        (identity["model"], faces.face_key_for(identity["model"], identity["vector"]),
         engine_id)
        for identity, engine_id in zip(identities, engine_ids)
        if faces.face_key_for(identity["model"], identity["vector"]) != engine_id
    ]
    assert not mismatches, (
        "faces.face_key_for disagrees with FaceCard.face_id:\n"
        + "\n".join(
            "  model={} ours={} engine={}".format(*row) for row in mismatches
        )
    )
    assert len(set(engine_ids)) == len(engine_ids), (
        "the engine produced a duplicate face id across distinct inputs"
    )


def test_the_agreement_check_is_not_inert(engine_result):
    """Guard against a green agreement that compared nothing worth comparing.

    Two failure modes are possible and neither shows up as a failing assertion
    elsewhere: a case set where every pair scores the same number, and a case set
    where every score is an exact constant that any implementation would hit.
    A run whose scores span identical, orthogonal, opposed and two intermediate
    values is one where a wrong scale or a wrong sign cannot survive.
    """
    scores = engine_result["scores"]
    assert len(set(scores)) >= 5, (
        "the agreement cases do not discriminate: {}".format(sorted(set(scores)))
    )
    assert max(scores) == pytest.approx(faces.IDENTICAL, abs=TOLERANCE), scores
    assert min(scores) == pytest.approx(faces.OPPOSITE, abs=TOLERANCE), scores
    assert any(
        faces.OPPOSITE + 1 < score < faces.IDENTICAL - 1 for score in scores
    ), "no intermediate score: only the fixed points were compared"
    assert engine_result["numpy"], (
        "the engine side did not report a numpy version, so it is not clear "
        "the comparison ran against numpy arithmetic at all"
    )
