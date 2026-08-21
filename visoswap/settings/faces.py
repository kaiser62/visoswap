"""Face identity: turning a recognition embedding into the key the face tier uses.

The face tier is the most specific of the three, and it is the only one whose key
is not a name a human typed. It is derived from what the recogniser saw, which is
what makes it survive a reload: close the project, reopen it, run detection again,
and the face that comes back is *not* byte-identical to the one that went in --
the crop moved by a pixel, the frame was a different frame -- so anything keyed on
exact bytes, on detection order, or on an index has already lost the association.

The reference web UI keys this tier by the ephemeral target index and holds it in
browser memory: ``webui2/app.js:102`` creates the entry as ``faceOptions[index]``
and lines 258, 283, 748 and 778 clear the whole mapping on project load. Lines
746-754 then try to carry entries across a re-detect *by index*, which is the
operation that silently hands face 0's settings to a different person when
detection order changes. There is no per-face persistence upstream to lift, only
a model to borrow, and this module is the part that had to be built rather than
borrowed.

**Standard library only.** ``array``, ``hashlib``, ``math``, ``sqlite3`` and
``logging``. No numpy, no torch. Two reasons, and both are load-bearing: this has
to be testable on the plain developer interpreter, which has no inference stack;
and ``import visoswap.settings`` must never drag twelve gigabytes of weights and a
CUDA context into a process that only wanted to know what type a slider is.

Storage format
--------------
An embedding is stored as raw **little-endian float32** bytes in
``project_faces.embedding``, beside the name of the recognition model that
produced it. The dimension is recoverable from the byte length, so no separate
column is needed and no length can disagree with the blob it describes.

A float64 embedding is narrowed to float32 on the way in. That is safe here for a
reason worth stating rather than assuming: the only decision this vector ever
informs is a comparison against an **integer** threshold on a 0-to-100 scale.
float32 carries about seven significant decimal digits, which is several orders of
magnitude finer than a decision made in whole units out of a hundred. Narrowing
also matches what the engine already does -- ``FaceCard.face_id`` digests
``ascontiguousarray(embedding, dtype=np.float32).tobytes()`` -- so the key derived
here and the key derived there are the same string.

Why the similarity function is injectable
-----------------------------------------
Two implementations of one metric is a genuine risk, not a hypothetical one: if
the settings layer and the swap pipeline disagree about whether two embeddings are
the same face, the engine swaps face A while the settings layer serves face B's
overrides, and every symptom points at the settings being wrong rather than at the
metric. Three things contain that risk, and all three are deliberate:

1. :data:`DEFAULT_SIMILARITY` is a pure-Python transcription of the engine's own
   five-line formula and nothing else -- no "improvement", no normalisation step
   the engine does not do.
2. Every entry point takes a ``similarity=`` callable, so Phase 4 can pass
   ``ModelsProcessor.findCosineDistance`` directly and delete the duplicate.
3. ``tests/test_face_settings.py`` runs both over the same vector pairs, on the
   engine interpreter, and fails on disagreement beyond float tolerance.

The seam exists because of (3). Without the agreement test it would just be a
second copy with an apologetic comment.

Why the threshold is the swap pipeline's threshold
--------------------------------------------------
Identity matching uses the **project-tier similarity threshold key** -- the same
one ``frame_worker`` compares against at three sites -- and not a new constant of
its own. A user who loosens matching for swapping therefore loosens it for
settings identity too. That coupling is deliberate: one notion of "same face" in
the application is better than two that can disagree, and two would disagree
exactly when it hurt most. It is stated here, and in ``docs/settings-schema.md``,
because the alternative reading is that somebody forgot to add a second setting.

Privacy (T-03-13)
-----------------
Recognition embeddings are biometric-derived data and this module writes them to
disk. They are derived from media the single local user supplied, stored in that
user's own project database on that user's own machine, and they are never logged
and never leave the machine. That is a stated fact rather than a discovery waiting
to happen.
"""

