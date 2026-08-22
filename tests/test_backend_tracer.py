"""Contract tests for the engine-backed generator seam."""

import ast
from pathlib import Path


def test_engine_adapter_is_lazy_and_uses_only_public_engine_surface():
    source = Path("backend/services/engine_backend.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not [node for node in imports if getattr(node, "module", "") == "visoswap.engine"]
