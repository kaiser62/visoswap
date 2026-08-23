"""Verify what is on disk and, separately, repair it.

This module is project-authored (exempted from the attribution gate). It is the
bootstrap half of the model manifest: ``verify`` classifies every required entry
into three states -- present-and-matching, present-and-mismatching, absent --
and ``repair`` fetches the ones that are wrong. The two are deliberately separate
functions, because "what is wrong" and "how to fix it" are different decisions
and a caller must be able to run the first without committing to the second.

**Module-scope imports are stdlib plus the manifest only.** The verify path is the
startup gate, which has to run on an interpreter that may not have ``requests`` or
``tqdm`` installed, so this module must not acquire the downloader's network
dependencies at import. ``download_file`` is imported lazily inside ``repair``.
``tests/test_model_bootstrap.py`` parses this module and asserts exactly that.

**Three states, not two.** A missing file and a corrupt file are different facts
with different remedies, and the vendored downloader already conflates them by
deleting and re-fetching anything that fails its check. A truncated download and a
file somebody replaced look identical to a presence check; they should not look
identical in the report a user reads. Zero bytes count as *absent* rather than
present, for the same reason a swap-only baseline recorded about empty files: an
empty file satisfies ``isfile`` and answers every reader with nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from visoswap.models import manifest
from visoswap.models.integrity_checker import check_file_integrity
from visoswap.schema import resolve_models_dir

__all__ = [
    "FAST",
    "FULL",
    "MODES",
    "VerificationResult",
    "ModelVerificationError",
    "RepairRefused",
    "verify",
    "repair",
]

#: Verify presence and non-emptiness only; do not hash.
FAST = "fast"
#: Verify presence, non-emptiness, and the SHA-256 digest against the manifest.
FULL = "full"
MODES = (FAST, FULL)

#: Where the repo keeps its models directory. On this machine ``model_assets/``
#: is a junction into a read-only weight tree; ``repair`` must never write into
#: the tree that junction resolves to.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class ModelVerificationError(RuntimeError):
    """The models directory is not complete enough to start against.

    Carries the full :class:`VerificationResult` on ``.result`` so a caller that
    needs the three groups does not have to re-run verification to get them.
    """

    def __init__(self, result: "VerificationResult") -> None:
        super().__init__(_verification_message(result))
        self.result = result


class RepairRefused(RuntimeError):
    """``repair`` refuses to fetch a file into a forbidden location.

    The forbidden places are the read-only weight tree the repo's ``model_assets``
    junction resolves into, and any destination whose resolved real path lies
    outside the configured models directory (a link escape). Writing into the
    borrowed weight tree is the one unrecoverable mistake this phase can make.
    """


@dataclass
class VerificationResult:
    """The three-state classification of a models directory.

    ``present``, ``mismatching`` and ``absent`` hold the entries in each state
    (``mismatching`` is empty in ``FAST`` mode, which cannot detect a hash
    change). ``optional`` is always reported and never causes ``ok`` to be
    false. ``mode`` names which pass produced the result, so a fast pass can
    never be mistaken for a verified one.
    """

    mode: str
    required_checked: int
    present: List[manifest.ManifestEntry] = field(default_factory=list)
    mismatching: List[manifest.ManifestEntry] = field(default_factory=list)
    absent: List[manifest.ManifestEntry] = field(default_factory=list)
    optional: List[manifest.ManifestEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing required is absent or mismatching."""
        return not self.absent and not self.mismatching

    def __str__(self) -> str:
        return _verification_message(self, ok_prefix="ok" if self.ok else "incomplete")


def _verification_message(result: VerificationResult, ok_prefix: str = "incomplete") -> str:
    directory = resolve_models_dir()
    lines = [
        "models {}: {} required entries checked in {!r} mode".format(
            ok_prefix, result.required_checked, result.mode
        )
    ]
    if result.absent:
        lines.append("  missing:")
        lines += ["    {} ({})".format(e.name, e.path) for e in result.absent]
    if result.mismatching:
        lines.append("  present but wrong (hash mismatch):")
        lines += ["    {} ({})".format(e.name, e.path) for e in result.mismatching]
    if not result.ok:
        lines.append("  searched directory: {}".format(directory))
    return "\n".join(lines)


