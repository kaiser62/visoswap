# VisoSwap

[![Open in Kaggle](https://kaggle.com/static/images/open-in-kaggle.svg)](https://kaggle.com/kernels/welcome?src=https://github.com/kaiser62/visoswap/blob/master/visoswap_kaggle.ipynb)

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

## Run on Free Kaggle GPU

You can run VisoSwap directly on Kaggle with a free NVIDIA GPU (T4 x2 or P100):

1. Click **[![Open in Kaggle](https://kaggle.com/static/images/open-in-kaggle.svg)](https://kaggle.com/kernels/welcome?src=https://github.com/kaiser62/visoswap/blob/master/visoswap_kaggle.ipynb)** to import [visoswap_kaggle.ipynb](visoswap_kaggle.ipynb) into Kaggle (or upload `visoswap_kaggle.ipynb` as a new notebook).
2. Set notebook **Settings**:
   - **Accelerator**: `GPU T4 x2` or `GPU P100`
   - **Internet**: `Internet on`
3. Click **Run All**.
4. The last cell prints a public Cloudflare tunnel link (e.g. `https://xxxx.trycloudflare.com`). Open it in your desktop browser or mobile Safari/Chrome to access the full VisoSwap Studio Web UI with live streaming and theater mode!

### Access key (optional shared secret)

Set `VISOSWAP_API_KEY` to require a key on the API, WebSockets and `/url=` shortcuts (empty = auth off;
`/api/health` and the static UI stay open; `/api/health` reports `auth: "required"|"off"`). Clients send header
`X-VisoSwap-Key`, or the web UI logs in once and keeps an HttpOnly cookie.

- **Kaggle**: add a Kaggle Secret named `VISOSWAP_API_KEY` and attach it to the notebook; the notebook exports it
  before starting the backend (never printed).
- **Colab**: no notebook lives in this repo. The Colab launcher/agent script must pass `VISOSWAP_API_KEY` through
  to the `uvicorn backend.main:app` process environment (e.g. `os.environ['VISOSWAP_API_KEY'] = userdata.get('VISOSWAP_API_KEY')`).

**One-click launchers** (Kaggle and Colab, with provisioning, plus an optional local web UI to run
them from your phone): `tools/launchers/kaggle-launch.sh`, `tools/launchers/colab-launch.sh`.
See [docs/launchers.md](docs/launchers.md).

## Face library: grouping by similarity

The library groups faces two ways. `POST /api/faces/auto-group` with no arguments
(the default, `mode=name`) groups by a named person detected in the uploaded
filename. `mode=similarity` groups by *identity*: two photos of one person land
together even when the files were named differently.

```bash
# 1. propose — read-only, writes nothing
curl -s -H "X-VisoSwap-Key: $KEY" \
  -X POST 'http://localhost:8000/api/faces/auto-group?mode=similarity&threshold=0.40'

# 2. apply only what you accepted
curl -s -H "X-VisoSwap-Key: $KEY" -H 'content-type: application/json' \
  -X POST http://localhost:8000/api/faces/auto-group/apply \
  -d '{"assignments":[{"group":"Alice","face_ids":["<id>","<id>"]}]}'

# find more of one person
curl -s -H "X-VisoSwap-Key: $KEY" \
  'http://localhost:8000/api/faces/<id>/similar?limit=8'
```

The proposal returns `clusters` (`suggested_group`, `face_ids`, `conflicts`),
`skipped_no_face` and `computed`. It is suggest-then-confirm: nothing is written
until `apply` runs. A member already in a different group is listed under
`conflicts` and is only moved when the caller sends `overwrite: true`.
`threshold` defaults to 0.40 and is clamped to 0.20–0.80.

Embedding a library on a CPU takes seconds to tens of seconds, so past 25 faces
with no cached embedding the endpoint answers **202** with a `job` id instead of
blocking; poll `GET /api/faces/group/{job_id}` (or listen for
`faces_embedding_progress` on the WebSocket) and the clusters come back when
`state` is `done`.

Clustering is complete-linkage: a group only forms when *every* pair inside it
clears the threshold, so A similar to B and B similar to C does not merge A with
C. False merges (two people, one label) are worse than missed ones, which is why
the threshold is the strict knob and the review step exists.

### Biometric data

A face embedding is biometric data. Each one is a 512-float vector stored as a
sidecar next to its image (`<face_id>.emb.npy`) inside the face store, keyed by
the image's content digest. It is never returned by the API — responses carry
similarity *scores*, never vectors — and never written to logs. Deleting a face
deletes its embedding and the "no face found" marker with it, so the store cannot
accumulate fingerprints of images you removed. Nothing is computed until you ask
for a grouping or a similar-faces lookup.

The models are `det_10g.onnx` (SCRFD) and `w600k_r50.onnx` (ArcFace) from
`MODELS_DIR`, run through onnxruntime on the **CPU only** — no GPU memory, no
dependency on the swap engine, so it works on a Mac, on Kaggle and on Colab. If
those files are missing the endpoints fail with a clear message rather than
crashing.

The 0.40 default is not yet validated against a labelled set of real faces; treat
the proposal as a suggestion to review, not as a decision. Choosing the threshold
properly needs precision/recall measured on real data (see
`docs/face-similarity-grouping-plan.md`, work split item 4).

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
