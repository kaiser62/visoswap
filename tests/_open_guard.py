"""A runtime open guard proving no opened path resolves inside a VisoMaster install.

Plan 04-04 (sever VisoMaster), Task 3. A grep proves no *literal* names a
VisoMaster path; only a guard watching real opens proves no *resolved* path
reaches one -- and on this machine that distinction is not theoretical: the
``model_assets`` junction resolves silently and ``os.path.islink`` reports
``False`` for it, so a static link check proves nothing. The guard wraps the
builtin ``open`` and low-level ``os.open`` and resolves every path they receive.

Non-vacuity has two halves, both mandatory (T-04-24):

* ``opens`` counts how many paths the guard observed; a caller asserts it is
  large, because a swap that loads a 277 MB ONNX model opens a great many files
  and a guard that saw zero opens proves nothing.
* :meth:`VisoMasterOpenGuard.assert_capable_of_firing` opens a file inside a
  planted forbidden root and asserts the guard raises -- without that, a guard
  with a broken comparison is indistinguishable from a clean run.
"""

from __future__ import annotations

import builtins
import os
import tempfile
from pathlib import Path

#: The read-only tree the project severs itself from. Nothing legitimately opens
#: inside it once the project owns its weights (model_assets_owned/).
DEFAULT_FORBIDDEN_ROOTS = (Path("D:/Visomaster"),)


class OpenGuardFired(OSError):
    """Raised when a path opened during a guarded run resolves inside a forbidden root."""


class VisoMasterOpenGuard:
    """Intercepts ``open``/``os.open`` and raises when a path lands in a forbidden root.

    Armed as a context manager; restored on exit. Counts every path observed so
    a caller can assert the guard actually saw real opens rather than none.
    """

    def __init__(self, forbidden_roots=DEFAULT_FORBIDDEN_ROOTS):
        self._forbidden = [Path(root).resolve() for root in forbidden_roots]
        self.opens = 0
        self._real_open = None
        self._real_os_open = None

    def _resolve(self, path) -> str:
        self.opens += 1
        real = Path(os.fspath(path)).resolve()
        for root in self._forbidden:
            if real.is_relative_to(root):
                raise OpenGuardFired(
                    "open guard refused {}: resolves inside forbidden root {}".format(
                        real, root
                    )
                )
        return str(real)

    def _guarded_open(self, file, *args, **kwargs):
        resolved = self._resolve(file)
        return self._real_open(resolved, *args, **kwargs)

    def _guarded_os_open(self, path, *args, **kwargs):
        resolved = self._resolve(path)
        return self._real_os_open(resolved, *args, **kwargs)

    def __enter__(self):
        self._real_open = builtins.open
        self._real_os_open = os.open
        builtins.open = self._guarded_open
        os.open = self._guarded_os_open
        return self

    def __exit__(self, *_exc):
        builtins.open = self._real_open
        os.open = self._real_os_open
        self._real_open = None
        self._real_os_open = None
        return False


def assert_capable_of_firing():
    """Show the guard fires on a forbidden root the run really touches.

    Opens a file inside a freshly planted forbidden directory and expects
    :class:`OpenGuardFired`. Returns the observed open count from the attempt so
    the caller can see the comparison was exercised, not short-circuited.
    """
    with tempfile.TemporaryDirectory(prefix="visoswap-guard-plant-") as tmp:
        planted = Path(tmp)
        (planted / "seed.txt").write_text("x", encoding="utf-8")
        guard = VisoMasterOpenGuard(forbidden_roots=[planted])
        with guard:
            try:
                with open(planted / "seed.txt", "r", encoding="utf-8"):
                    pass
            except OpenGuardFired:
                return guard.opens
            raise AssertionError("open guard did not fire on a planted forbidden root")
