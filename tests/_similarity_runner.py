"""Ask the engine itself what two embeddings score, and what a face's key is.

Run as a subprocess on the **engine** interpreter, never imported by pytest --
the same shape as ``_qt_guard_probe.py`` and ``_engine_runner.py``, and for the
same reason. ``visoswap.processors.models_processor`` imports torch, onnxruntime
and torchvision; the interpreter running pytest has none of them.

Two questions are asked here, and both are agreement questions rather than
behaviour questions. ``visoswap/settings/faces.py`` contains a pure-Python
transcription of the engine's similarity formula and a stdlib transcription of
the engine's face-key digest. Either could drift from the original, and both
would drift *quietly*: a similarity on the wrong scale still returns a plausible
number, and a digest computed over the wrong bytes still returns a plausible hex
string. Only comparison against the originals catches it.

``ModelsProcessor.findCosineDistance`` reads nothing but its two arguments, so it
is called **unbound** -- ``ModelsProcessor.findCosineDistance(None, a, b)``. No
instance is constructed, no weights are loaded, and the whole run costs an import.
``FaceCard.face_id`` is likewise a pure property over the embedding it is handed.

Usage::

    python -B tests/_similarity_runner.py <cases.json>

``cases.json`` is written by the test, not by this file, so both sides are
provably scoring the *same* vectors rather than two independently regenerated
sets that could quietly stop matching::

    {"pairs": [[[...], [...]], ...], "identities": [{"model": "...",
     "vector": [...]}, ...]}

Exit codes follow ``_engine_runner.py``'s vocabulary so a harness fault can never
be read as an agreement::

    0  CLEAN          every case was scored
    2  DEPS_MISSING   an engine dependency is not installed
    4  ENGINE_ERROR   anything else

Exactly one machine-readable line is printed, last, after any noise the engine's
own imports emit (``models_processor`` prints on stdout when TensorRT is absent)::

    LABEL:similarity:<json>
"""

import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

EXIT_CLEAN = 0
EXIT_DEPS_MISSING = 2
EXIT_ENGINE_ERROR = 4

MODE = "similarity"


def _report(label, detail, code):
    print("{}:{}:{}".format(label, MODE, detail))
    return code


def main(argv):
    if len(argv) != 1:
        return _report("ENGINE_ERROR", "usage: _similarity_runner.py <cases.json>",
                       EXIT_ENGINE_ERROR)

    with open(argv[0], "r", encoding="utf-8") as handle:
        cases = json.load(handle)

    try:
        import numpy as np

        from visoswap.engine import FaceCard
        from visoswap.processors.models_processor import ModelsProcessor
    except ImportError as error:
        return _report("DEPS_MISSING", repr(str(error)), EXIT_DEPS_MISSING)

    try:
        scores = []
        for first, second in cases.get("pairs", []):
            # float32 on both sides: that is what `faces.pack_embedding` stores
            # and what `FaceCard.face_id` digests, so scoring float64 here would
            # compare the transcription against something the engine never sees.
            vector1 = np.asarray(first, dtype=np.float32)
            vector2 = np.asarray(second, dtype=np.float32)
            # Unbound, with `self` as None. The method reads no instance state.
            scores.append(
                float(ModelsProcessor.findCosineDistance(None, vector1, vector2))
            )

        face_ids = []
        for identity in cases.get("identities", []):
            card = FaceCard(
                embedding_store={
                    identity["model"]: np.asarray(
                        identity["vector"], dtype=np.float32
                    )
                },
                crop=None,
                recognition_model=identity["model"],
            )
            face_ids.append(card.face_id)
    except Exception as error:  # noqa: BLE001 - the exit code is the report
        return _report(
            "ENGINE_ERROR", repr("{}: {}".format(type(error).__name__, error)),
            EXIT_ENGINE_ERROR,
        )

    return _report(
        "CLEAN",
        json.dumps(
            {
                "scores": scores,
                "face_ids": face_ids,
                "numpy": np.__version__,
                "python": sys.version.split()[0],
            }
        ),
        EXIT_CLEAN,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
