"""Qt-reachability probe.

Run as a subprocess, never imported by pytest. It installs a ``sys.meta_path``
finder that refuses every Qt binding root, then imports the modules named on the
command line and reports whether any of them reached Qt.

Blocking ``PySide6`` alone proves nothing. Measured against unmodified VisoMaster
source: with only ``PySide6`` blocked, ``app/processors/workers/frame_worker.py``
fails with ``QtBindingsNotFoundError`` -- raised by ``qtpy``, not by the block.
The transitive chain reaches Qt through ``qtpy`` independently of any direct
binding import, so every root is blocked.

Exit codes are distinct on purpose. Conflating "no Qt was reached" with "the
import died before it could reach Qt" is exactly how this gate goes green for the
wrong reason:

    0  CLEAN         every module imported and no blocked root is in sys.modules
    1  QT_REACHED    the block tripped, or a blocked root leaked into sys.modules
    2  DEPS_MISSING  a ModuleNotFoundError named a root that is not blocked
    3  IMPORT_ERROR  anything else, including a Qt root present before we started

Usage:
    python tests/_qt_guard_probe.py visoswap.processors.utils.faceutil
"""

import importlib
import os
import sys

#: Every Qt binding root. All of them, for the measured reason above.
BLOCKED_ROOTS = frozenset(
    {
        "PySide6",
        "PySide2",
        "PyQt5",
        "PyQt6",
        "qtpy",
        "shiboken6",
        "shiboken2",
    }
)

EXIT_CLEAN = 0
EXIT_QT_REACHED = 1
EXIT_DEPS_MISSING = 2
EXIT_IMPORT_ERROR = 3


class QtBlocked(ImportError):
    """Raised by the meta_path finder when a blocked Qt root is requested.

    A dedicated type so the probe can tell "we blocked this" apart from "the
    module was genuinely absent", which are the same ``ImportError`` otherwise.
    """


def _root_of(name):
    return (name or "").partition(".")[0]


class QtBlockingFinder:
    """A ``sys.meta_path`` finder that denies every Qt binding root."""

    def find_spec(self, fullname, path=None, target=None):
        if _root_of(fullname) in BLOCKED_ROOTS:
            raise QtBlocked(
                "blocked Qt binding import: {}".format(fullname), name=fullname
            )
        return None

    # Legacy hook, for anything still calling the pre-PEP-451 protocol.
    def find_module(self, fullname, path=None):  # pragma: no cover
        self.find_spec(fullname, path)
        return None


def _leaked_roots():
    return sorted(root for root in BLOCKED_ROOTS if root in sys.modules)


def _one_line(text):
    return " ".join(str(text).split())


def _report(code, label, module, detail):
    print("{}:{}:{}".format(label, module, _one_line(detail)))
    return code


def main(argv):
    modules = list(argv)
    if not modules:
        return _report(
            EXIT_IMPORT_ERROR, "IMPORT_ERROR", "-", "no module names given on argv"
        )

    # A Qt root already imported at interpreter start (sitecustomize, a .pth
    # file) would otherwise be blamed on the module under test. Report it as an
    # environment fault rather than a false QT_REACHED.
    preloaded = _leaked_roots()
    if preloaded:
        return _report(
            EXIT_IMPORT_ERROR,
            "IMPORT_ERROR",
            "-",
            "blocked roots already in sys.modules before the probe armed: "
            + ", ".join(preloaded),
        )

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    sys.meta_path.insert(0, QtBlockingFinder())

    for name in modules:
        try:
            importlib.import_module(name)
        except QtBlocked as exc:
            return _report(EXIT_QT_REACHED, "QT_REACHED", name, exc)
        except ModuleNotFoundError as exc:
            root = _root_of(getattr(exc, "name", None))
            if root in BLOCKED_ROOTS:
                # Defensive: the finder raises, so this should be unreachable.
                return _report(EXIT_QT_REACHED, "QT_REACHED", name, exc)
            return _report(
                EXIT_DEPS_MISSING, "DEPS_MISSING", name, root or _one_line(exc)
            )
        except Exception as exc:  # noqa: BLE001 - the probe reports, never raises
            return _report(
                EXIT_IMPORT_ERROR,
                "IMPORT_ERROR",
                name,
                "{}: {}".format(type(exc).__name__, exc),
            )

        # Checked after every import, not only at the end, so a leak is
        # attributed to the module that caused it. A module that swallows the
        # block with `try: import PySide6 / except ImportError: pass` would
        # otherwise import "successfully" while Qt sits in sys.modules.
        leaked = _leaked_roots()
        if leaked:
            return _report(
                EXIT_QT_REACHED,
                "QT_REACHED",
                name,
                "blocked roots present in sys.modules: " + ", ".join(leaked),
            )

    print("CLEAN")
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
