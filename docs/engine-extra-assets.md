# Engine extra assets — an absence record

Every asset the engine would need **beyond the 56-entry model manifest**, and
which of them exist. The answer, for all of them, is: none of them exist.

This document is a record of **absence and re-enablement**, nothing else. Plan
02-04 fetched nothing, downloaded nothing, converted nothing and ran no
inference. Consequently:

> **There is no measured digest anywhere in this file.** Nothing was acquired, so
> there is nothing to hash. Where a digest appears below it is a digest *read out
> of vendored source code* — a value the code will check against — and it is
> labelled as such. A placeholder digest presented as measured would be a lie,
> and would be trusted later by someone who had no way to know.

The companion document is [`engine-path-coverage.md`](engine-path-coverage.md),
which records which engine paths have been *run*. This file records what would
have to exist before the two unrun ones could be.

The governing decision is
[`02-DECISION-deferred-paths.md`](../.planning/phases/02-engine-api-first-swap/02-DECISION-deferred-paths.md),
taken by the project owner on 2026-08-21.

---

## A finding about upstream, before the list

`rd64-uni-refined.pth` is **not in VisoMaster's own model manifest**.
`visoswap/models/models_data.py` carries 56 `models_list` entries and 6
`models_trt_list` entries, and the string `.pth` appears **zero times** in the
whole file. The manifest is what the upstream downloader populates.

So the text-masking control is **non-functional on a stock VisoMaster install**,
not merely on this one. Nothing in the vendoring broke it; it was never wired to
an acquirable weight file in the first place. That is worth stating plainly,
because it is the answer to "how did nobody hit this before" — the feature ships
with a control that cannot work unless the user sources a weight file that the
project never tells them about.

---

## The assets

### 1. `rd64-uni-refined.pth` — CLIPseg dense-prediction head

| | |
|---|---|
| **Read by** | `visoswap/processors/face_masks.py:256` — `torch.load(f'{models_dir}/rd64-uni-refined.pth', weights_only=True)` |
| **Resolved path** | `models_dir` is `'./model_assets'` (`visoswap/models/models_data.py:6`), CWD-relative |
| **Present?** | **No.** Not under `model_assets/`, not anywhere on this machine outside `site-packages`. |
| **In the manifest?** | **No.** See the finding above. |
| **Needed for** | The `ClipEnableToggle` text-driven mask, `frame_worker.py:654–655` |
| **Digest** | **None recorded — nothing was downloaded.** There is no authoritative published digest in this repository to record either; the manifest does not carry the file. |

The load call already passes `weights_only=True` (plan 02-03, threat T-02-12), so
a crafted checkpoint at this site cannot execute code at load time. That
narrowing is in place *before* the file can ever exist, which is the right order.

### 2. Any `.dfm` file — DeepFaceLive swap models

| | |
|---|---|
| **Read by** | `visoswap/processors/utils/dfm_model.py:23` — `onnxruntime.InferenceSession(str(model_path), providers=...)` |
| **Expected location** | `model_assets/dfm_models/` |
| **Present?** | **No.** That directory contains exactly one entry, `.gitkeep` (0 bytes). No `.dfm` file exists anywhere on this machine. |
| **Also missing** | A populator for `EngineContext.dfm_models_data`. The context carries the mapping; nothing fills it. `models_processor.load_dfm_model` indexes it by the selected model name and raises on a miss. |
| **Reached by** | The swapper-model key `DeepFaceLive (DFM)`. The fixture's `SwapModelSelection` is `Inswapper128` and `DFMModelSelection` is `""`, so nothing in Phase 2 selects it. |
| **Digest** | **None recorded — nothing was downloaded.** A `.dfm` file is user-sourced by nature; there is no canonical set to hash. |

