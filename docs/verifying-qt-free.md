# Verifying that the engine is Qt-free

VisoSwap vendors VisoMaster's inference engine and removes its Qt coupling. That
claim is checked two different ways, and the difference between them is the whole
point of this document.

| Check | What it proves | When it runs |
|---|---|---|
| **Blocked-import gate** | The vendored tree cannot reach Qt *even on an interpreter where Qt is installed*. | Every `pytest` run. Automatic. |
| **Clean-room import** | The vendored tree imports on a machine where Qt is *genuinely absent*. | By hand, at a phase boundary. This document. |

The first is the stronger proof of *unreachability*: refusing `PySide6` on an
interpreter that has `PySide6` sitting right there means the block did real work.
`tests/_qt_guard_probe.py` installs a `sys.meta_path` finder that denies all seven
Qt binding roots -- `PySide6`, `PySide2`, `PyQt5`, `PyQt6`, `qtpy`, `shiboken6`,
`shiboken2` -- imports every discovered module, and then checks that none of those
roots leaked into `sys.modules` anyway.

The second covers a failure mode the first structurally cannot see. A stale
`.pth` file, a namespace package, or a conditional import guarded on availability
(`try: import PySide6 / except ImportError: pass`) can pass a blocked-import gate
and still misbehave -- or the code can turn out to depend on Qt being *installed*
in a way that blocking, rather than removing, papers over. Only an environment
with no Qt in it answers that.

It is not a test because building the environment pulls multi-gigabyte
CUDA-pinned `torch`, `torchvision` and `tensorrt` wheels. No test suite should do
that on every run.

## Running the clean-room check

### 1. A Python 3.10 interpreter

The vendored code targets 3.10 -- that is what VisoMaster's own portable
interpreter is (3.10.13), and 3.13 removed `pkg_resources`, which parts of this
dependency set still reach for.

```
py -3.10 -m venv D:/Dev/visoswap/.venv-clean
```

If `py -3.10` reports *"No suitable Python runtime found"*, the machine has no
3.10 installed. Fetch one user-scoped rather than installing a second system
Python:

```
uv python install 3.10
uv python find 3.10          # prints the interpreter path
"<that path>" -m venv D:/Dev/visoswap/.venv-clean
```

`.venv-clean/` is gitignored. Delete it when you are done; it is ~6 GB.

### 2. Review the resolution before installing anything

This is the project's only package-manager install, so it is the one place the
Package Legitimacy Gate applies (threat **T-01-16**). Look at the resolved list
*before* it downloads:

```
.venv-clean/Scripts/python -m pip install --dry-run -r requirements-engine.txt
```

`requirements-engine.txt` introduces no package that VisoMaster did not already
pin in `requirements_cu124.txt`; it is that file with the four Qt/theming entries
and `pyvirtualcam` removed. Everything else in the resolved list should be a
recognisable transitive dependency of those pins. Anything you do not recognise
is a finding -- stop and report it rather than installing.

### 3. Install

```
.venv-clean/Scripts/python -m pip install -r requirements-engine.txt
```

Expect a long download. If a CUDA-pinned wheel is unavailable for your setup,
note which one and use the CPU wheels instead: this check is about import
reachability, not inference. Nothing here runs a model.

### 4. Confirm Qt is genuinely absent

This is the step that makes the exercise worth doing. Skipping it turns the
clean-room check into a second, weaker copy of the automatic gate.

```
.venv-clean/Scripts/python -m pip show PySide6
```

Expected: `WARNING: Package(s) not found: PySide6`.

Worth checking the other six roots too, since the automatic gate blocks all seven
and only this step can show they are absent rather than merely blocked:

```
.venv-clean/Scripts/python -m pip show PySide6 PySide2 PyQt5 PyQt6 qtpy shiboken6 shiboken2
```

### 5. Import the tree with that interpreter

Point the suite's engine interpreter at the clean venv and run everything:

```
cd D:/Dev/visoswap
VISOSWAP_ENGINE_PYTHON=D:/Dev/visoswap/.venv-clean/Scripts/python.exe \
  .venv-clean/Scripts/python -m pytest tests/ -q
```

Expected: a green suite. That run includes
`test_every_vendored_module_imports_without_qt`, which hands the probe every
package *and* every module under `visoswap/` in one subprocess and requires
`CLEAN`.

To see the probe's own output rather than pytest's:

```
.venv-clean/Scripts/python -c "import sys; sys.path.insert(0,'.'); from tests.conftest import vendored_modules; print('\n'.join(vendored_modules()))" > modules.txt
.venv-clean/Scripts/python tests/_qt_guard_probe.py $(cat modules.txt)
```

Expected: `CLEAN`, exit 0.

One test will behave differently and that is correct:
`test_probe_blocker_is_armed` hands the probe `PySide6.QtCore` directly and
requires it to be refused. In a venv with no Qt at all it is refused by the
blocker before the absence is ever discovered -- the finder raises `QtBlocked`
from `find_spec`, ahead of the normal `ModuleNotFoundError`. So it still passes,
and it still means what it says.

## What a failure here means

A failure is a real finding, not a formality. It means something reaches Qt in a
way the blocked-import model did not capture. Report the first module that failed
together with its traceback; do not work around it by installing Qt into the
clean venv, which destroys the only property the environment has.

## What this check does *not* prove

That the engine **runs**. Phase 1 proves import-cleanliness and nothing more. No
model has been loaded, no frame swapped, and the DFM, LivePortrait and CLIPseg
paths have never been executed -- they are import-proven only. Phase 2 exercises
them, and that is where the remaining coupling risk is retired.
