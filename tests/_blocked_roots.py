"""The one definition of every package root the test seals refuse.

Two copies of a block list is how two gates silently diverge: one grows a root,
the other keeps passing, and the weaker gate is the one everybody reads. So the
roots live here, once, and ``tests/conftest.py``, ``tests/_qt_guard_probe.py``
and ``tests/_engine_runner.py`` all read them from this module.

**This module imports nothing, deliberately.** The probe and the runner execute
on the *engine* interpreter, which carries torch, onnxruntime and PySide6 but has
no pytest -- measured on both
``D:/Visomaster/dependencies/Python/python.exe`` and ``.venv-clean``. So they
cannot import ``conftest``, which imports pytest at module scope. Putting the
constants in a dependency-free module and re-exporting them from ``conftest`` is
what lets one definition serve both sides of that split without teaching
``conftest`` to pretend pytest is optional.
"""

#: Every Qt binding root. All seven, for the reason measured in plan 01-01:
#: blocking ``PySide6`` alone proves nothing, because the transitive chain
#: reaches Qt through ``qtpy`` independently of any direct binding import.
QT_ROOTS = frozenset(
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

#: VisoMaster's application package. It is an implicit namespace package -- there
#: is no ``app/__init__.py`` -- so it becomes importable the moment a VisoMaster
#: checkout appears on ``sys.path``, which is exactly the condition the engine
#: runner deliberately creates in order to prove the seal is not inert.
#:
#: A surviving ``app.*`` import in vendored code means the rewrite map missed a
#: line and the module only works inside the source tree.
VISOMASTER_ROOTS = frozenset({"app"})

#: VisoSwap's own web layer, arriving in Phase 5. The engine is a library and
#: must not know it exists; ``from backend...`` inside ``visoswap/`` inverts the
#: dependency the whole design rests on. Blocked here so Phase 2's roadmap
#: criterion 3 is a runtime proof rather than a text search.
BACKEND_ROOTS = frozenset({"backend"})

#: What the engine runner seals, as named groups. The grouping is not cosmetic:
#: the runner's self-test provokes each group separately, so "the seal is armed"
#: can never be concluded from one group firing.
SEALED_GROUPS = (
    ("qt", QT_ROOTS),
    ("visomaster", VISOMASTER_ROOTS),
    ("backend", BACKEND_ROOTS),
)

#: Every sealed root, flattened.
ALL_SEALED_ROOTS = frozenset().union(*(roots for _, roots in SEALED_GROUPS))


def root_of(name):
    """``PySide6.QtCore`` -> ``PySide6``. Empty string for ``None``."""
    return (name or "").partition(".")[0]
