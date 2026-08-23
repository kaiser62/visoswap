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

## The engine generator

`EngineFrameGenerator` sits behind the unchanged eight-member `FrameGenerator`
seam. Settings come from the Phase 3 store, not a hand-written dict: the global
tier (`store.resolve_control`, all 33 keys) and the project tier
(`store.resolve_parameters`, all 168 keys) are resolved from the backend's own
app DB each time an engine is built, so the engine's unconditional control and
parameter reads never hit a sparse dict.

The source face is carried on the project row as `source_face_path` — a
project-authored absolute path, not a name resolved inside VisoMaster's folder.
The predecessor resolved faces by name from VisoMaster's own source directory,
which this repo drops. The column is server-assigned (the tracer / a trusted
caller sets it directly on the row) and is stripped from the public API
response alongside `video_path`, so absolute filesystem paths never reach the
browser.

## The tracer's proof

The end-to-end tracer drives ONE generation request through
`backend/api/generation.py` — not through the adapter directly — on the
combined interpreter, with the Qt seal armed and `backend` left off the block
list. Measured output line:

```
CLEAN:tracer:frame=000000.000.jpg size=336835 provider=CUDA elapsed=10.7s reachable_before_seal=PyQt5=no,PyQt6=no,PySide2=no,PySide6=no,app=no,qtpy=no,shiboken2=no,shiboken6=no
```

This is roadmap criterion 5's first proof: a generation request through the
backend produced a frame file on disk via the vendored engine, in a process
where PySide6 was not importable.
