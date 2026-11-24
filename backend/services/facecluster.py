"""Clustering and naming for "group by similarity" (plan sections 3-4).

Pure numpy, no I/O and no store access, so the awkward parts -- linkage,
thresholds, naming and conflicts -- are testable without a face library. The API
layer (`backend/api/faces.py`) loads embeddings, calls in here, and writes only
what the user confirms.

Complete linkage on cosine similarity: a cluster is only joined when *every*
pair across it clears the threshold. Single linkage would chain A~B~C into one
group even when A and C are different people, which for a face library means
merging two people into one label -- the failure mode that matters most, since
false merges are worse than missed ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

#: Default for w600k_r50 same-identity, per the plan.
DEFAULT_THRESHOLD = 0.40
#: Clamped so a client cannot ask for "everything in one group" (0.0) or for a
#: threshold nothing can reach (1.0 means only identical vectors).
MIN_THRESHOLD = 0.2
MAX_THRESHOLD = 0.8
#: A proposed group of one is not a group.
MIN_CLUSTER_SIZE = 2


def clamp_threshold(value: Optional[float]) -> float:
    """Bring a requested threshold into range. None -> the default."""
    if value is None:
        return DEFAULT_THRESHOLD
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    if not np.isfinite(v):          # NaN/inf would silently disable the check
        return DEFAULT_THRESHOLD
    return float(min(MAX_THRESHOLD, max(MIN_THRESHOLD, v)))


@dataclass
class Cluster:
    """One proposed group: members in a stable order, plus who it would move."""

    face_ids: list[str] = field(default_factory=list)
    suggested_group: str = ""
    conflicts: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "suggested_group": self.suggested_group,
            "face_ids": list(self.face_ids),
            "conflicts": [dict(c) for c in self.conflicts],
        }


def cosine_float(a, b) -> float:
    """Cosine similarity of two vectors (not necessarily normalised)."""
    av = np.asarray(a, dtype=np.float64).ravel()
    bv = np.asarray(b, dtype=np.float64).ravel()
    denom = float(np.linalg.norm(av) * np.linalg.norm(bv))
    if denom <= 1e-12:
        return 0.0
    return float(np.clip(np.dot(av, bv) / denom, -1.0, 1.0))


def similarity_matrix(vectors: np.ndarray) -> np.ndarray:
    """Cosine similarity of every pair, from L2-normalised rows."""
    m = np.asarray(vectors, dtype=np.float32)
    if m.ndim != 2:
        raise ValueError("vectors must be a 2-D array")
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms <= 1e-12] = 1.0            # a zero vector contributes 0, not NaN
    unit = m / norms
    sim = unit @ unit.T
    return np.clip(sim, -1.0, 1.0)


def cluster(
    face_ids: Sequence[str], vectors: np.ndarray, threshold: float = DEFAULT_THRESHOLD
) -> list[list[str]]:
    """Agglomerative complete-linkage clustering; clusters of MIN_CLUSTER_SIZE+ only.

    Deterministic: ties are broken by the input order of `face_ids`, so the same
    library always yields the same clusters and the same review screen.
    """
    ids = list(face_ids)
    if len(ids) != len(vectors):
        raise ValueError("face_ids and vectors must be the same length")
    if len(ids) < MIN_CLUSTER_SIZE:
        return []
    sim = similarity_matrix(vectors)
    threshold = clamp_threshold(threshold)

    clusters: list[list[int]] = [[i] for i in range(len(ids))]
    while True:
        best: tuple[float, int, int] | None = None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                cross = sim[np.ix_(clusters[a], clusters[b])]
                # Complete linkage: the weakest pair decides.
                weakest = float(cross.min())
                if weakest < threshold:
                    continue
                if best is None or weakest > best[0]:
                    best = (weakest, a, b)
        if best is None:
            break
        _, a, b = best
        clusters[a] = sorted(clusters[a] + clusters[b])
        clusters.pop(b)

    out = [[ids[i] for i in members] for members in clusters if len(members) >= MIN_CLUSTER_SIZE]
    return out


def most_common(values: Iterable[Optional[str]]) -> Optional[str]:
    """Most frequent non-empty value; ties go to the value seen first.

    First-seen tie-breaking keeps naming stable across runs (a dict's insertion
    order on the input order) instead of depending on hashing.
    """
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], -list(counts).index(kv[0])))[0]


def suggest_group(members: Sequence[dict]) -> str:
    """Name a proposed cluster from what its members already say about themselves.

    1. an existing group most members share (respect what the user set up),
    2. else the most common name-detected group,
    3. else nothing -- the caller numbers it "Person N".
    """
    existing = most_common(m.get("group") for m in members)
    if existing:
        return existing
    detected = most_common(m.get("detected") for m in members)
    return detected or ""


def conflicts_for(members: Sequence[dict], suggested: str) -> list[dict[str, str]]:
    """Members that already belong to a different group: reported, never moved."""
    out = []
    for m in members:
        current = m.get("group")
        if current and current != suggested:
            out.append({"face_id": m["face_id"], "current_group": current})
    return sorted(out, key=lambda c: c["face_id"])


def build_clusters(
    faces: Sequence[dict],
    vectors: np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
    name_prefix: str = "Person",
) -> list[Cluster]:
    """Cluster rows of `vectors` (parallel to `faces`) into named proposals.

    Each face dict needs `face_id` and may carry `group` (current) and `detected`
    (from the filename). A proposal is only worth showing when applying it would
    actually change something: a cluster where every member is already in the
    proposed group would rewrite the same bytes, so it is dropped rather than
    added to the review list as a row the user has to dismiss.
    """
    by_id = {f["face_id"]: f for f in faces}
    ordered_ids = [f["face_id"] for f in faces]
    groups = cluster(ordered_ids, vectors, threshold)
    proposed: list[Cluster] = []
    for members_ids in groups:
        members = [by_id[fid] for fid in members_ids]
        suggested = suggest_group(members)
        if suggested and all(m.get("group") == suggested for m in members):
            continue           # nothing to change
        proposed.append(
            Cluster(
                face_ids=list(members_ids),
                suggested_group=suggested,
                conflicts=conflicts_for(members, suggested),
            )
        )
    _apply_placeholders(proposed, name_prefix)
    return proposed


def _apply_placeholders(clusters: list[Cluster], prefix: str) -> None:
    """Number the clusters that could not be named "Person 1", "Person 2", ...

    Numbering is by cluster order (largest first, then first member) and skips
    names already taken, so a suggestion never collides with an existing group.
    """
    taken = {c.suggested_group for c in clusters if c.suggested_group}
    clusters.sort(key=lambda c: (-len(c.face_ids), c.face_ids[0] if c.face_ids else ""))
    n = 0
    for c in clusters:
        if c.suggested_group:
            continue
        while True:
            n += 1
            candidate = f"{prefix} {n}"
            if candidate not in taken:
                break
        c.suggested_group = candidate
        taken.add(candidate)


def similar_to(
    target_id: str,
    candidate_ids: Sequence[str],
    vectors: np.ndarray,
    limit: int = 8,
    exclude: Sequence[str] = (),
) -> list[tuple[str, float]]:
    """`candidate_ids` ranked by similarity to `target_id`, best first.

    `candidate_ids` and `vectors` are parallel and must include the target. The
    target itself and anything in `exclude` are dropped from the ranking.
    """
    ids = list(candidate_ids)
    if target_id not in ids:
        raise ValueError("the target face is not among the candidates")
    if limit <= 0:
        return []
    sim = similarity_matrix(vectors)
    row = sim[ids.index(target_id)]
    skip = {target_id, *exclude}
    scored = [(fid, float(row[i])) for i, fid in enumerate(ids) if fid not in skip]
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:limit]
