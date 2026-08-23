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
import re
from pathlib import Path

import pytest

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
