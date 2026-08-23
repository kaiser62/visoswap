"""Contract tests for the engine-backed generator seam, plus the end-to-end tracer.

The static assertions (lazy import, surface usage) run on the plain developer
interpreter with no torch. The tracer -- one real generation through the route
on the combined interpreter -- is driven as a subprocess so the plain pytest
interpreter never needs a CUDA stack.
"""

import ast
from pathlib import Path

from tests.conftest import run_backend_runner

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The tracer drives a real generation through the backend route, so it must run
#: on the combined interpreter (web + inference stacks, aiosqlite + torch). The
#: default `engine_python` fixture is VisoMaster's own interpreter, which has
#: torch but no web stack. `.venv-clean` is the combined runtime this repo
#: builds (see docs/backend-port.md).
COMBINED_PYTHON = REPO_ROOT / ".venv-clean" / "Scripts" / "python.exe"


def test_engine_adapter_is_lazy_and_uses_only_public_engine_surface():
    source = Path("backend/services/engine_backend.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not [node for node in imports if getattr(node, "module", "") == "visoswap.engine"]


def test_tracer_drives_one_real_generation():
    """A real frame is generated through the route, on the combined interpreter."""
    assert COMBINED_PYTHON.is_file(), (
        "combined interpreter not found at {} -- run the Phase 4 install".format(COMBINED_PYTHON)
    )
    code, stdout = run_backend_runner(COMBINED_PYTHON, ["--tracer"])
    assert code == 0, "tracer exited {}: {}".format(code, stdout)

    # Parse the single CLEAN line: CLEAN:tracer:frame=... size=... provider=...
    assert stdout.startswith("CLEAN:tracer:"), "unexpected output: {}".format(stdout)
    detail = stdout.split(":", 2)[2]
    kv = {}
    for token in detail.split():
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k] = v

    # Separate assertions -- one `exit == 0` cannot say which of several held.
    frame_name = kv.get("frame")
    assert frame_name, "tracer did not report a frame file: {}".format(stdout)
    assert kv.get("size") and int(kv["size"]) > 0, (
        "generated frame is empty: {}".format(stdout)
    )
    assert kv.get("provider") == "CUDA", (
        "tracer did not resolve provider CUDA: {}".format(stdout)
    )
    assert "PySide6=no" in kv.get("reachable_before_seal", ""), (
        "PySide6 was reachable in the tracer process (or seal was not armed): "
        "{}".format(stdout)
    )
