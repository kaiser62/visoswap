"""Pick the one face in a video that gets the swap.

The rule is female-first, largest-second. Two things make that harder than it
sounds, and both are why this file exists rather than a `max()` call at the
call site:

*Prominence is not available where the faces are.* VisoMaster's face cards
carry a fixed 112x112 crop and no bounding box, so "biggest face" has to come
from a separate `run_detect` pass, matched back to the cards by recognition
embedding -- index does not work, because cards are deduplicated and the two
passes therefore return different counts.

*The gender classifier is only sometimes right.* Measured over 138 faces from
112 source images: male labels cluster at margins of 0.2-3.5 while confident
female labels sit at 4-12, and sub-1.0 calls in either direction are coin
flips. So a label is only believed above a threshold; below it the face is
`unknown` and ranked on size alone.

Nothing here imports onnxruntime or torch -- the ranking is a pure function so
it can be tested without a GPU. `classify_crop` takes an already-built session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Below this the classifier's label carries no information. See module docs.
DEFAULT_MARGIN_THRESHOLD = 1.0

#: insightface's genderage model wants a 96x96 aligned crop.
_GENDERAGE_SIZE = 96


@dataclass(frozen=True)
class Candidate:
    """One detected face, as far as selection is concerned."""

    index: int
    gender: str
    margin: float
    area: float


def choose_target(
    candidates: list[Candidate], threshold: float = DEFAULT_MARGIN_THRESHOLD
) -> int:
    """Index of the face to swap.

    Ranked by confident-female first, then area, then detection order. Ties go
    to the earlier face so the same video always binds the same way.
    """
    if not candidates:
        raise ValueError("cannot choose a target face from an empty candidate list")

    def key(c: Candidate) -> tuple[int, float, int]:
        confident_female = int(c.gender == "female" and c.margin >= threshold)
        # Negative index so that, at equal rank and area, the earlier face wins.
        return (confident_female, c.area, -c.index)

    return max(candidates, key=key).index


def classify_crop(session: Any, input_name: str, crop_bgr: Any) -> tuple[str, float]:
    """Gender and confidence margin for one aligned face crop.

    The crop arrives BGR: `card_actions.find_target_faces` swaps the detector's
    RGB output before storing it on the card. insightface's genderage wants RGB
    -- its own preprocessing passes `swapRB=True` -- so the channels are flipped
    back here.

    This is not cosmetic. Measured on the same clips, feeding the stored BGR
    straight through flipped a face's label between adjacent frames, while the
    flipped version held one label across all 13 detections at 3.4x the mean
    confidence margin.
    """
    import cv2
    import numpy as np

    arr = np.asarray(crop_bgr)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"expected an HxWx3 crop, got shape {arr.shape}")

    rgb = arr[..., ::-1]
    resized = cv2.resize(rgb.astype(np.uint8), (_GENDERAGE_SIZE, _GENDERAGE_SIZE))
    blob = resized.transpose(2, 0, 1)[None].astype(np.float32)
    scores = session.run(None, {input_name: blob})[0][0]

    female, male = float(scores[0]), float(scores[1])
    return ("female" if female > male else "male", abs(female - male))