import array
import hashlib
import logging
import math
import sqlite3
import sys

from visoswap.settings import store

LOGGER = logging.getLogger(__name__)

__all__ = [
    "SIMILARITY_THRESHOLD_KEY",
    "DEFAULT_SIMILARITY",
    "IDENTICAL",
    "ORTHOGONAL",
    "OPPOSITE",
    "pack_embedding",
    "unpack_embedding",
    "face_key_for",
    "cosine_similarity",
    "threshold_for",
    "stored_identities",
    "resolve_identity",
    "register_identity",
    "forget_identity",
]

#: The project-tier key whose value is the match threshold. Named once, here.
#: ``frame_worker`` compares ``findCosineDistance(...)`` against this same key's
#: value with ``>=`` at three sites; identity matching uses the same key and the
#: same comparison so the two can never drift apart.
SIMILARITY_THRESHOLD_KEY = "SimilarityThresholdSlider"

#: The scale landmarks of the engine's metric, spelled out because the numbers
#: are surprising and a reader who assumes 0-to-1 will pick a threshold that is
#: either always-match or never-match.
IDENTICAL = 100.0
ORTHOGONAL = 50.0
OPPOSITE = 0.0

_FLOAT32 = "f"

if array.array(_FLOAT32).itemsize != 4:
    # Never true on any platform CPython supports, but the blob format is a
    # persistence contract and a silent 8-byte 'f' would corrupt every stored
    # embedding while every test still passed.
    raise RuntimeError(
        "array typecode 'f' is {} bytes on this platform, not 4; the stored "
        "embedding format assumes float32".format(array.array(_FLOAT32).itemsize)
    )


class EmbeddingMismatch(ValueError):
    """Raised when two embeddings cannot be compared at all.

    Different dimensions under the same recognition model means something other
    than a near-miss happened -- a model was swapped without its name changing,
    or a row was hand-edited. It is not a low score, and reporting it as one
    would hide it.
    """


# --------------------------------------------------------------------------
# the blob format
# --------------------------------------------------------------------------


def pack_embedding(embedding) -> bytes:
    """An iterable of numbers -> little-endian float32 bytes.

    Accepts anything iterable that yields floats, which includes a numpy array,
    a list and a ``memoryview`` -- so a caller holding a numpy vector needs no
    conversion step and this module still needs no numpy.

    Little-endian explicitly, not "whatever this machine is". A database file is
    portable and a native-order blob is not.
    """
    values = array.array(_FLOAT32, (float(value) for value in embedding))
    if not values:
        raise ValueError(
            "refusing to store an empty embedding: an empty vector scores nan "
            "against everything and would match nothing forever"
        )
    if _needs_swap():
        values.byteswap()
    return values.tobytes()


def unpack_embedding(blob) -> list:
    """Little-endian float32 bytes -> a list of Python floats.

    The dimension comes from ``len(blob) // 4``; there is no stored length that
    could disagree with the bytes it describes.
    """
    if len(blob) % 4:
        raise EmbeddingMismatch(
            "stored embedding is {} bytes, which is not a whole number of "
            "float32 values".format(len(blob))
        )
    values = array.array(_FLOAT32)
    values.frombytes(bytes(blob))
    if _needs_swap():
        values.byteswap()
    return values.tolist()


def _needs_swap() -> bool:
    return sys.byteorder != "little"


# --------------------------------------------------------------------------
# the key
# --------------------------------------------------------------------------


