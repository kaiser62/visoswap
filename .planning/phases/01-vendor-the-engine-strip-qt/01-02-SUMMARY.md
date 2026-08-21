---
phase: 01-vendor-the-engine-strip-qt
plan: 02
subsystem: engine
tags: [vendoring, qt-strip, import-rewrite, static-analysis]
status: complete

requires:
  - 01-01 (attribution header, Qt-reachability probe, conftest harness)
provides:
  - visoswap/processors/ populated with 7 inference modules + 3 utils
  - visoswap/processors/external/ (CLIPseg + cliplib + BPE vocab asset)
  - visoswap/models/ (models_data, downloader, integrity_checker)
  - visoswap/processors/utils/misc.py (the 6-symbol subset of miscellaneous.py)
  - PENDING_QT_STRIP as the single shared exclusion set for both gates
  - tests/test_no_qt_source.py (tokenize-based static Qt scan)
affects:
  - plan 01-03 lands visoswap.processors.models_processor, already the target of rewritten TYPE_CHECKING imports
  - plan 01-04 lands frame_worker and empties PENDING_QT_STRIP

tech-stack:
  added: []
  patterns:
    - "byte-level vendoring: bytes in, bytes out, so CRLF survives and the copy diffs clean against upstream"
    - "gate coverage by tree walk plus an explicit exclusion set, never a hardcoded module list"
    - "tokenize COMMENT-token stripping rather than a line filter, so trailing removal notes are exact-safe"
    - "one exclusion set read by both the import gate and the static scan, so they cannot diverge"

key-files:
  created:
    - visoswap/processors/face_detectors.py
    - visoswap/processors/face_editors.py
    - visoswap/processors/face_landmark_detectors.py
    - visoswap/processors/face_masks.py
    - visoswap/processors/face_restorers.py
    - visoswap/processors/face_swappers.py
    - visoswap/processors/frame_enhancers.py
    - visoswap/processors/utils/dfm_model.py
    - visoswap/processors/utils/engine_builder.py
    - visoswap/processors/utils/tensorrt_predictor.py
    - visoswap/processors/utils/misc.py
    - visoswap/processors/external/__init__.py
    - visoswap/processors/external/clipseg.py
    - visoswap/processors/external/resnet.py
    - visoswap/processors/external/cliplib/__init__.py
    - visoswap/processors/external/cliplib/clip.py
    - visoswap/processors/external/cliplib/model.py
    - visoswap/processors/external/cliplib/simple_tokenizer.py
    - visoswap/processors/external/cliplib/bpe_simple_vocab_16e6.txt.gz
    - visoswap/models/__init__.py
    - visoswap/models/models_data.py
    - visoswap/models/downloader.py
    - visoswap/models/integrity_checker.py
    - tests/test_no_qt_source.py
  modified:
    - pyproject.toml
    - tests/conftest.py
    - tests/test_qt_free.py

decisions:
  - "Vendor at the byte level, not the text level -- read bytes, replace byte substrings, write bytes -- so CRLF endings are never touched and the result is provably `upstream + rewrite map` and nothing else."
  - "Verify the vendoring mechanically rather than by eye: reconstruct each file as upstream-bytes-with-rewrites-applied and assert byte equality against what landed. All ten Task 1 files matched exactly."
  - "Put PENDING_QT_STRIP, module discovery and source discovery in conftest.py so the import gate and the static scan read one set. Two copies of an exclusion list is how the two gates silently diverge."
  - "Strip only COMMENT tokens, not STRING tokens. A Qt name in a string is reachable via importlib, so it stays a hit."
  - "Case-insensitive Qt matching, verified safe first: the whole vendored corpus was measured to have zero case-insensitive hits for signal/slot/qt* before the pattern was written."
  - "misc.py assembled from byte-exact extracts of upstream definitions rather than retyped, and the extraction asserted byte-identical against source."

metrics:
  duration: ~30 min
  completed: 2026-08-21
  tasks: 3
  commits: 3
  files: 27

actuals:
  tokens: 75317
  tasks: 3
  commits: 3
---