**The threat class here is different from the other two.** A `.dfm` file is an
ONNX model handed straight to an inference session — it is not unpickled. So the
exposure is a **malicious ONNX graph parsed by the runtime**, not arbitrary code
execution at load time. `weights_only=` does not apply and would not help.
Whoever re-enables this path should not carry over the `torch.load` mental model.

### 3. CLIP `ViT-B/16` — an implicit first-run download, not a file this project stores

This one is not a missing file. It is a **network fetch that the code performs on
your behalf, silently, the first time the text-masking path is constructed** —
and it is the most important entry in this document, for the reason in the next
section.

| | |
|---|---|
| **Triggered by** | `visoswap/processors/external/clipseg.py:91` — `clip.load(version, device='cpu', jit=False)`, inside `CLIPDenseBase.__init__` |
| **Fetched by** | `visoswap/processors/external/cliplib/clip.py:125` — `_download(_MODELS[name], download_root or os.path.expanduser("~/.cache/clip"))` |
| **Source** | `https://openaipublic.azureedge.net/clip/models/5806e77c…f416f/ViT-B-16.pt` (`clip.py:41`) |
| **Lands at** | `~/.cache/clip/ViT-B-16.pt`. On this machine `~/.cache` is itself redirected to `D:/DevCache/cache`, and **no `clip` subdirectory exists** — nothing has ever triggered this. |
| **Size** | Reported as roughly 335 MB. **Not measured here** — measuring it would have required the download this plan refuses to perform. Treat the number as indicative, not verified. |
| **Digest** | The downloader checks SHA-256 `5806e77cd80f8b59890b7e101eabd078d9fb84e6937f9e85e4ecb61988df416f`, taken from the second-to-last URL path segment (`clip.py:53`, `clip.py:75`). **This digest is read out of vendored source, not measured from an artifact** — no artifact exists. |

Two honest qualifications on that integrity check:

- It verifies **after** the full file has been written to disk, and on mismatch it
  raises **without deleting** the bad file (`clip.py:75–76`). The next run
  re-hashes it, warns, and overwrites — so a corrupt file is never *loaded*, but
  it does persist on disk in the interim.
- Verifying a digest embedded in the URL means the URL is the root of trust. It
  detects a corrupted or truncated transfer; it does not defend against an
  attacker who controls the URL.

---

## Why descoping CLIPseg does **not** make the download safe

The obvious reading of the deferral is: *`rd64-uni-refined.pth` is missing, so the
text-masking path dies at the missing file, so the 335 MB download never
happens.* **That reading is wrong, and it is wrong in the dangerous direction.**

Here is the actual call order inside `run_CLIPs`:

```
face_masks.py:248   run_CLIPs(...)
face_masks.py:254     CLIPDensePredT(version='ViT-B/16', ...)     <-- constructor
clipseg.py:85           CLIPDenseBase.__init__
clipseg.py:91             clip.load('ViT-B/16', device='cpu', jit=False)
clip.py:125                 _download(...)  -> ~/.cache/clip      <-- ~335 MB OFF THE NETWORK
clip.py:134                 torch.jit.load(opened_file)           <-- deserializer #1
clip.py:141                 torch.load(opened_file, weights_only=True)   <-- deserializer #2, on RuntimeError
face_masks.py:256     torch.load('.../rd64-uni-refined.pth')      <-- ONLY NOW does the missing file matter
```

The construction on line 254 completes **before** line 256 is evaluated. The
download, and both deserializers that consume it, are strictly **upstream** of
the missing file. `rd64-uni-refined.pth` being absent stops the path *after* the
network fetch and *after* the bytes have been fed to a deserializer.

So the ~335 MB `ViT-B/16` download is **not a size note**. It is **the input to a
deserializer**, and it is the input to a deserializer that a missing local file
cannot protect you from.

**What actually keeps this inert in Phase 2** is a different thing entirely:
`frame_worker.py:654` gates the whole call on `parameters["ClipEnableToggle"]`,
and the engine fixture sets that to `false`. `run_CLIPs` is never entered, so
line 254 never runs. The safety comes from **the toggle**, not from the missing
weight file. Anyone who reasons "the file is gone, therefore the path is safe"
has the causality backwards and will be surprised the moment the toggle is
flipped on a machine with network access.