def face_key_for(recognition_model: str, embedding) -> str:
    """The content-addressed key for a first sighting of this face.

    **This must produce byte-for-byte the same string as
    ``visoswap.engine.FaceCard.face_id``.** Same digest (blake2b, 16-byte
    output), same order (model name as UTF-8, then the float32 bytes), same
    narrowing. If the two ever diverge, the engine hands the store a key the
    store has never seen and every face override silently stops resolving --
    which looks exactly like the settings not saving.
    ``tests/test_face_settings.py`` asserts the agreement against the real
    ``FaceCard`` on the engine interpreter rather than trusting this note.

    A digest is one way, so this is only ever the key for a face that has not
    been seen before. Recognising a face that *has* been seen is
    :func:`resolve_identity`'s job, and it is threshold matching, not hashing --
    two detections of one person one frame apart differ in the last few bits and
    digest to two different strings.
    """
    digest = hashlib.blake2b(digest_size=16)
    digest.update(recognition_model.encode("utf-8"))
    digest.update(pack_embedding(embedding))
    return digest.hexdigest()


# --------------------------------------------------------------------------
# the metric
# --------------------------------------------------------------------------


def cosine_similarity(vector1, vector2) -> float:
    """The engine's ``findCosineDistance``, transcribed, in pure Python.

    Despite the upstream name this returns a **similarity on a 0-to-100 scale**,
    not a distance and not a 0-to-1 anything:

    ==========================  =====
    identical vectors           100
    orthogonal vectors           50
    opposed vectors               0
    ==========================  =====

    The arithmetic upstream is ``cos_dist = 1 - cos``, which lands in 0..2, then
    ``100 - cos_dist * 50``. Scale is invisible to it, so a vector and twice that
    vector score 100.

    Reimplementing this as a 0-to-1 cosine similarity and comparing against a
    0-to-1 threshold would appear to work and would match at a completely
    different tightness -- the default threshold of 60 means "cosine above 0.2",
    which is *loose*, and a reader who assumes 0-to-1 would read 60 as tight.
    That is the single most expensive mistake available in this file.

    Zero-length input returns ``nan``, matching numpy's behaviour on the same
    expression (numpy warns and yields nan; this does not warn). ``nan`` fails
    every ``>=`` comparison, so a degenerate vector matches nothing rather than
    matching everything.
    """
    if len(vector1) != len(vector2):
        raise EmbeddingMismatch(
            "cannot compare embeddings of {} and {} dimensions".format(
                len(vector1), len(vector2)
            )
        )
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for a, b in zip(vector1, vector2):
        dot += a * b
        norm1 += a * a
        norm2 += b * b
    denominator = math.sqrt(norm1) * math.sqrt(norm2)
    if denominator == 0.0:
        return float("nan")
    cos_dist = 1.0 - dot / denominator
    return 100.0 - cos_dist * 50.0


#: The default matcher. Replace it per call, never in place.
DEFAULT_SIMILARITY = cosine_similarity


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------


def threshold_for(connection: sqlite3.Connection, project_id=None, models_dir=None):
    """The match threshold for ``project_id``: the swap pipeline's own threshold.

    Read at the **project** tier deliberately. The face tier could override this
    key too, but at the moment identity is being resolved there is no face key
    yet -- that is the answer being computed -- so consulting a per-face override
    would require knowing the result in order to find it.
    """
    return store.resolve(
        connection, SIMILARITY_THRESHOLD_KEY, project_id, models_dir=models_dir
    )


def stored_identities(connection, project_id, recognition_model):
    """``[(face_key, embedding)]`` for one project under one recognition model.

    Filtered by model **in SQL**, not scored and discarded. The four recognition
    models produce embeddings in unrelated vector spaces, so a cross-model score
    carries no information at all rather than a little. Filtering in SQL also means
    a project whose recognition model was changed mid-flight simply sees no
    stored identities under the new model, which is a normal state to be in and
    not a fault to raise on.
    """
    rows = connection.execute(
        "SELECT face_key, embedding FROM project_faces "
        "WHERE project_id = ? AND recognition_model = ? "
        "ORDER BY face_key",
        (project_id, recognition_model),
    ).fetchall()
    return [(row[0], unpack_embedding(row[1])) for row in rows]