# Phase 01 Plan 02: Vendor the Qt-Free Processor Tree Summary

Twenty vendored modules, 6,632 lines, now import on an interpreter where PySide6 is
installed and every Qt binding root is blocked -- and a second, import-free static scan
reads all 26 vendored source files for Qt references the import gate structurally cannot see.

## What Was Built

**Task 1 -- the seven inference modules and three utils.** `face_detectors.py`,
`face_editors.py`, `face_landmark_detectors.py`, `face_masks.py`, `face_restorers.py`,
`face_swappers.py`, `frame_enhancers.py`, plus `utils/{dfm_model,engine_builder,
tensorrt_predictor}.py`. Copied at the byte level, attribution header prepended, the
seven-entry rewrite map applied. `engine_builder.py` and `tensorrt_predictor.py` needed no
rewrite at all -- they import nothing from `app`.

**Task 2 -- `external/`, `visoswap/models/`, and the misc subset.** The CLIPseg stack
(`clipseg.py`, `resnet.py`, `cliplib/{__init__,clip,model,simple_tokenizer}.py`) with the
1.3 MB `bpe_simple_vocab_16e6.txt.gz` copied as binary and `cmp`-verified byte-identical.
`visoswap/models/` holds `models_data.py` (verbatim, `./model_assets` intact),
`downloader.py` and `integrity_checker.py`. `pyproject.toml` gained a
`[tool.setuptools.package-data]` entry so a wheel build cannot silently drop the vocab.

`visoswap/processors/utils/misc.py` carries the three definitions the tree actually imports,
extracted byte-exact from `app/helpers/miscellaneous.py` and asserted identical to source.
The remaining ~80% of that module -- directory walking, extension sniffing, the ffmpeg-on-PATH
check, a benchmark decorator -- is not vendored, and with it `cv2`, `shutil`, `datetime` and
`threading` stay out of the engine's import graph.

**Task 3 -- the gate, widened.** `tests/conftest.py` now owns `PENDING_QT_STRIP`,
`module_name_for()`, `vendored_sources()` and `vendored_modules()`. `test_qt_free.py` hands
all 20 discovered modules to the probe in one subprocess run. `tests/test_no_qt_source.py`
scans all 26 source files, blanking COMMENT tokens via `tokenize` before matching.

## Coverage the Gates Now Have

| Gate | Covers | Excluded |
|------|--------|----------|
| import (`test_qt_free.py`) | 20 modules, one probe run | the 2 in `PENDING_QT_STRIP` |
| static (`test_no_qt_source.py`) | 26 `.py` files incl. `__init__.py` | the 2 in `PENDING_QT_STRIP` |

Both read the same set. Plan 01-04 deletes two lines from `conftest.py` and both go green
together, which was the point of putting the set there rather than in either test.

## Verification Results (verbatim)

Task 1:

```
$ python - <<'PY'  # no vendored file resolves an import through `app`
no app-package imports
VERIFY-1a exit=0

$ test $(ls visoswap/processors/*.py visoswap/processors/utils/*.py | wc -l) -ge 12
VERIFY-1b exit=0 count=13
```

Mechanical-fidelity check (added, see deviation 1) -- vendored body vs. upstream bytes with
only the rewrite map applied:

```
OK  processors/face_detectors.py
OK  processors/face_editors.py
OK  processors/face_landmark_detectors.py
OK  processors/face_masks.py
OK  processors/face_restorers.py
OK  processors/face_swappers.py
OK  processors/frame_enhancers.py
OK  processors/utils/dfm_model.py
OK  processors/utils/engine_builder.py
OK  processors/utils/tensorrt_predictor.py
ALL MECHANICAL: True
```

Task 2:

```
$ test -s .../bpe_simple_vocab_16e6.txt.gz && cmp -s .../bpe... "D:/Visomaster/.../bpe..."
VERIFY-2a exit=0

$ "D:/Visomaster/dependencies/Python/python.exe" tests/_qt_guard_probe.py \
    visoswap.processors.external.clipseg visoswap.models.models_data \
    visoswap.models.downloader visoswap.processors.utils.misc
CLEAN
probe exit=0

$ "D:/Visomaster/dependencies/Python/python.exe" -c "... from visoswap.processors.utils.misc import t512,t384,t256,t128,is_file_exists; print('ok')"
ok
VERIFY-2c exit=0
```

