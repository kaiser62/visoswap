"""The sealed engine runner: the only thing in this project that runs engine code.

Run as a subprocess, **never imported by pytest** -- the same shape as
``_qt_guard_probe.py`` and for the same reason. The interpreter that runs pytest
has no torch; the interpreter that has torch also has Qt. Neither one alone can
both drive a test and execute the engine, so the two are split across a process
boundary and this file lives on the far side of it.

Before anything else is imported, a ``sys.meta_path`` finder is installed at
index 0 refusing three groups of roots:

* the seven Qt binding roots, from Phase 1;
* ``app``, VisoMaster's application package;
* ``backend``, VisoSwap's web layer.

Blocking the second and third is what turns the roadmap's criterion 3 from a text
search into a runtime proof. A grep says no line imports ``backend``. This says
the engine *cannot* reach ``backend`` or VisoMaster **even when both are
installed and importable** -- and the self-test puts a VisoMaster checkout on
``sys.path`` on purpose, so the seal is proven against a package that genuinely
would have resolved rather than against one that was never there.

That distinction is the lesson plan 01-04 paid for: a blocker is inert wherever
the thing it blocks is absent, and an inert blocker reports the same ``CLEAN`` as
a working one.

Exit codes extend the probe's vocabulary so a harness fault can never be read as
an engine pass::

    0  CLEAN           the mode completed and no sealed root was reached
    1  SEAL_BREACHED   a sealed root was reached, or leaked into sys.modules
    2  DEPS_MISSING    an engine dependency is not installed
    3  ASSET_MISSING   a required asset or fixture is not on disk
    4  ENGINE_ERROR    anything else

Exactly one machine-readable line is printed before exit, so pytest can attribute
a failure without parsing a traceback::

    LABEL:mode:detail

Usage::

    python tests/_engine_runner.py --selftest
    python tests/_engine_runner.py --import PySide6.QtCore
"""

import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)

# Both, and before the seal: the repo root so later modes can import
# ``visoswap.*``, the tests directory so the block lists resolve as a plain
# module without dragging in the ``tests`` package.
for _path in (_REPO_ROOT, _TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _blocked_roots import (  # noqa: E402 - must follow the sys.path setup above
    ALL_SEALED_ROOTS,
    SEALED_GROUPS,
    root_of,
)

EXIT_CLEAN = 0
EXIT_SEAL_BREACHED = 1
EXIT_DEPS_MISSING = 2
EXIT_ASSET_MISSING = 3
EXIT_ENGINE_ERROR = 4

VISOMASTER_ENV_VAR = "VISOMASTER_DIR"
DEFAULT_VISOMASTER_DIR = os.path.join("D:", os.sep, "Visomaster")

#: The runner's only settings source. Not the layout dicts (they need Qt), not
#: ``profiles.json`` (untyped strings), not a dict written inline in a test.
#:
#: Overridable so the ASSET_MISSING path can be *demonstrated* rather than
#: assumed -- an exit code nobody has watched fire is an exit code nobody knows
#: the meaning of, which is the whole reason this vocabulary exists.
SETTINGS_FIXTURE_ENV_VAR = "VISOSWAP_SETTINGS_FIXTURE"
DEFAULT_SETTINGS_FIXTURE = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "engine_settings.json"
)


class SealBroken(ImportError):
    """Raised by the finder when a sealed root is requested.

    A dedicated type so "we refused this" is distinguishable from "the module was
    genuinely absent", which are otherwise the same ``ImportError``.
    """


class SealingFinder:
    """A ``sys.meta_path`` finder that denies every sealed root."""

    def find_spec(self, fullname, path=None, target=None):
        if root_of(fullname) in ALL_SEALED_ROOTS:
            raise SealBroken("sealed import refused: {}".format(fullname), name=fullname)
        return None

    # Legacy hook, for anything still on the pre-PEP-451 protocol.
    def find_module(self, fullname, path=None):  # pragma: no cover
        self.find_spec(fullname, path)
        return None


def leaked_roots():
    return sorted(root for root in ALL_SEALED_ROOTS if root in sys.modules)


def one_line(text):
    return " ".join(str(text).split())


def report(code, label, mode, detail):
    print("{}:{}:{}".format(label, mode, one_line(detail)))
    return code


def resolve_visomaster_dir():
    return os.path.abspath(os.environ.get(VISOMASTER_ENV_VAR) or DEFAULT_VISOMASTER_DIR)


def reachability_before_sealing():
    """Which sealed roots would actually have resolved, measured *before* arming.

    This is the anti-inertness check. ``find_spec`` after the seal is armed trips
    the seal, so it has to happen first, and its result is reported rather than
    asserted: ``backend`` does not exist until Phase 5, and a self-test that
    demanded it be reachable would be a test of the calendar.
    """
    import importlib.util

    reachable = {}
    for root in sorted(ALL_SEALED_ROOTS):
        try:
            reachable[root] = importlib.util.find_spec(root) is not None
        except Exception:  # noqa: BLE001 - a broken parent package is "not reachable"
            reachable[root] = False
    return reachable


def arm_seal():
    """Install the finder at index 0. Returns the finder."""
    finder = SealingFinder()
    sys.meta_path.insert(0, finder)
    return finder