def _verify_core(models_dir: Optional[os.PathLike], mode: str) -> VerificationResult:
    if mode not in MODES:
        raise ValueError("unknown verification mode {!r}; expected one of {}".format(mode, MODES))
    entries = manifest.tracked(models_dir)
    required = [e for e in entries if e.required]
    optional = [e for e in entries if not e.required]

    present: List[manifest.ManifestEntry] = []
    mismatching: List[manifest.ManifestEntry] = []
    absent: List[manifest.ManifestEntry] = []

    for entry in required:
        path = entry.path
        if not path.is_file() or path.stat().st_size == 0:
            # Zero bytes is absent, not present: it satisfies isfile and answers
            # every reader with nothing.
            absent.append(entry)
            continue
        if mode == FAST:
            present.append(entry)
            continue
        if check_file_integrity(str(path), entry.digest):
            present.append(entry)
        else:
            mismatching.append(entry)

    return VerificationResult(
        mode=mode,
        required_checked=len(required),
        present=present,
        mismatching=mismatching,
        absent=absent,
        optional=optional,
    )


def verify(models_dir: Optional[os.PathLike] = None, mode: str = FAST) -> VerificationResult:
    """Verify the models directory, raising on an incomplete one.

    Returns the :class:`VerificationResult` when the directory is complete. When
    anything required is absent or mismatching it raises
    :class:`ModelVerificationError` whose message names the offending files and
    the directory searched, and whose ``.result`` carries the full groups.
    """
    result = _verify_core(models_dir, mode)
    if not result.ok:
        raise ModelVerificationError(result)
    return result


def _read_only_weight_tree() -> Optional[Path]:
    """The realpath of the repo's ``model_assets`` junction, if it resolves.

    On this machine ``model_assets/`` is a junction into ``D:/Visomaster``, a
    read-only borrowed weight tree. ``repair`` resolves the junction and refuses
    to write anywhere under its realpath. If the junction is absent or unresolvable
    (e.g. a plain directory that is genuinely writable), this returns ``None`` and
    the guard is a no-op -- a real writable models directory is not the borrowed
    tree.
    """
    junction = REPO_ROOT / "model_assets"
    try:
        return junction.resolve()
    except OSError:
        return None


def _repair_dest_allowed(
    dest: Path,
    models_dir: Path,
    read_only: Optional[Path],
) -> bool:
    """True when ``repair`` may fetch a file to ``dest``.

    Refuses when ``dest``'s resolved real path lies outside the configured models
    directory (a link escape), or inside the read-only weight tree the repo's
    ``model_assets`` junction points at.
    """
    real = dest.resolve()
    if not real.is_relative_to(models_dir.resolve()):
        return False
    if read_only is not None and real.is_relative_to(read_only.resolve()):
        return False
    return True


def repair(
    models_dir: Optional[os.PathLike] = None,
    mode: str = FAST,
    fetcher: Optional[Callable[..., bool]] = None,
) -> VerificationResult:
    """Fetch every required entry that is missing or mismatching.

    ``fetcher`` defaults to the vendored ``visoswap.models.downloader.download_file``,
    imported lazily so this module never needs a network stack at import. A caller
    may pass a stand-in (as the tests do) that records what it was asked to fetch
    and returns without touching the network.

    Each destination is guarded by :meth:`_repair_dest_allowed` before the fetcher
    is called: writing into the read-only weight tree, or through a link that
    escapes the configured models directory, raises :class:`RepairRefused` before
    any file is opened.
    """
    result = _verify_core(models_dir, mode)
    directory = resolve_models_dir(models_dir)
    read_only = _read_only_weight_tree()

    to_fetch = result.absent + result.mismatching
    if not to_fetch:
        return result

    if fetcher is None:
        from visoswap.models.downloader import download_file

        fetcher = download_file

    for entry in to_fetch:
        if not _repair_dest_allowed(entry.path, directory, read_only):
            raise RepairRefused(
                "repair refused to fetch {} to {}: the destination resolves "
                "inside the read-only weight tree or outside the configured "
                "models directory {}".format(
                    entry.name, entry.path, directory
                )
            )
        fetcher(entry.name, str(entry.path), entry.digest, entry.url)

    return _verify_core(models_dir, mode)