`misc.py` public surface:

```
public names: ['Path', 'get_scaling_transforms', 'is_file_exists', 't128', 't256', 't384', 't512', 'v2']
defined here (not imported): ['get_scaling_transforms', 'is_file_exists', 't128', 't256', 't384', 't512']
```

Task 3:

```
$ python -m pytest tests/ -x -q
.........                                                                [100%]
9 passed in 7.60s

$ python -m pytest tests/test_no_qt_source.py -q
...                                                                      [100%]
3 passed in 0.24s
```

Discovered coverage:

```
modules probed: 20
sources scanned: 26
```

Plan-level:

```
$ python -m pytest tests/ -q
.........                                                                [100%]
9 passed in 8.48s

$ <app-import scan over visoswap/**/*.py>
no app-package imports
app-scan exit=0

$ cmp visoswap/processors/external/cliplib/bpe_simple_vocab_16e6.txt.gz "D:/Visomaster/.../bpe_simple_vocab_16e6.txt.gz"
identical

$ find "D:/Visomaster/app" -type f -name '*.py' -newermt '-1 day'
(no output)

$ find "D:/Visomaster/app" -newermt '-1 day'          # widened: any file, not just .py
(no output)

$ cd C:/Users/Sakat/.devswarm/repos/1/ef7f9c3d/config-parallel-setup && git status --short
(no output)
```

Fidelity spot-check on `face_masks.py`, the file the plan names -- the entire diff:

```
--- D:/Visomaster/app/processors/face_masks.py	2025-02-05 15:25:14
+++ visoswap/processors/face_masks.py	2026-08-21 10:08:30
@@ -1,3 +1,8 @@
+# Vendored from VisoMaster (https://github.com/visomaster/VisoMaster).
+# VisoMaster is licensed GPLv3; this vendored copy inherits that license.
+# This file was vendored into VisoSwap and may have been modified from upstream.
+# See NOTICE for vendoring provenance and LICENSE for the full GPLv3 text.
+
 from typing import TYPE_CHECKING
 ...
-from app.processors.external.clipseg import CLIPDensePredT
-from app.processors.models_data import models_dir
+from visoswap.processors.external.clipseg import CLIPDensePredT
+from visoswap.models.models_data import models_dir
 if TYPE_CHECKING:
-    from app.processors.models_processor import ModelsProcessor
+    from visoswap.processors.models_processor import ModelsProcessor
```

Header plus three import lines. Nothing else.

Negative test -- the static scan proven capable of failing, and of ignoring comments, against
a real vendored file rather than only a fixture:

```
=== injected code reference: exit 1 ===
E           visoswap\processors\utils\misc.py:43: 'PySide6' in: from PySide6.QtCore import Signal
E           visoswap\processors\utils\misc.py:43: 'QtCore' in: from PySide6.QtCore import Signal
E           visoswap\processors\utils\misc.py:43: 'Signal' in: from PySide6.QtCore import Signal
1 failed in 0.15s
=== comment-only reference: exit 0 ===
1 passed in 0.08s
restored byte-identical: True
```

## What This Does NOT Prove

The DFM path (`utils/dfm_model.py`), the LivePortrait path (`face_editors.py`) and the
CLIPseg masking path (`external/clipseg.py` + `external/cliplib/`) are **import-proven only**.

Nothing in Phase 1 executes them. No model weights are loaded, no frame is processed, no
`.dfm` file is opened, no CLIPseg forward pass runs. `import visoswap.processors.face_editors`
succeeding says the module's top-level statements execute and reach no Qt binding. It says
nothing about whether LivePortrait produces a correct frame, whether the DFM loader finds its
ONNX, or whether CLIPseg's tokenizer resolves its vocabulary at runtime.

