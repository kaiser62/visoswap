# Model bootstrap

The backend refuses to start on incomplete models. This page is the written
record of *why* the refusal works the way it does, so that the decisions here
are not later "simplified" away. It corresponds to plan 04-03 and closes roadmap
criteria 2 and 3 of Phase 4.

## What the refusal replaces

Before this plan, a missing model surfaced as a `FileNotFoundError` several
layers inside an ONNX session load, on the first frame a user asked for. That is
the *mid-inference* failure `BACKEND-01` exists to replace. The gate instead
raises from the app's lifespan, so the process never reaches a serving state and
no route can answer — not merely that the generation route checks first. A check
inside `api/generation.py` would satisfy criterion 2's wording and miss its
point: the failure would arrive as a 500 on the first frame, which is the exact
failure mode being removed. Do not move the gate into a route "later".

## The manifest

`visoswap/models/manifest.py` is the only reader of the two vendored model lists
in `visoswap/models/models_data.py`:

- `models_list` — **56** entries, each with `model_name`, `local_path`, `hash`
  and `url`. These are downloadable ONNX assets.
- `models_trt_list` — **6** entries when `import tensorrt` succeeds in the
  process, **0** otherwise. Each has `model_name`, `local_path`, `hash` and
  **no `url`**; their `local_path` interpolates `trt.__version__` into the
  filename.

The tracked set is **derived at runtime** and moves with the interpreter's
TensorRT availability: 62 on the interpreter that runs the backend (TensorRT
10.6.0 installed), 56 on one where it is not. No count appears as a literal
anywhere in the manifest module or its tests; a test AST-checks the module and
forbids the literals 56, 6, 62 and 12. This is what makes the roadmap's "derived
from the manifest at runtime, never a hardcoded count" a property of the code.

## The required/optional rule

An entry is **required** exactly when it is **fetchable** *and* **consumable**:

- *fetchable* — the manifest gave it a `url` (something could supply it).
- *consumable* — the execution provider that would load it is one
  `visoswap/engine.py` accepts (`CUDA`, `CPU`).

The six TensorRT entries fail both halves:

1. They cannot be supplied: no `url`, and `visoswap/models/downloader.py`
   requires one.
2. Nothing can load them: `visoswap/engine.py`'s constructor refuses the
   `TensorRT` / `TensorRT-Engine` providers outright with a `ValueError` because
   their provider options write an engine and timing cache to a *relative*
   `tensorrt-engines/` path. The provider that reads a `.trt` file cannot be
   selected at all.

A missing `.trt` therefore cannot produce the mid-inference failure the gate
exists to prevent — which is the test of whether something belongs in the
required set. If a later phase re-enables TensorRT (making the cache path
absolute first), flipping the accepted-provider set flips those six back into
required automatically; the rule is a predicate, not a list of six names, and a
test pins that the currently-optional set equals exactly the entries lacking a
URL.

## Three verification states, not two

`visoswap/models/bootstrap.py` classifies every required entry into:

- **present-and-matching** — exists, non-empty, hashes to the manifest digest
  (full mode),
- **present-and-mismatching** — exists but the bytes are wrong,
- **absent** — missing, or **zero bytes**.

Mismatching is *not* collapsed into absent. A truncated download and a file
somebody replaced look identical to a presence check and to the vendored
downloader (which deletes and re-fetches anything that fails its check); they
should not look identical in the report a user reads. Zero bytes counts as
absent rather than present, because an empty file satisfies `isfile` and answers
every reader with nothing.

## Two modes, and the measured cost of the full one

- **fast** — presence and non-emptiness only.
- **full** — presence, non-emptiness, and SHA-256 against the manifest digest.

The result names which mode produced it, so a fast pass can never be mistaken
for a hash-verified one. A full pass over the real 12.23 GB tree costs roughly
**6.8 s warm** (measured 1,786 MB/s on `inswapper_128.fp16.onnx`, 277.7 MB in
0.16 s); a cold read is substantially slower. That is affordable on a first run
and not on every restart.

The startup default is **auto**: fast on every start, full when no verification
marker for the current manifest exists. The marker lives beside the project's
own data (`data/.model-verify-<fingerprint>.marker`), never inside the models
directory — writing into a directory that may be a junction into a read-only
tree is the mistake this gate is most exposed to. The fingerprint is derived
from the sorted `(name, digest)` pairs of the required set, so an upstream
change in a required file's digest invalidates the marker.

`MODELS_VERIFY_MODE` selects `auto` (default), `fast` (the developer-loop escape
hatch) or `full` (a hard proof). There is deliberately **no off switch**: an off
switch on a refusal-to-start gate is the first thing an environment file sets
and then forgets, and `BACKEND-01` would quietly stop being true.

## The vendored downloader's two behaviours

Repair wraps `visoswap/models/downloader.download_file` unchanged (it is vendored
under GPLv3; it diverges only at a named edit site with a provenance comment).
Two measured behaviours are worth knowing:

1. It **removes a file whose hash does not match** and re-downloads it, and it
   retries three times.
2. It writes **directly to the final path with no temporary name**, so an
   interrupted download leaves a truncated file where the real one belongs — which
   the next run detects by hash and removes, but which is present-and-wrong in the
   interim.

This is the same class of finding plan 02-04 recorded about the vendored CLIP
downloader (`clip.py:75-76`).

## The refusal to write into the read-only tree

Repair refuses any destination whose resolved real path lies **inside the
read-only weight tree** (`D:/Visomaster`, which the repo's `model_assets` junction
points at) or **outside the configured models directory** (a link escape), before
opening a file for writing. Overwriting the borrowed 12 GB weight tree is the one
unrecoverable mistake this phase can make, and plan 02-01 treated the identical
hazard as its first threat. Verification opens nothing for writing at all.

## A spec correction

The design spec's "Models" section reads "URLs and hashes for all **54** files
(12 GB)". The 12 GB is right (12.23 GB measured); the **54 is wrong** — the
manifest holds **56**. The manifest is the authority; the discrepancy is recorded
rather than inherited.
