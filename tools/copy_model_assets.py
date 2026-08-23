"""Copy the 12GB weight set into a project-owned directory, verified.

``visoswap/models/models_data.py`` reads ``MODELS_DIR`` with a project-owned
default once plan 04-04 lands. That default points at a directory this repository
owns (``model_assets_owned/``) rather than at the ``model_assets`` junction into
the borrowed ``D:/Visomaster`` tree. This tool produces that owned directory by
**copying** (never moving, never deleting) from the VisoMaster checkout, and it
verifies every copied file against the same manifest digests plan 04-03 defined.

The refusals are load-bearing and not optional (threats T-04-21 / T-04-22):

- Refuse a destination whose resolved real path lies inside a VisoMaster install.
- Refuse to copy from an unreadable source.
- Refuse to proceed if the destination volume has less free space than the
  manifest's total size plus a margin.
- Copy, never move; never ``rmdir``/``rm -rf``/rename anything under the source.
  On Windows a recursive delete aimed at a junction can follow it into the target,
  and the target here is 12.23 GB of weights this project must not touch.
- Verify every copied entry against its manifest digest afterwards and report the
  three states plan 04-03 defined. A copy is complete when the verifier says so,
  not when the copy loop ends.

Standard library + the manifest only, so it runs before any engine dependency is
known to be installed.

Usage:
    python tools/copy_model_assets.py            # make the verified owned copy
    python tools/copy_model_assets.py --check    # verify the existing copy
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# The script runs as `python tools/copy_model_assets.py`, so the repo root is not
# on sys.path; add it so the manifest import resolves regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REPO_ROOT = _REPO_ROOT

#: The project-owned model directory. This is the default ``models_dir`` for the
#: whole project once 04-04 lands, and the destination of the copy.
OWNED_DIR = REPO_ROOT / "model_assets_owned"

#: The source tree this project vendored its engine from. Read-only.
DEFAULT_SOURCE = Path("D:/Visomaster/model_assets")
SOURCE_ENV_VAR = "VISOSWAP_MODEL_ASSETS_SOURCE"

#: Extra headroom required on the destination volume beyond the manifest total.
FREE_MARGIN_BYTES = 1 << 30  # 1 GiB


def resolve_source() -> Path:
    override = os.environ.get(SOURCE_ENV_VAR)
    return Path(override or DEFAULT_SOURCE).expanduser()


def _resolves_inside_visomaster(path: Path) -> bool:
    """Whether ``path``'s resolved real path lies inside a VisoMaster install.

    Uses ``realpath`` deliberately: ``os.path.islink`` returns ``False`` for the
    ``model_assets`` junction on this machine (measured), so a link check proves
    nothing. The resolved path is the only check that sees the junction.
    """
    real = path.resolve()
    # A VisoMaster install is recognisable by its root: D:/Visomaster. We refuse
    # any destination whose real path is at or under it. Use the realpath so a
    # junction pointing into it is caught too.
    viso = Path("D:/Visomaster").resolve()
    return real.is_relative_to(viso)


def manifest_total_and_entries() -> tuple[int, list]:
    from visoswap.models import manifest

    required = manifest.required_entries()
    total = 0
    records = []
    for entry in required:
        records.append((entry.name, entry.path, entry.digest))
        if entry.path.is_file():
            total += entry.path.stat().st_size
    return total, records


def _check_volume_space(dest: Path, needed: int) -> None:
    anchor = dest if dest.exists() else dest.parent
    while not anchor.exists():
        anchor = anchor.parent
    free = shutil.disk_usage(anchor).free
    if free < needed + FREE_MARGIN_BYTES:
        raise SystemExit(
            "FAILED: {} has {:.1f} GiB free but the model set needs {:.1f} GiB "
            "plus {:.1f} GiB margin. Refusing to start a copy that cannot finish.".format(
                anchor, free / (1 << 30), needed / (1 << 30), FREE_MARGIN_BYTES / (1 << 30)
            )
        )


#: Extra runtime assets the engine reads that are NOT in the 56-file manifest
#: (which is all ONNX). These are copied too, or the LivePortrait face editor and
#: 3d68 landmark reads fail at runtime.
EXTRA_ASSETS = (
    "liveportrait_onnx/lip_array.pkl",
    "meanshape_68.pkl",
)


def _copy_extra_assets(source: Path, dest: Path) -> None:
    for relative in EXTRA_ASSETS:
        src = source / relative
        if not src.is_file():
            print("WARN: extra asset {} not present in source; skipping".format(relative))
            continue
        dst = dest / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not (dst.is_file() and dst.stat().st_size == src.stat().st_size):
            shutil.copy2(src, dst)


def copy_set(source: Path, dest: Path) -> None:
    """Copy every required file source->dest, verifying each after copying."""
    source_real = source.resolve()
    if not source.is_dir():
        raise SystemExit("FAILED: source is not a directory: {}".format(source))
    if _resolves_inside_visomaster(dest):
        raise SystemExit(
            "FAILED: destination {} resolves inside a VisoMaster install; refusing.".format(dest)
        )

    total, records = manifest_total_and_entries()
    _check_volume_space(dest, total)
    print(
        "copying {} required files (~{:.2f} GiB) from {} to {}".format(
            len(records), total / (1 << 30), source, dest
        )
    )

    dest.mkdir(parents=True, exist_ok=True)
    for name, src_path, digest in records:
        # The relative location under the source tree.
        try:
            rel = src_path.resolve().relative_to(source_real)
        except ValueError:
            raise SystemExit(
                "FAILED: manifest entry {} does not live under source {}; refusing.".format(
                    name, source
                )
            )
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Copy, never move. If a partial file exists (interrupted run), re-copy it
        # so an interrupted copy is detectable as incomplete rather than done.
        if dst.is_file() and _hash_matches(dst, digest):
            continue
        shutil.copy2(src_path, dst)

    _copy_extra_assets(source, dest)
    print("copy complete; verifying every entry against its manifest digest...")
    verify_set(dest, check_required_ok=False)


def _hash_matches(path: Path, digest: str) -> bool:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 17), b""):
            h.update(block)
    return h.hexdigest() == digest


def verify_set(dest: Path, check_required_ok: bool = True) -> None:
    """Verify the owned copy against the manifest; report three states."""
    from visoswap.models import bootstrap

    # bootstrap.verify resolves the directory via MODELS_DIR / default. We need it
    # to look at `dest`, so run it with the destination injected as MODELS_DIR.
    old = os.environ.get("MODELS_DIR")
    os.environ["MODELS_DIR"] = str(dest.resolve())
    try:
        result = bootstrap.verify(mode=bootstrap.FULL)
    finally:
        if old is None:
            os.environ.pop("MODELS_DIR", None)
        else:
            os.environ["MODELS_DIR"] = old

    print(
        "verify: required={} present={} mismatching={} absent={} mode={}".format(
            result.required_checked,
            len(result.present),
            len(result.mismatching),
            len(result.absent),
            result.mode,
        )
    )
    if check_required_ok and not result.ok:
        raise SystemExit("FAILED: the copy is incomplete:\n{}".format(result))
    if not result.ok:
        raise SystemExit("FAILED: the copy is incomplete:\n{}".format(result))


def main(argv) -> int:
    parser = argparse.ArgumentParser(
        description="Copy the weight set into a project-owned directory, verified."
    )
    parser.add_argument("--check", action="store_true", help="verify the existing copy")
    parser.add_argument("--dest", default=str(OWNED_DIR), help="destination directory")
    args = parser.parse_args(argv)

    source = resolve_source()
    dest = Path(args.dest)
    print("source: {} (from {})".format(source, SOURCE_ENV_VAR if os.environ.get(SOURCE_ENV_VAR) else "default"))
    print("dest:   {}".format(dest))

    if args.check:
        verify_set(dest)
        return 0
    copy_set(source, dest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