Coupling that only appears when these paths are exercised -- a missing attribute, an asset
resolved from a path that no longer exists, an upstream helper never imported at module scope
-- will surface in **Phase 2**, whose success criteria exercise all three explicitly. Phase 1
clears imports. That is the whole claim.

The BPE vocab is one concrete instance of this. It is byte-identical and adjacent to
`simple_tokenizer.py`, which is what `__file__`-relative resolution needs, but nothing in
Phase 1 calls `SimpleTokenizer()`. The placement is verified; the resolution is not.

## Deviations from Plan

### Auto-added functionality

**1. [Rule 2 - Missing critical functionality] Byte-exact mechanical-fidelity assertion**
- **Found during:** Task 1
- **Issue:** The plan's `<verify>` for Task 1 checks that no `app` import survives and that
  at least 12 files exist. Neither detects the failure the task's `<action>` is most worried
  about -- a copy that got reformatted, reordered, or "improved" in passing. The plan puts
  that check in `<verification>` as a one-file eyeball diff of `face_masks.py`.
- **Why critical:** the property being protected is that every vendored file diffs cleanly
  against upstream forever. Checking one of ten files by eye establishes it for one of ten.
- **Fix:** reconstructed each file as `upstream_bytes` with only the rewrite map applied and
  asserted byte equality against what landed. All ten matched. An earlier line-oriented
  `difflib` pass reported two spurious empty-line diffs in `face_detectors.py`; the byte
  comparison showed those were a difflib hunk-grouping artifact, not a real difference --
  which is itself the argument for comparing bytes rather than diff output.
- **Files modified:** none (verification only, run at execution time)
- **Commit:** 1debcb1

**2. [Rule 2 - Missing critical functionality] `test_scanner_is_not_inert`**
- **Found during:** Task 3
- **Issue:** the plan specifies one test in `test_no_qt_source.py`. A broken regex or an
  over-eager comment stripper would make it pass over Qt-riddled code, permanently and
  silently -- the same false-green class plan 01-01 added `test_probe_blocker_is_armed` for.
- **Fix:** a fixture-based test asserting that a real `from PySide6.QtCore import Signal` is
  flagged, that a *trailing* comment carrying the same three names is not, and that a
  whole-line comment mentioning `QPixmap` is not. This is the exact behaviour plans 01-03 and
  01-04 will depend on when they annotate what they removed.
- **Files modified:** `tests/test_no_qt_source.py`
- **Commit:** cbec6bf

**3. [Rule 2 - Missing critical functionality] Non-vacuity guards on both gates**
- **Found during:** Task 3
- **Issue:** discovery replaced a hardcoded list. If discovery returns nothing -- wrong root,
  a rename -- `run_probe(python, [])` exits 0 and the scan loop finds no hits. Both gates go
  green having examined zero files.
- **Fix:** `test_gate_discovers_the_vendored_tree` and `test_scan_covers_the_vendored_tree`
  assert the discovered list is non-empty and that no `PENDING_QT_STRIP` entry leaked into it.
- **Files modified:** `tests/test_qt_free.py`, `tests/test_no_qt_source.py`
- **Commit:** cbec6bf

### Design choices made within the plan's latitude

**4. `PENDING_QT_STRIP` and the discovery helpers live in `conftest.py`, not in either test.**
The plan says "both tests share one exclusion set" without saying where it lives. Defining it
in `test_qt_free.py` and importing it into `test_no_qt_source.py` would make the static scan --
which is deliberately dependency-free -- import a module that imports the probe harness.
`conftest.py` is already the shared-harness home and `tests/` is already a package.

**5. Case-insensitive matching was measured before it was written, not assumed.** Matching
`Signal` and `Slot` case-insensitively is the risky half of the plan's pattern -- `scipy.signal`,
a variable named `slot`, and the gate is unusable. The whole vendored corpus plus `helpers/`
was grepped for all fifteen tokens case-insensitively before the pattern was written: zero
hits. The looser match therefore costs nothing today and catches a stray `pyside6` in a note
tomorrow. Had there been a single hit, `Signal`/`Slot` would have gone case-sensitive.

