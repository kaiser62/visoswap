"""Inference processors vendored from VisoMaster's ``app/processors``.

Intentionally empty of imports, and plan 01-04 decided it stays that way.

Eagerly importing the submodules here would pull torch, onnxruntime, tensorrt
and skimage into every ``import visoswap.processors``, which is the wrong
default for a library, and it would drag any future mid-strip module into the
Qt-reachability gate before its plan is finished.

The cost of that choice is that ``import visoswap.processors`` -- the roadmap's
literal success criterion -- imports an empty file and proves nothing on its
own. That is threat T-01-13, and it is paid for in the gate rather than here:
``tests/conftest.vendored_modules`` hands the probe this package name **and**
every discovered submodule in one run. Do not "fix" the criterion by adding
imports to this file.

``video_processor.py`` is deliberately absent -- dropped whole, never vendored
and never stubbed. ``tests/test_dropped_modules.py`` is what keeps it that way.
"""
