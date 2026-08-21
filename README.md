# VisoSwap

A self-contained video face-swap application: you load a video, pick a source
face, and the swapped result plays back in real time while frames are generated
asynchronously behind the playhead. Playback never blocks on generation — if a
generated frame is missing at its timestamp, the original video frame is shown,
so the video does not pause, wait, or stutter. It is the working `visomaster_live`
project cut free of its dependency on a local VisoMaster install: the inference
code is vendored into `visoswap/`, the Qt UI is dropped entirely, and every
setting is explicit and stored rather than inherited from whatever a GUI last
wrote to disk. Built for a single technical user running it on their own GPU
machine.

## Status

Early. Phase 1 of the roadmap — vendoring the engine and stripping Qt — is in
progress. See `.planning/ROADMAP.md`.

## Layout

```
visoswap/                   vendored engine, GPLv3
  processors/               from VisoMaster's app/processors, Qt stripped
tests/                      including the Qt-reachability gate
```

## The Qt-reachability gate

`visoswap/` must import with no Qt binding reachable. That is enforced, not
asserted: `tests/_qt_guard_probe.py` installs a `sys.meta_path` finder that
refuses all seven Qt binding roots — `PySide6`, `PySide2`, `PyQt5`, `PyQt6`,
`qtpy`, `shiboken6`, `shiboken2` — and then imports the vendored modules.
Blocking `PySide6` alone is not enough; the transitive chain out of VisoMaster's
worker code reaches Qt through `qtpy` independently.

```
python -m pytest tests/ -q
```

The probe runs as a subprocess on an interpreter carrying the engine's runtime
dependencies. That interpreter defaults to
`D:/Visomaster/dependencies/Python/python.exe` and is overridable with
`VISOSWAP_ENGINE_PYTHON`. That interpreter has PySide6 installed, deliberately:
proving vendored code cannot reach Qt where Qt *is* installed is a stronger
result than proving it where Qt is simply absent.

The probe distinguishes its failure modes by exit code — `0` clean, `1` Qt was
reached, `2` an engine dependency is missing, `3` any other import error — so a
missing dependency can never be mistaken for a Qt-cleanliness pass. No test in
the gate has a skip path.

## Licensing

**VisoSwap is licensed under the GNU General Public License, version 3 or later.**
The full text is in [LICENSE](LICENSE).

GPLv3 is forced, not chosen. VisoSwap vendors inference code from
[VisoMaster](https://github.com/visomaster/VisoMaster), which is GPLv3, so the
obligation attached the moment the first file landed. Every vendored file under
`visoswap/` carries an attribution header naming VisoMaster as its origin, and
`tests/test_vendor_headers.py` fails the build if one loses it.

Attribution and the provenance of the vendored copy are recorded in
[NOTICE](NOTICE). Read it before redistributing.

### Model weights are non-commercial

The insightface model weights this project depends on — including
`genderage.onnx`, which the face selection relies on — are licensed for
**non-commercial research use only**. This is a separate obligation from the GPL
and is not covered by it. It blocks nothing about running, modifying, or
redistributing this source; it blocks any commercial deployment.