def resolve_identity(
    connection,
    project_id,
    recognition_model,
    embedding,
    threshold=None,
    similarity=None,
    models_dir=None,
):
    """The key of the stored identity this embedding *is*, or ``None``.

    Scores against every identity stored for this project under this recognition
    model and returns the key of the **best** match at or above ``threshold``.
    Best rather than first: with two people in a project, first-past-the-post
    would hand the settings to whichever row sorted earlier once the threshold
    was loosened enough for both to clear it.

    ``threshold`` defaults to :func:`threshold_for`. ``similarity`` defaults to
    :data:`DEFAULT_SIMILARITY`; pass the engine's own bound method to remove the
    second implementation entirely.

    Returning ``None`` means "not seen before", and the caller's next move is
    :func:`register_identity`. It never means "error": a project with no stored
    faces, a project whose recognition model just changed, and a genuinely new
    person are all the same answer, because they all lead to the same action.
    """
    if threshold is None:
        threshold = threshold_for(connection, project_id, models_dir=models_dir)
    scorer = similarity or DEFAULT_SIMILARITY

    probe = unpack_embedding(pack_embedding(embedding))

    best_key = None
    best_score = None
    for face_key, stored in stored_identities(
        connection, project_id, recognition_model
    ):
        if len(stored) != len(probe):
            # Same model name, different dimension: a hand-edited row or a model
            # swapped without renaming. Not comparable, and not this function's
            # to repair -- skip it rather than raise, so one bad row cannot make
            # every subsequent detection fail.
            LOGGER.warning(
                "ignoring stored identity with %d dimensions under %r; the "
                "probe has %d",
                len(stored),
                recognition_model,
                len(probe),
            )
            continue
        score = scorer(probe, stored)
        if not (score >= threshold):
            # Written as `not (score >= threshold)` rather than `score <
            # threshold` so a nan score -- a degenerate stored vector -- is a
            # miss. `nan < threshold` is False and would let it through.
            continue
        if best_score is None or score > best_score:
            best_key = face_key
            best_score = score
    return best_key


def register_identity(
    connection,
    project_id,
    recognition_model,
    embedding,
    face_key,
    threshold=None,
    similarity=None,
    models_dir=None,
):
    """Match this embedding to a stored identity, or store it as a new one.

    Returns the key the caller should use for the face tier: the **existing**
    key when the embedding matches something already stored, otherwise
    ``face_key`` -- which the caller derives from the embedding itself, via
    ``FaceCard.face_id`` or :func:`face_key_for`.

    That is the whole replacement plan 02-02 recorded as needed. Exact-digest
    keying becomes threshold matching, and the digest survives as the key for a
    first sighting, so an identity is still a content-derived string rather than
    a counter -- there is no sequence anywhere in this module, and reopening a
    project cannot renumber anybody.

    The stored embedding is the **first** one seen for an identity and is never
    updated by a later match. Rewriting it on every match would let an identity
    drift across a session, one small step at a time, until it was closer to a
    different person than to the face it started as; nothing would ever exceed
    the threshold in a single step, so nothing would ever report it.
    """
    existing = resolve_identity(
        connection,
        project_id,
        recognition_model,
        embedding,
        threshold=threshold,
        similarity=similarity,
        models_dir=models_dir,
    )
    if existing is not None:
        return existing

    connection.execute(
        "INSERT INTO project_faces "
        "(project_id, face_key, recognition_model, embedding, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(project_id, face_key) DO NOTHING",
        (project_id, face_key, recognition_model, pack_embedding(embedding)),
    )
    return face_key


def forget_identity(connection, project_id, face_key) -> bool:
    """Drop an identity and every face-tier override stored under it.

    Both, in that order, because the row in ``face_settings`` has no meaning
    without the identity it is keyed to and would otherwise be unreachable
    storage that no query ever returns.
    """
    connection.execute(
        "DELETE FROM face_settings WHERE project_id = ? AND face_key = ?",
        (project_id, face_key),
    )
    cursor = connection.execute(
        "DELETE FROM project_faces WHERE project_id = ? AND face_key = ?",
        (project_id, face_key),
    )
    return cursor.rowcount > 0
