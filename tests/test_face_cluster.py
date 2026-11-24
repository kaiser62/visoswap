"""Clustering and naming: pure functions, so the properties can be stated directly."""

from __future__ import annotations

import numpy as np
import pytest

from backend.services import facecluster as C


def unit(*xs) -> np.ndarray:
    v = np.asarray(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


def bunch(*vectors) -> np.ndarray:
    return np.stack([np.asarray(v, dtype=np.float32) for v in vectors])


# --------------------------------------------------------------------------- threshold
def test_threshold_defaults_and_clamps():
    assert C.clamp_threshold(None) == C.DEFAULT_THRESHOLD
    assert C.clamp_threshold(0.1) == C.MIN_THRESHOLD
    assert C.clamp_threshold(5) == C.MAX_THRESHOLD
    assert C.clamp_threshold(0.5) == 0.5
    assert C.clamp_threshold(-1) == C.MIN_THRESHOLD


@pytest.mark.parametrize("bad", ["", "abc", None, float("nan"), float("inf"), object()])
def test_a_nonsense_threshold_falls_back_to_the_default(bad):
    assert C.clamp_threshold(bad) == C.DEFAULT_THRESHOLD


def test_the_clamp_bounds_are_the_documented_ones():
    assert (C.MIN_THRESHOLD, C.MAX_THRESHOLD, C.DEFAULT_THRESHOLD) == (0.2, 0.8, 0.40)


# --------------------------------------------------------------------------- linkage
def test_chain_a_b_c_with_a_not_c_stays_split():
    """The property complete linkage exists for.

    A~B = 0.75, B~C = 0.75, A~C = -0.2: single linkage would chain all three
    into one person. Complete linkage must refuse, because some pair in the
    merged cluster is below the threshold.
    """
    a = unit(1, 0, 0)
    b = unit(0.62, 0.48, 0)          # cos(a,b) = 0.79
    c = unit(0.30, 0.95, 0)          # cos(b,c) = 0.82, but cos(a,c) = 0.30
    ids = ["a" * 32, "b" * 32, "c" * 32]
    assert C.cosine_float(a, c) < 0.40, "the fixture must keep A and C below the threshold"
    got = C.cluster(ids, bunch(a, b, c), threshold=0.40)
    assert all(len(g) < 3 for g in got), f"chained into one cluster: {got}"
    # B is similar to both, so it may sit with either; A and C must never share a group.
    for g in got:
        assert not ({ids[0], ids[2]} <= set(g)), f"A and C were merged: {got}"


def test_two_close_faces_do_cluster():
    a = unit(1, 0, 0)
    b = unit(0.98, 0.19, 0)          # cos ~ .98
    ids = ["a" * 32, "b" * 32]
    assert C.cluster(ids, bunch(a, b), threshold=0.40) == [ids]


def test_complete_linkage_is_stricter_than_single_for_a_chain():
    """Six points in a line, adjacent cos ~.9, ends cos ~.5: single linkage merges
    all six, complete linkage splits at the threshold."""
    # Points on an arc: each neighbour scores ~0.99, the two ends ~0.70. At the
    # maximum threshold (0.8) complete linkage must refuse to merge the whole
    # chain, even though every adjacent pair passes comfortably.
    angles = np.linspace(0, 0.795, 6)
    vecs = np.stack([unit(np.cos(t), np.sin(t)) for t in angles])
    ids = [f"{i:032x}" for i in range(6)]
    assert float(vecs[0] @ vecs[-1]) < C.MAX_THRESHOLD < float(vecs[0] @ vecs[1])
    got = C.cluster(ids, vecs, threshold=C.MAX_THRESHOLD)
    assert max(len(g) for g in got) < 6, f"chained end to end: {got}"


def test_a_single_face_is_never_a_cluster():
    assert C.cluster(["a" * 32], bunch(unit(1, 0, 0)), threshold=0.40) == []
    assert C.cluster([], np.zeros((0, 2), np.float32), threshold=0.40) == []


def test_clusters_are_deterministic_across_runs():
    rng = np.random.default_rng(7)
    vecs = rng.normal(size=(12, 8)).astype(np.float32)
    ids = [f"{i:032x}" for i in range(12)]
    first = C.cluster(ids, vecs, threshold=0.3)
    second = C.cluster(ids, vecs, threshold=0.3)
    assert first == second


def test_cluster_members_follow_input_order():
    a, b = unit(1, 0), unit(0.99, 0.14)
    ids = ["b" * 32, "a" * 32]       # deliberately not sorted
    assert C.cluster(ids, bunch(a, b), threshold=0.4) == [ids]


def test_a_mismatched_length_is_refused():
    with pytest.raises(ValueError):
        C.cluster(["a" * 32], np.zeros((2, 2), np.float32))


def test_every_face_lands_in_at_most_one_cluster():
    vecs = bunch(unit(1, 0), unit(0.99, 0.1), unit(0, 1), unit(0.05, 0.99))
    ids = [f"{i:032x}" for i in range(4)]
    got = C.cluster(ids, vecs, threshold=0.4)
    flat = [f for g in got for f in g]
    assert len(flat) == len(set(flat))


# --------------------------------------------------------------------------- similarity matrix
def test_similarity_matrix_is_symmetric_with_a_unit_diagonal():
    vecs = bunch(unit(1, 0, 0), unit(0.9, 0.43, 0), unit(0, 1, 0))
    m = C.similarity_matrix(vecs)
    assert np.allclose(m, m.T)
    assert np.allclose(np.diag(m), 1.0)


def test_a_zero_vector_does_not_produce_nan():
    m = C.similarity_matrix(bunch(unit(1, 0), np.zeros(2, np.float32)))
    assert np.isfinite(m).all()


# --------------------------------------------------------------------------- naming
def test_most_common_prefers_the_frequent_value():
    assert C.most_common(["a", "b", "a"]) == "a"
    assert C.most_common(["b", None, "b"]) == "b"


def test_most_common_ignores_empty_values():
    assert C.most_common([None, "", "x"]) == "x"
    assert C.most_common([None, ""]) is None


def test_most_common_breaks_ties_by_first_seen():
    assert C.most_common(["z", "y"]) == "z"
    assert C.most_common(["y", "z"]) == "y"


def test_an_existing_group_wins_a_naming_tie():
    members = [{"face_id": "a" * 32, "group": "Alice", "detected": "Bob"}]
    assert C.suggest_group(members) == "Alice"


def test_else_the_detected_name_is_used():
    members = [{"face_id": "a" * 32, "detected": "Alice"}, {"face_id": "b" * 32, "detected": "Alice"}]
    assert C.suggest_group(members) == "Alice"


def test_else_no_name_is_offered():
    assert C.suggest_group([{"face_id": "a" * 32}]) == ""


def test_conflicts_name_the_current_group_and_are_sorted():
    members = [
        {"face_id": "c" * 32, "group": "Carol"},
        {"face_id": "a" * 32, "group": "Alice"},
        {"face_id": "b" * 32, "group": "Alice"},
    ]
    got = C.conflicts_for(members, "Alice")
    assert got == [{"face_id": "c" * 32, "current_group": "Carol"}]


def test_a_member_already_in_the_suggested_group_is_not_a_conflict():
    members = [{"face_id": "a" * 32, "group": "Alice"}]
    assert C.conflicts_for(members, "Alice") == []


# --------------------------------------------------------------------------- build_clusters
def _faces(n, group=None, detected=None):
    return [{"face_id": f"{i:032x}", "group": group, "detected": detected} for i in range(n)]


def test_proposals_are_numbered_person_1_person_2():
    vecs = bunch(unit(1, 0), unit(0.99, 0.1), unit(0, 1), unit(0.05, 0.99))
    got = C.build_clusters(_faces(4), vecs, threshold=0.4)
    assert [c.suggested_group for c in got] == ["Person 1", "Person 2"]
    assert [len(c.face_ids) for c in got] == [2, 2]


def test_numbering_skips_a_name_already_in_use():
    vecs = bunch(unit(1, 0), unit(0.99, 0.1), unit(0, 1), unit(0.05, 0.99))
    faces = _faces(2) + [{"face_id": f"{2:032x}", "group": "Person 1"}, {"face_id": f"{3:032x}"}]
    got = C.build_clusters(faces, vecs, threshold=0.4)
    names = [c.suggested_group for c in got]
    assert len(names) == len(set(names)), f"a suggestion collided with an existing group: {names}"


def test_a_cluster_that_changes_nothing_is_dropped():
    """Every member already in the proposed group: applying it writes nothing, so
    proposing it is just a row the user has to dismiss."""
    vecs = bunch(unit(1, 0), unit(0.99, 0.1))
    faces = [{"face_id": "a" * 32, "group": "Alice"}, {"face_id": "b" * 32, "group": "Alice"}]
    assert C.build_clusters(faces, vecs, threshold=0.4) == []


def test_a_cluster_with_one_conflicting_member_is_kept_and_reported():
    vecs = bunch(unit(1, 0), unit(0.99, 0.1))
    faces = [{"face_id": "a" * 32, "group": "Alice"}, {"face_id": "b" * 32, "group": "Bob"}]
    got = C.build_clusters(faces, vecs, threshold=0.4)
    assert len(got) == 1
    assert got[0].suggested_group == "Alice"
    assert got[0].conflicts == [{"face_id": "b" * 32, "current_group": "Bob"}]


def test_ordering_is_largest_first_then_by_first_member():
    vecs = bunch(unit(1, 0), unit(0.99, 0.1), unit(0.98, 0.2), unit(0, 1), unit(0.05, 0.99))
    got = C.build_clusters(_faces(5), vecs, threshold=0.4)
    assert [len(c.face_ids) for c in got] == sorted((len(c.face_ids) for c in got), reverse=True)


def test_a_cluster_serialises_without_vectors():
    got = C.build_clusters(_faces(2), bunch(unit(1, 0), unit(0.99, 0.1)), threshold=0.4)
    d = got[0].to_dict()
    assert set(d) == {"suggested_group", "face_ids", "conflicts"}
    assert "embedding" not in str(d) and "vector" not in str(d)


# --------------------------------------------------------------------------- similar_to
def test_similar_ranks_best_first_and_excludes_the_target():
    vecs = bunch(unit(1, 0), unit(0.99, 0.1), unit(0.8, 0.6), unit(0, 1))
    ids = [f"{i:032x}" for i in range(4)]
    got = C.similar_to(ids[0], ids, vecs, limit=2)
    assert [f for f, _ in got] == [ids[1], ids[2]]
    assert all(f != ids[0] for f, _ in got)


def test_similar_respects_limit_and_exclusions():
    vecs = bunch(unit(1, 0), unit(0.99, 0.1), unit(0.8, 0.6))
    ids = [f"{i:032x}" for i in range(3)]
    assert len(C.similar_to(ids[0], ids, vecs, limit=1)) == 1
    assert C.similar_to(ids[0], ids, vecs, limit=8, exclude=[ids[1]]) == [(ids[2], pytest.approx(0.8, abs=0.01))]
    assert C.similar_to(ids[0], ids, vecs, limit=0) == []


def test_similar_refuses_a_target_that_is_not_a_candidate():
    with pytest.raises(ValueError):
        C.similar_to("z" * 32, ["a" * 32], bunch(unit(1, 0)))


def test_similar_scores_are_in_range():
    rng = np.random.default_rng(3)
    vecs = rng.normal(size=(6, 4)).astype(np.float32)
    ids = [f"{i:032x}" for i in range(6)]
    for _, score in C.similar_to(ids[0], ids, vecs, limit=5):
        assert -1.0 <= score <= 1.0
