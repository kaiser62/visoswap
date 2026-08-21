# Vendored from VisoMaster (https://github.com/visomaster/VisoMaster).
# VisoMaster is licensed GPLv3; this vendored copy inherits that license.
# This file was vendored into VisoSwap and may have been modified from upstream.
# See NOTICE for vendoring provenance and LICENSE for the full GPLv3 text.

"""The subset of VisoMaster's ``app/helpers/miscellaneous.py`` the engine needs.

Upstream's module is mostly UI-side: directory walking for the file browser,
video/image extension sniffing, an ffmpeg-on-PATH check, a benchmark decorator.
None of it is imported by a vendored processor. Vendoring it wholesale would
pull ``cv2`` and a pile of dead code into the engine's import graph for no gain.

What the vendored tree actually imports is this and nothing else:

* ``t512``/``t384``/``t256``/``t128`` and the ``get_scaling_transforms()`` that
  produces them -- used by ``workers/frame_worker.py`` (vendored in plan 01-04).
* ``is_file_exists`` -- used by ``face_swappers.py`` and by ``models_processor``
  (vendored in plan 01-03).

The three definitions below are copied byte for byte from upstream. The resize
transforms carry specific interpolation and antialias settings; retyping them
from memory would change model input silently, which is the kind of bug that
shows up as slightly worse output and never as an error.
"""

from pathlib import Path
from torchvision.transforms import v2

def get_scaling_transforms():
    t512 = v2.Resize((512, 512), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
    t384 = v2.Resize((384, 384), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
    t256 = v2.Resize((256, 256), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
    t128 = v2.Resize((128, 128), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
    return t512, t384, t256, t128  

t512, t384, t256, t128 = get_scaling_transforms()

def is_file_exists(file_path: str) -> bool:
    if not file_path:
        return False
    return Path(file_path).is_file()