**6. `__init__.py` files are scanned but not probed.** `vendored_sources()` includes them --
a Qt import smuggled into a package `__init__` is the most damaging place to miss one --
while `vendored_modules()` drops them, since importing a package is implied by importing
anything inside it.

**7. The `models_dir` comment is inline, not on its own line.** The plan says "leave a short
comment on that line". Inline keeps `models_data.py` at exactly 448 lines and keeps the diff
against upstream to a single line rather than an insertion. It is also, incidentally, the
first real trailing comment in the vendored tree and therefore the first live exercise of
Task 3's `tokenize` stripping.

## Deferred Deliberately (not oversights)

| Item | Location | Deferred to |
|------|----------|-------------|
| `models_dir = './model_assets'` hardcoded | `visoswap/models/models_data.py:6` | Phase 4 model bootstrap makes it env-driven via `MODELS_DIR`. Comment in place pointing there. |
| `torch.load` without `weights_only=True` | `visoswap/processors/external/clipseg.py:305` (upstream `:300`; the 5-line header shifts every vendored line number by +5) | Phase 2, per threat register T-01-06. Not invoked in Phase 1. |

Both are recorded decisions inherited from the plan's `<threat_model>`, left verbatim so the
vendored copy keeps diffing clean against upstream.

## Threat Register Outcomes

| Threat ID | Disposition | Outcome |
|-----------|-------------|---------|
| T-01-05 | mitigate | `downloader.py` vendored unmodified; its SHA-256 check against `models_data.py` and re-download-on-mismatch survive intact. |
| T-01-06 | accept | `torch.load` left as upstream wrote it. Carried forward to Phase 2, recorded above. |
| T-01-07 | mitigate | Gate discovers modules by walking `visoswap/`; the only exclusions are two commented lines in `PENDING_QT_STRIP`. Non-vacuity guards added on top. |
| T-01-08 | accept | No package-manager install was performed. No new third-party package introduced. |

No new threat surface. `downloader.py`'s network reach and `clipseg.py`'s `torch.load` were
both already in the register; nothing else vendored here opens a socket, a file from user
input, or a deserializer.

## What Did Not Survive Contact

Three things, all minor -- the plan's measured facts held.

- **Line counts are all off by one, consistently.** The plan says `face_detectors.py` is 1064
  lines; `grep -c ""` says 1065. Same +1 on every file. The plan counted content lines and the
  files end with a trailing newline. No action; noting it so plan 01-03's counts
  (`models_processor.py` 462, `frame_worker.py` 249) are read the same way.

- **`app/processors/external/` has no `__init__.py` upstream.** It is a namespace package
  there. The plan lists `visoswap/processors/external/__init__.py` among the files to
  "copy... applying the header", but there is nothing to copy. Written as project-authored,
  import-free scaffolding instead -- which is what the plan's later paragraph actually asks
  for ("`visoswap/models/__init__.py` and `visoswap/processors/external/__init__.py` stay
  import-free"). `cliplib/__init__.py` *does* exist upstream (`from .clip import *`), was
  vendored with the header, and is correctly treated as vendored code by the header gate.

- **Success criterion 3's "and nothing else" is true of definitions, not of the namespace.**
  `misc.py` defines exactly the six required names, but `Path` and `v2` are also module
  attributes because the copied definitions need them. Removing them is impossible without
  rewriting the bodies, which is the one thing the task forbids. Recorded rather than
  papered over.

## Known Stubs

None. No placeholder values, no TODO/FIXME markers, no unwired components. The `TYPE_CHECKING`
imports of `visoswap.processors.models_processor` point at a module plan 01-03 lands; they are
not stubs -- they never execute at runtime and were rewritten deliberately per the plan's
import map.

## Self-Check: PASSED

All 24 created files verified present on disk; all 3 modified files verified changed. All
three commits (`1debcb1`, `2dcb990`, `cbec6bf`) verified in `git log`. Working tree clean
apart from untracked `__pycache__`, which `.gitignore` covers. `D:/Visomaster/app` and the
`config-parallel-setup` worktree both verified unmodified.