def resolve_settings_fixture():
    return os.path.abspath(
        os.environ.get(SETTINGS_FIXTURE_ENV_VAR) or DEFAULT_SETTINGS_FIXTURE
    )


def load_settings(path):
    """The typed settings pair, or ``None`` if the fixture is missing."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def mode_selftest():
    """Arm the seals, prove each group refuses, import nothing from the engine.

    Later plans add modes that actually swap a frame. This one proves only that
    the harness is armed and *capable of failing*, which is the thing that has to
    be true before any engine failure can be believed.
    """
    mode = "selftest"

    preloaded = leaked_roots()
    if preloaded:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots already in sys.modules before the seal armed: "
            + ", ".join(preloaded),
        )

    # On sys.path deliberately: a seal proven against an absent package proves
    # nothing. With the checkout on the path, `app` genuinely resolves, so the
    # refusal below is the seal firing rather than the module simply not being
    # there.
    visomaster_dir = resolve_visomaster_dir()
    if os.path.isdir(visomaster_dir) and visomaster_dir not in sys.path:
        sys.path.append(visomaster_dir)

    reachable = reachability_before_sealing()

    fixture_path = resolve_settings_fixture()
    settings = load_settings(fixture_path)
    if settings is None:
        return report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "settings fixture not found at {} -- regenerate with "
            "tools/dump_engine_settings.py".format(fixture_path),
        )

    arm_seal()

    import importlib

    # Each group is provoked separately. Concluding "the seal is armed" from one
    # group firing is exactly how the other two end up unenforced.
    for group, roots in SEALED_GROUPS:
        # Provoke with a root that genuinely resolves where one exists, so the
        # refusal is the seal firing rather than the module simply not being
        # installed. Falls back to the first root by name when the whole group is
        # absent -- ``backend`` until Phase 5 -- which still proves the finder
        # fires on the name, and is reported as such below.
        candidates = sorted(root for root in roots if reachable.get(root))
        probe_root = candidates[0] if candidates else sorted(roots)[0]
        try:
            importlib.import_module(probe_root)
        except SealBroken:
            pass
        except BaseException as exc:  # noqa: BLE001 - any other outcome is a failure
            return report(
                EXIT_SEAL_BREACHED,
                "SEAL_BREACHED",
                mode,
                "the {} seal raised {} instead of SealBroken for {!r}: {}".format(
                    group, type(exc).__name__, probe_root, exc
                ),
            )
        else:
            return report(
                EXIT_SEAL_BREACHED,
                "SEAL_BREACHED",
                mode,
                "the {} seal did not fire: import {!r} succeeded".format(
                    group, probe_root
                ),
            )

    leaked = leaked_roots()
    if leaked:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots in sys.modules after provoking: " + ", ".join(leaked),
        )

    return report(
        EXIT_CLEAN,
        "CLEAN",
        mode,
        "groups={} settings=project:{},global:{} reachable_before_seal={}".format(
            "+".join(group for group, _ in SEALED_GROUPS),
            len(settings.get("project", {})),
            len(settings.get("global", {})),
            ",".join(
                "{}={}".format(root, "yes" if ok else "no")
                for root, ok in sorted(reachable.items())
            ),
        ),
    )


def mode_import(dotted):
    """Import one named module under the armed seal and report the outcome.

    This is how the runner is *shown* capable of exiting 1 rather than assumed
    to be. A gate nobody has watched fail is a gate nobody knows the state of.
    """
    mode = "import:{}".format(dotted)

    preloaded = leaked_roots()
    if preloaded:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots already in sys.modules before the seal armed: "
            + ", ".join(preloaded),
        )

    visomaster_dir = resolve_visomaster_dir()
    if os.path.isdir(visomaster_dir) and visomaster_dir not in sys.path:
        sys.path.append(visomaster_dir)

    arm_seal()

    import importlib

    try:
        importlib.import_module(dotted)
    except SealBroken as exc:
        return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
    except ModuleNotFoundError as exc:
        if root_of(getattr(exc, "name", None)) in ALL_SEALED_ROOTS:
            # Defensive: the finder raises, so this should be unreachable.
            return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
        return report(EXIT_DEPS_MISSING, "DEPS_MISSING", mode, exc)
    except FileNotFoundError as exc:
        return report(EXIT_ASSET_MISSING, "ASSET_MISSING", mode, exc)
    except BaseException as exc:  # noqa: BLE001 - the runner reports, never raises
        return report(
            EXIT_ENGINE_ERROR,
            "ENGINE_ERROR",
            mode,
            "{}: {}".format(type(exc).__name__, exc),
        )

    leaked = leaked_roots()
    if leaked:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots in sys.modules after importing {}: {}".format(
                dotted, ", ".join(leaked)
            ),
        )

    return report(EXIT_CLEAN, "CLEAN", mode, "imported")


USAGE = "usage: _engine_runner.py (--selftest | --import DOTTED_NAME)"


def main(argv):
    if argv == ["--selftest"]:
        return mode_selftest()
    if len(argv) == 2 and argv[0] == "--import":
        return mode_import(argv[1])
    return report(EXIT_ENGINE_ERROR, "ENGINE_ERROR", "-", USAGE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
