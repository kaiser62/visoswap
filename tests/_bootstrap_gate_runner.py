"""Sealed runner for the model-bootstrap startup gate (plan 04-03).

Drives the backend app's lifespan against the ``MODELS_DIR``/``MODELS_VERIFY_MODE``
chosen by the caller and reports which criterion held. Runs on the combined
interpreter (``.venv-clean``), which is the only one carrying the web stack.

Exit codes:
  0  the observed outcome matched the requested expectation
  1  the outcome did not match

Modes:
  --expect-fail  the app must REFUSE to start (criterion 2); prints
                 ``REFUSED:<message>`` on success
  --expect-ok    the app must START and ``GET /api/health`` must return 200
                 (criterion 3); prints ``HEALTH:<status>`` on success
"""

from __future__ import annotations

import asyncio
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
for _path in (_REPO_ROOT, _TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _run_lifespan(expect_fail: bool) -> int:
    from asgi_lifespan import LifespanManager
    import httpx

    from backend.main import create_app

    app = create_app()

    async def _main():
        if not expect_fail:
            # Criterion 3: startup succeeds and /api/health returns 200.
            async with LifespanManager(app) as manager:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=manager.app),
                    base_url="http://test",
                ) as client:
                    response = await client.get("/api/health")
            return ("HEALTH", str(response.status_code))

        # Criterion 2: startup must raise (the app never reaches a serving state).
        try:
            async with LifespanManager(app):
                pass
        except SystemExit as exc:  # uvicorn's exit path, if reached in-process
            return ("REFUSED", str(exc))
        except Exception as exc:  # the ModelVerificationError path
            return ("REFUSED", str(exc))
        return ("STARTED", "")

    label, value = asyncio.run(_main())
    print("{}:{}".format(label, value))
    if expect_fail:
        return 0 if label == "REFUSED" else 1
    return 0 if label == "HEALTH" and value == "200" else 1


if __name__ == "__main__":
    flag = sys.argv[1] if len(sys.argv) > 1 else ""
    if flag not in {"--expect-fail", "--expect-ok"}:
        sys.stderr.write("usage: _bootstrap_gate_runner.py --expect-fail|--expect-ok\n")
        sys.exit(2)
    sys.exit(_run_lifespan(expect_fail=(flag == "--expect-fail")))
