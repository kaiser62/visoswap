# Backend port

The `backend/` tree is adopted from VisoSwap's predecessor,
`config-parallel-setup`, rather than vendored from VisoMaster. It is therefore
project-authored code and is listed explicitly in the attribution gate's
`PROJECT_AUTHORED` allow-list.

## Runtime decision

The combined application runtime is `.venv-clean/Scripts/python.exe` (Python
3.10). It already carries the pinned CUDA inference stack, so the pinned web
stack is installed there instead of duplicating multi-gigabyte Torch packages
in a second virtual environment. The web requirements relax the predecessor's
`pillow==11.1.0` to the engine's working `pillow==9.5.0`; the backend uses only
the stable `Image.open`, `convert`, `resize`, `split`, `merge`, `tobytes`, and
`save` APIs.

## Backend reduction

The port retains 24 Python files. It intentionally does not carry
`services/comfyui.py`, `services/inprocess.py`, or `services/visomaster.py`.
The remaining generator seam is `FrameGenerator`; the engine adapter lands
behind that seam in the next task.

The predecessor's three generation values collapse to the one in-process
engine value, `engine`. The database retains its `backend` column as the
future extension seam, but removes the workflow URL and per-frame prompt-id
columns. Existing rows are migrated to `engine`. The request schemas lose
workflow settings, and the old backend routes are removed; `FrameGenerator` is
the seam a future optional backend would use.

The provider default changes from TensorRT to CUDA. TensorRT is rejected
because its relative engine-cache path can write outside the intended project
directory.