This is why plan 02-03's hardening of `clip.py:141` mattered. It was found late,
it was not in any earlier inventory, and it was the only one of the three
`torch.load` sites under `visoswap/` whose input arrives **over the network**.
It now passes `weights_only=True`. Note precisely what that buys: `clip.py:134`
attempts `torch.jit.load` first and only a `RuntimeError` falls through to line
141, so for a well-formed official archive line 141 is not reached at all. It is
reached exactly when the downloaded file is *not* a JIT archive — which is the
anomalous case, and therefore the case worth hardening.

---

## Re-enablement

Nothing below has been done. This is the instruction set for whoever does it.

**Do not write through the models link.** `model_assets/` is a junction into
`D:/Visomaster/model_assets`, which is a read-only source tree. `models_dir` is
`'./model_assets'`, so the naive reading of "put the file in the models
directory" means writing into a tree this project must not modify. Phase 4's
bootstrap has to make `models_dir` point at a project-owned, gitignored asset
location first — `model_assets/` is already gitignored (`.gitignore:9`), so an
un-linked real directory is the shape to aim for.

### CLIPseg text masking

1. Point `models_dir` at a project-owned asset directory (Phase 4 work — see
   `models_data.py:6`, which is vendored verbatim and CWD-relative today).
2. Source `rd64-uni-refined.pth`. **A human must nominate the source**; this
   project has no manifest entry, no URL and no published digest for it.
   Measure SHA-256 after download, record it, and delete any partial on mismatch.
3. Allow network egress on first run for the `ViT-B/16` backbone, or pre-seed
   `~/.cache/clip/ViT-B-16.pt` — verifying, before you place it, that its SHA-256
   equals the digest in the URL path at `clip.py:41`.
4. Turn `ClipEnableToggle` on in the project tier and supply `ClipText`.

### DFM

1. Place at least one `.dfm` file in a project-owned `dfm_models/` directory —
   again, not through the junction.
2. **Write the `EngineContext.dfm_models_data` populator.** This is code, not an
   asset. Upstream fills it from a directory-scanning helper in
   `app/helpers/miscellaneous.py` that plan 01-02 deliberately did not vendor,
   because directory discovery is not an inference concern. Plan 02-02 left the
   mapping empty rather than inventing a scan for a directory with nothing in it.
3. Select `DeepFaceLive (DFM)` as the swapper model and set `DFMModelSelection`
   to the model's name. No extra recognition model is needed — the arcface
   mapping routes this swapper to the `Inswapper128ArcFace` recogniser, and
   `frame_worker.py:171` deliberately skips the source embedding for it.
4. Validate the ONNX graph, not the pickle. See the threat note in entry 2.

---

## Forward-carry to Phase 4

**BACKEND-01's model-completeness check will not catch any of this.**

The check refuses to start on an incomplete model set, and it derives that set at
runtime from `models_list` plus `models_trt_list`. All three assets above are
**outside that manifest**:

- `rd64-uni-refined.pth` — absent from the manifest, as established at the top of
  this file.
- `.dfm` files — user-sourced, never in a manifest.
- `ViT-B/16` — not a project file at all; it lives in a per-user cache directory
  the manifest has no concept of.

So the bootstrap will look at a machine with none of these present, find every
manifest entry satisfied, and **start happily with a text-masking control that
cannot work and a DFM swapper that raises on selection**.

Whoever re-enables either path **must extend the bootstrap's checked set in the
same change**. Supplying the asset without extending the check leaves the failure
exactly where it is today — deferred to runtime, surfacing as a `FileNotFoundError`
or a `KeyError` in the middle of a frame, rather than at startup where the
completeness check exists to put it.
