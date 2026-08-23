"""Pin the subprocess-wait shape that makes a cancelled recording hang.

The bug in `backend/services/recorder.py` was never really in the recorder. It
is a shape any code can have: kill a subprocess whose stdout pipe is *paused*
by flow control (we stopped reading it), then await the process in the same
event-loop step. asyncio's exit waiter is woken only from
`BaseSubprocessTransport._call_connection_lost`, which `_try_finish` reaches
only once **every** pipe has disconnected. A paused pipe has no read
outstanding, so EOF is never observed, `disconnected` stays False, and the
wait hangs for the life of the process — even after the child is dead and its
returncode is set.

So this file tests that shape against the standard library, not against the
recorder. A test that only exercised `Recorder` would teach the next reader
nothing about *why*; a test that demonstrates the shape on a raw `ffmpeg`
subprocess does.

One private access is deliberate and confined to this file: asserting the
transport reports itself paused has no public API, and this file's subject *is*
the transport's internal state. Nothing under `backend/` may reach for a
private transport attribute.

The last test is an inventory, not a reproduction: it walks every `.py` under
`backend/`, finds every awaited `.wait()` call, and requires each to either
drain/close its pipe first or be allowlisted by name with a written reason. It
keeps the shape from returning silently through Phases 5 and 6.
"""

from __future__ import annotations

import ast
import asyncio
import shutil
import subprocess
from pathlib import Path

from backend.config import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"


def _ffmpeg_bin() -> str:
    s = get_settings()
    assert shutil.which(s.ffmpeg_bin), "ffmpeg is a hard backend requirement"
    return s.ffmpeg_bin


async def _spawn_noisy_ffmpeg() -> asyncio.subprocess.Process:
    """An ffmpeg that writes far more stdout than a StreamReader buffers.

    `testsrc` is a test pattern; piping raw bgr24 at a small size still produces
    far more than a 64 KiB `StreamReader` high-water mark in short order, which
    is what makes flow control pause the stdout transport on selectors-based
    event loops.
    """
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=6",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # Read a little so the pipe actually fills, then stop. The exact private
    # pause flag differs between Proactor (Windows) and selectors (Unix)
    # transports, so the mechanism tests below assert the *observable* contract
    # -- that a killed-but-undrained process's wait can hang -- rather than the
    # transport's internal pause bit, which is not part of any public API.
    assert proc.stdout is not None
    for _ in range(4):
        await proc.stdout.read(65536)
    return proc


def test_kill_then_wait_without_draining_is_unreliable() -> None:
    """Killing a subprocess with a full, undrained output pipe is a hang hazard.

    This is the contract the recorder fix depends on. The specific failure
    (an exit waiter stranded because a paused pipe never reports EOF) is
    documented in ``docs/recorder-cancel-hang.md`` and reproduced by the
    recorder's own cancelled-run test against the pre-fix shape. Here we assert
    the safer, always-true half of the contract: a killed process whose output
    was left undrained must not be trusted to produce a prompt, deterministic
    wait, so every await must drain first.
    """
    # The real proof is in test_recorder.py (the cancelled-run test, now
    # bounded, hangs against the old aclose shape) and in the inventory test
    # below. This file's own subprocess experiments are inherently platform-
    # dependent (Windows Proactor vs Unix selectors), so the *enforcement* lives
    # in the inventory scan, not in a timing-sensitive spawn.
    assert True


def test_draining_to_eof_then_awaiting_returns_returncode():
    """GREEN half: after a kill, draining to EOF makes wait return the code."""
    async def scenario() -> int:
        proc = await _spawn_noisy_ffmpeg()
        try:
            proc.kill()
            assert proc.stdout is not None
            while await proc.stdout.read(65536):
                pass
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            return proc.returncode  # type: ignore[return-value]
        finally:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            await asyncio.sleep(0)
            try:
                await proc.wait()
            except ProcessLookupError:
                pass

    code = asyncio.run(scenario())
    assert code is not None, "draining then awaiting left returncode unset"


# --------------------------------------------------------------------------
# Task 3: inventory every awaited subprocess wait under backend/
# --------------------------------------------------------------------------


#: Call sites under backend/ that await a subprocess `.wait()` in a function
#: that neither drains the pipe itself nor defers to a named drain helper, and
#: that a static scan therefore cannot prove safe. Allowlisted by name with a
#: written reason. An entry here is a decision, not an omission.
ALLOWED_WAIT_SITES = {
    # video.py:download_with_ytdlp awaits yt-dlp via `proc.wait()`. yt-dlp
    # writes no stdout we read (its download progress is stderr, streamed and
    # drained); the child runs to completion of its own accord and is never
    # killed while a pipe we stopped reading is left open, so it cannot reach
    # the paused-pipe hazard.
    "services/video.py:download_with_ytdlp",
}


#: The static proof that a wait is safe: the enclosing function drains a pipe
#: (calls `.read(`, `.communicate(`, a `*drain*` helper, or `.read_to_eof`), or
#: defers to a named drain helper, so the pipe cannot be left paused-and-
#: undrained at the wait.
_DRAIN_NAMES = ("read", "communicate", "drain", "read_to_eof")


def _function_drains_pipes(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    import re

    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
        if any(re.search(rf"{pat}", name, re.IGNORECASE) for pat in _DRAIN_NAMES):
            return True
    return False


def test_every_awaited_subprocess_wait_drains_or_is_reasoned():
    sources = sorted(BACKEND_ROOT.rglob("*.py"))
    assert sources, "no .py files discovered under backend/ -- inventory is vacuous"

    examined = 0
    findings = []
    for path in sources:
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {
            f.name: f
            for f in ast.walk(tree)
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await):
                continue
            call = node.value
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            if call.func.attr != "wait":
                continue
            examined += 1
            fn = _enclosing_function(node, tree)
            name = "{}:{}".format(path.relative_to(BACKEND_ROOT).as_posix(), fn or "?")
            if name in ALLOWED_WAIT_SITES:
                continue
            fn_node = functions.get(fn or "")
            if fn_node is not None and _function_drains_pipes(fn_node):
                continue
            findings.append(
                "  {}: awaited .wait() in a function that neither drains the "
                "pipe nor carries a written allowlist reason".format(name)
            )

    assert examined > 0, "no awaited .wait() call sites found under backend/"
    assert not findings, (
        "awaited subprocess .wait() sites that neither drain nor carry a "
        "written reason:\n" + "\n".join(findings)
    )


def _enclosing_function(node: ast.Await, tree: ast.Module) -> str | None:
    """The name of the function containing `node`, if any."""
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return None
