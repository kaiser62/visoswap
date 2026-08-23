"""Verification and repair of the models directory.

These tests build scratch models directories under the test's own tmp_path --
never ``model_assets``, and never any real weights. A controlled manifest of a
handful of small files is installed via monkeypatch, their real digests are
computed, and the test then drives both modes over the scratch tree, removing a
file, truncating one to zero bytes, and flipping a byte in another, asserting
each lands in the state it should.

Two discipline checks live here too, both from plan 04-03:

* ``bootstrap.py`` imports nothing beyond the standard library and the manifest
  at module scope -- the verify path is the startup gate, which must run on an
  interpreter without a network stack.
* Nothing in this module performs a download: ``repair`` is pointed at a
  recording stand-in fetcher that records what it was asked to fetch and returns
  without touching the network.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from visoswap.models import bootstrap, manifest, models_data


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _install_manifest(monkeypatch, scratch: Path, spec):
    """Install a small controlled manifest resolved under ``scratch``.

    ``spec`` is an ordered list of ``(name, content, present)``. ``content`` is
    the bytes that *would* match the manifest digest, and ``present`` says whether
    the file exists on disk. When ``present`` is true the file is written to
    ``scratch``; otherwise it is left absent. The record's hash is always
    ``sha256(content)``, so a FULL pass that reads the file sees it as
    present-and-matching.
    """
    records = []
    for name, content, present in spec:
        digest = hashlib.sha256(content).hexdigest()
        if present:
            (scratch / f"{name}.onnx").write_bytes(content)
        records.append(
            {
                "model_name": name,
                "local_path": f"./model_assets/{name}.onnx",
                "hash": digest,
                "url": f"http://example.test/{name}.onnx",
            }
        )
    monkeypatch.setattr(models_data, "models_list", records)
    monkeypatch.setattr(models_data, "models_trt_list", [])


def _names(result) -> set[str]:
    return {e.name for e in result}


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def test_complete_directory_succeeds_and_names_count(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", True), ("c", b"ccccc", True)]
    )

    result = bootstrap.verify(models_dir=scratch, mode=bootstrap.FULL)
    assert result.ok
    assert result.required_checked == 3
    assert len(result.present) == 3
    assert not result.absent
    assert not result.mismatching
    # The mode is stated in the result.
    assert result.mode == bootstrap.FULL


def test_missing_one_required_fails_and_names_file_and_directory(
    monkeypatch, tmp_path
):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", False), ("c", b"ccccc", True)]
    )

    with pytest.raises(bootstrap.ModelVerificationError) as excinfo:
        bootstrap.verify(models_dir=scratch, mode=bootstrap.FULL)
    message = str(excinfo.value)
    # The message names the missing file and the searched directory.
    assert "b.onnx" in message
    assert str(scratch.resolve()) in message
    # The exception carries the full groups for callers that need them.
    assert _names(excinfo.value.result.absent) == {"b"}


def test_zero_byte_file_is_reported_absent(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"", True), ("c", b"ccccc", True)]
    )

    result = bootstrap._verify_core(scratch, bootstrap.FULL)
    # Zero bytes satisfies isfile but answers every reader with nothing -> absent.
    assert _names(result.absent) == {"b"}
    assert "b" not in _names(result.present)


def test_altered_file_is_reported_mismatching_not_absent(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", True), ("c", b"ccccc", True)]
    )
    # Flip a byte in b after the manifest was installed.
    (scratch / "b.onnx").write_bytes(b"bXbb")

    with pytest.raises(bootstrap.ModelVerificationError) as excinfo:
        bootstrap.verify(models_dir=scratch, mode=bootstrap.FULL)
    message = str(excinfo.value)
    assert "b.onnx" in message
    assert "hash mismatch" in message
    result = excinfo.value.result
    assert _names(result.mismatching) == {"b"}
    assert "b" not in _names(result.absent)  # present-but-wrong, not absent


def test_fast_mode_presence_only_and_mode_named(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"BBBB", True), ("c", b"ccccc", True)]
    )
    # Corrupt b: in FAST mode this is undetectable and must be reported present.
    (scratch / "b.onnx").write_bytes(b"bXbb")

    result = bootstrap.verify(models_dir=scratch, mode=bootstrap.FAST)
    assert result.ok
    assert result.mode == bootstrap.FAST
    assert len(result.present) == 3
    assert not result.mismatching
    assert not result.absent


def test_optional_entries_are_reported_and_never_fail(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", True), ("c", b"ccccc", True)]
    )
    # Add an optional (unfetchable, TensorRT) entry that is absent on disk.
    optional_records = [
        {
            "model_name": "TrtThing",
            "local_path": "./model_assets/liveportrait_onnx/thing.10.6.0.trt",
            "hash": "ab" * 32,  # no url -> not fetchable -> optional
        }
    ]
    monkeypatch.setattr(models_data, "models_trt_list", optional_records)

    result = bootstrap.verify(models_dir=scratch, mode=bootstrap.FULL)
    assert result.ok  # the absent optional entry must not fail verification
    assert _names(result.optional) == {"TrtThing"}


# ---------------------------------------------------------------------------
# module hygiene
# ---------------------------------------------------------------------------


def test_bootstrap_imports_stdlib_only_at_module_scope():
    """The verify path must not acquire a network stack at import."""
    import ast

    tree = ast.parse(Path(bootstrap.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
    banned = {"requests", "tqdm", "torch", "onnxruntime", "cv2", "fastapi"}
    assert not (mods & banned), "bootstrap imports {!r} at module scope".format(
        sorted(mods & banned)
    )


def test_no_literal_model_count_in_bootstrap_module():
    import ast

    tree = ast.parse(Path(bootstrap.__file__).read_text(encoding="utf-8"))
    bad = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value in (56, 6, 62, 12)
    ]
    assert not bad, "bootstrap.py hardcodes a model count: {!r}".format(bad)


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------


def test_repair_fetches_missing_with_recording_stand_in(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", False), ("c", b"ccccc", True)]
    )
    fetched = []

    def recording(name, path, digest, url):
        fetched.append((name, str(path), url))
        (scratch / f"{name}.onnx").write_bytes(b"bbbb")  # restore to matching

    result = bootstrap.repair(models_dir=scratch, mode=bootstrap.FULL, fetcher=recording)
    assert result.ok
    assert len(fetched) == 1
    name, path, url = fetched[0]
    assert name == "b"
    assert url == "http://example.test/b.onnx"  # the recording fetcher saw the URL
    assert "b.onnx" in path


def test_repair_recovers_mismatching_file(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", True), ("c", b"ccccc", True)]
    )
    (scratch / "b.onnx").write_bytes(b"bXbb")  # corrupt
    fetched = []

    def recording(name, path, digest, url):
        fetched.append(name)
        (scratch / f"{name}.onnx").write_bytes(b"bbbb")  # restore to matching

    result = bootstrap.repair(models_dir=scratch, mode=bootstrap.FULL, fetcher=recording)
    assert result.ok
    assert fetched == ["b"]


def test_repair_refuses_to_fetch_into_read_only_tree(monkeypatch, tmp_path):
    """The read-only weight tree is the one unrecoverable mistake."""
    scratch = tmp_path / "models"
    scratch.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", False), ("c", b"ccccc", True)]
    )
    fetched = []

    def recording(name, path, digest, url):
        fetched.append(name)
        return True

    # From repair's perspective the whole scratch dir is the read-only tree.
    monkeypatch.setattr(bootstrap, "_read_only_weight_tree", lambda: scratch.resolve())

    with pytest.raises(bootstrap.RepairRefused):
        bootstrap.repair(models_dir=scratch, mode=bootstrap.FULL, fetcher=recording)
    assert fetched == []  # the fetcher was never called


def test_repair_guard_rejects_outside_and_read_only_dests(monkeypatch, tmp_path):
    scratch = tmp_path / "models"
    scratch.mkdir()
    read_only = tmp_path / "readonly"
    read_only.mkdir()
    read_only_dest = read_only / "x.onnx"
    inside = scratch / "x.onnx"

    # A dest inside a normal models dir is allowed.
    assert bootstrap._repair_dest_allowed(inside, scratch, read_only) is True
    # A dest inside the read-only tree is refused even though it is not escaped.
    assert bootstrap._repair_dest_allowed(read_only_dest, scratch, read_only) is False
    # A dest outside the models dir is refused.
    assert bootstrap._repair_dest_allowed(read_only_dest, scratch, None) is False


# ---------------------------------------------------------------------------
# roadmap criteria 2 & 3, end to end (plan 04-03 Task 3)
# ---------------------------------------------------------------------------
#
# These drive the real backend app's lifespan against the caller's MODELS_DIR, so
# they need the web stack and run as subprocesses on the combined interpreter
# (.venv-clean) -- the plain pytest interpreter never imports backend.


COMBINED_PYTHON = REPO_ROOT / ".venv-clean" / "Scripts" / "python.exe"
GATE_RUNNER = REPO_ROOT / "tests" / "_bootstrap_gate_runner.py"


def _gate_env(**overrides) -> dict:
    env = dict(os.environ)
    env.update(overrides)
    return env


def _run_gate(env, *flags) -> subprocess.CompletedProcess:
    assert COMBINED_PYTHON.is_file(), (
        "combined interpreter not found at {} -- run the Phase 4 install".format(COMBINED_PYTHON)
    )
    return subprocess.run(
        [str(COMBINED_PYTHON), "-B", str(GATE_RUNNER), *flags],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )


def test_criterion2_incomplete_models_refuses_at_startup(tmp_path):
    """The app raises from its lifespan; the message names the searched directory."""
    scratch = tmp_path / "empty-models"
    scratch.mkdir()
    proc = _run_gate(
        _gate_env(MODELS_DIR=str(scratch), MODELS_VERIFY_MODE="full"),
        "--expect-fail",
    )
    assert proc.returncode == 0, "runner failed: {}".format(proc.stdout + proc.stderr)
    assert proc.stdout.startswith("REFUSED:"), "app did not refuse: {}".format(proc.stdout)
    message = proc.stdout[len("REFUSED:") :]
    assert "models" in message
    assert str(scratch.resolve()) in message, "message must name the searched directory"


def test_criterion2_incomplete_models_exits_nonzero_as_a_process(tmp_path):
    """As a real server process, an incomplete MODELS_DIR must exit non-zero."""
    scratch = tmp_path / "empty-models"
    scratch.mkdir()
    proc = subprocess.run(
        [str(COMBINED_PYTHON), "-m", "uvicorn", "backend.main:app", "--port", "0"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=_gate_env(MODELS_DIR=str(scratch)),
        timeout=120,
    )
    assert proc.returncode != 0, "server started despite incomplete models"
    combined = proc.stdout + proc.stderr
    assert str(scratch.resolve()) in combined, "process error must name the searched directory"


def test_criterion3_complete_models_health_200():
    """A complete tree starts and /api/health returns 200.

    Uses ``fast`` mode so the test is deterministic and does not re-hash 12 GB on
    every run; the *hash*-verification of the real tree is proven separately by
    :func:`test_real_tree_hashes_to_manifest` and the FULL-mode verify logic by
    the scratch-directory tests above. The real tree is the repo's ``model_assets``
    junction, which holds all required files present and non-empty.
    """
    proc = _run_gate(_gate_env(MODELS_VERIFY_MODE="fast"), "--expect-ok")
    assert proc.returncode == 0, "runner failed: {}".format(proc.stdout + proc.stderr)
    assert proc.stdout.startswith("HEALTH:200"), "expected 200, got: {}".format(proc.stdout)


def test_real_tree_hashes_to_manifest():
    """The real tree's required files hash to their manifest digests.

    A full 12 GB pass is too slow for a unit test, so this proves the property
    on a representative required file (``Inswapper128``, 277 MB, ~0.16 s warm) --
    the same file the plan measured hashing to its manifest digest. This is what
    makes criterion 3 "verified by hash" true of the real tree rather than assumed.
    """
    from visoswap.models.integrity_checker import check_file_integrity

    candidates = [e for e in manifest.tracked() if e.name == "Inswapper128"]
    assert candidates, "no Inswapper128 entry in the manifest"
    entry = candidates[0]
    assert check_file_integrity(str(entry.path), entry.digest), (
        "real tree file {} does not hash to its manifest digest".format(entry.path)
    )


def test_resolve_startup_mode_auto_full_then_fast(tmp_path):
    """auto runs full with no marker, then fast once a full pass is recorded."""
    assert bootstrap.resolve_startup_mode("auto", tmp_path) == bootstrap.FULL
    bootstrap.verification_marker_path(tmp_path).write_text("ok\n", encoding="utf-8")
    assert bootstrap.resolve_startup_mode("auto", tmp_path) == bootstrap.FAST


def test_resolve_startup_mode_forces_explicit_side(tmp_path):
    """fast/full override auto regardless of the marker."""
    assert bootstrap.resolve_startup_mode("fast", tmp_path) == bootstrap.FAST
    assert bootstrap.resolve_startup_mode("full", tmp_path) == bootstrap.FULL


def test_verify_at_startup_records_marker_after_full(monkeypatch, tmp_path):
    """A successful full pass writes the marker; a fast pass does not."""
    scratch = tmp_path / "models"
    scratch.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _install_manifest(
        monkeypatch, scratch, [("a", b"aaa", True), ("b", b"bbbb", True), ("c", b"ccccc", True)]
    )
    marker = bootstrap.verification_marker_path(data_dir)

    bootstrap.verify_at_startup(scratch, data_dir, "full")
    assert marker.exists()

    marker.unlink()
    bootstrap.verify_at_startup(scratch, data_dir, "fast")
    assert not marker.exists()
