"""Inference processors vendored from VisoMaster's ``app/processors``.

Intentionally empty of imports. Plan 01-04 decides what this package
re-exports, once ``models_processor`` and ``frame_worker`` are de-Qt'd. Adding
an eager import before then would pull the still-Qt-coupled modules into the
Qt-reachability gate and make it fail for a reason the gate is not yet
supposed to be testing.
"""
