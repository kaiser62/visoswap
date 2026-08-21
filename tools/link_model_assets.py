"""Make the 12GB weight set reachable from the repository without copying it.

``visoswap/models/models_data.py`` sets ``models_dir = './model_assets'`` -- a
*relative* path, vendored verbatim, with no environment override until Phase 4.
Every asset read in the engine is built from that string by f-string at import or
call time. So the working directory of whatever runs engine code is load-bearing,
and the repository root needs a ``model_assets`` entry that resolves to the real
weights.

Copying is the wrong mechanism. ``ModelsProcessor.load_model`` does not
auto-download -- the download call is commented out upstream -- so a missing
weight raises out of ``onnxruntime.InferenceSession`` rather than starting a 12GB
fetch. A link gives the engine the whole set for zero bytes and zero download.

The link is intended **read-only**. It points into ``D:/Visomaster``, which is
declared read-only source material for this project; anything written through it
lands in that tree. Nothing in Phase 2 writes through it.

Standard library only, on purpose: this runs before any engine dependency is
known to be installed, and on the plain developer interpreter which has neither
torch nor Qt.

Usage:
    python tools/link_model_assets.py           # create the link
    python tools/link_model_assets.py --check   # verify the link and probe set
"""

import argparse
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Where the link is created. The name is fixed by ``models_dir`` in vendored
#: code; the location is fixed by that path being relative to the process CWD.
LINK_PATH = os.path.join(REPO_ROOT, "model_assets")

SOURCE_ENV_VAR = "VISOSWAP_MODEL_ASSETS_SOURCE"

#: The VisoMaster checkout this project vendored its engine from. Read-only.
DEFAULT_SOURCE = os.path.join("D:", os.sep, "Visomaster", "model_assets")

#: Exactly the files the Phase 2 swap path reads, named rather than globbed so a
#: later phase can widen or shrink the set deliberately and see the diff.
#:
#:   det_10g.onnx                            RetinaFace detector
#:   w600k_r50.onnx                          Inswapper128ArcFace recogniser
#:   inswapper_128.fp16.onnx                 the swapper itself
#:   occluder.onnx                           occlusion mask
#:   XSeg_model.onnx                         XSeg mask
#:   faceparser_resnet34.onnx                face parser
#:   meanshape_68.pkl                        3d68 landmark mean shape
#:   liveportrait_onnx/lip_array.pkl         read by FaceEditors.__init__
#:   liveportrait_onnx/motion_extractor.onnx LivePortrait entry model
#:
#: ``rd64-uni-refined.pth`` is deliberately **not** here. CLIPseg needs it, it
#: exists nowhere under ``D:/Visomaster``, and it is absent from the 56-entry
#: model manifest -- upstream's text-masking control is non-functional on this
#: install too. Plan 02-04 owns that gap. A check that fails from day one teaches
#: everyone to ignore the check.
PROBE_FILES = (
    "det_10g.onnx",
    "w600k_r50.onnx",
    "inswapper_128.fp16.onnx",
    "occluder.onnx",
    "XSeg_model.onnx",
    "faceparser_resnet34.onnx",
    "meanshape_68.pkl",
    "liveportrait_onnx/lip_array.pkl",
    "liveportrait_onnx/motion_extractor.onnx",
)

EXIT_OK = 0
EXIT_FAILED = 1


def resolve_source() -> str:
    """$VISOSWAP_MODEL_ASSETS_SOURCE, else the known VisoMaster checkout."""
    return os.path.abspath(os.environ.get(SOURCE_ENV_VAR) or DEFAULT_SOURCE)


def link_target(path):
    """The path a link points at, or ``None`` if ``path`` is not a link.

    ``os.path.islink`` reports ``False`` for a Windows junction, which is exactly
    what this script creates, so islink alone would classify our own link as a
    real directory and refuse to touch it. ``os.readlink`` has understood
    junctions since 3.8 and is the portable test here: it succeeds for a symlink
    and for a junction, and raises for anything else.
    """
    try:
        return os.readlink(path)
    except (OSError, ValueError):
        return None


def is_link(path) -> bool:
    return os.path.islink(path) or link_target(path) is not None


def same_location(left, right) -> bool:
    """Whether two paths name the same directory, case-insensitively on Windows.

    A junction read back on Windows comes out as ``\\\\?\\D:\\Visomaster\\...``,
    which never string-compares equal to the source we were handed. Resolving
    both sides through ``realpath`` normalises the prefix away.
    """
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
        os.path.realpath(right)
    )


def create_link(source) -> int:
    """Create the ``model_assets`` link. Returns a process exit code."""
    if not os.path.isdir(source):
        print("FAILED: source is not a directory: {}".format(source))
        return EXIT_FAILED

    if os.path.lexists(LINK_PATH):
        if is_link(LINK_PATH):
            if same_location(LINK_PATH, source):
                print("OK: link already points at {}".format(source))
                return EXIT_OK
            print(
                "FAILED: {} is a link to {}, not to {}. Remove it deliberately "
                "and re-run.".format(
                    LINK_PATH, os.path.realpath(LINK_PATH), source
                )
            )
            return EXIT_FAILED
        # A real directory of weights here is the one unrecoverable mistake this
        # script could make. Never delete, never overwrite, never merge.
        print(
            "FAILED: {} already exists and is not a link. Refusing to touch it "
            "-- if those are real weights, deleting them is unrecoverable.".format(
                LINK_PATH
            )
        )
        return EXIT_FAILED

    if os.name == "nt":
        # A junction, not a symbolic link: junctions need no administrator
        # rights and no developer-mode opt-in. ``mklink`` is a cmd builtin, so
        # cmd has to be the executable -- but the paths go through as argv
        # entries, never interpolated into a shell string (shell=False).
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", LINK_PATH, source],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = (proc.stdout + proc.stderr).strip()
            print("FAILED: mklink /J exited {}: {}".format(proc.returncode, detail))
            return EXIT_FAILED
    else:
        try:
            os.symlink(source, LINK_PATH, target_is_directory=True)
        except OSError as exc:
            print("FAILED: symlink: {}".format(exc))
            return EXIT_FAILED

    print("CREATED: {} -> {}".format(LINK_PATH, source))
    return EXIT_OK


def check_link(source) -> int:
    """Verify the link and every probe file readable through it."""
    if not os.path.lexists(LINK_PATH):
        print("FAILED: {} does not exist. Run without --check first.".format(LINK_PATH))
        return EXIT_FAILED

    if not is_link(LINK_PATH):
        print("FAILED: {} exists but is not a link.".format(LINK_PATH))
        return EXIT_FAILED

    if not os.path.isdir(LINK_PATH):
        print("FAILED: {} does not resolve to a directory.".format(LINK_PATH))
        return EXIT_FAILED

    if not same_location(LINK_PATH, source):
        print(
            "FAILED: {} resolves to {}, expected {}.".format(
                LINK_PATH, os.path.realpath(LINK_PATH), source
            )
        )
        return EXIT_FAILED

    missing = []
    for relative in PROBE_FILES:
        candidate = os.path.join(LINK_PATH, *relative.split("/"))
        if not os.path.isfile(candidate):
            missing.append(relative)
            continue
        try:
            # Readable *through the link*, not merely present -- a junction into
            # a tree with a denied ACL lists fine and reads nothing.
            with open(candidate, "rb") as handle:
                handle.read(1)
        except OSError as exc:
            missing.append("{} ({})".format(relative, exc))

    if missing:
        print(
            "FAILED: {} of {} probe files unreadable through the link:".format(
                len(missing), len(PROBE_FILES)
            )
        )
        for name in missing:
            print("  - {}".format(name))
        return EXIT_FAILED

    print(
        "OK: {} -> {} ({} probe files readable)".format(
            LINK_PATH, os.path.realpath(LINK_PATH), len(PROBE_FILES)
        )
    )
    return EXIT_OK


def main(argv) -> int:
    parser = argparse.ArgumentParser(
        description="Link the repository's model_assets at the VisoMaster weight set."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the existing link and its probe set instead of creating it",
    )
    args = parser.parse_args(argv)

    source = resolve_source()
    # Printed on every run, before anything can fail: a link pointed at the
    # wrong tree must be diagnosable from the log alone.
    print(
        "source: {} (from {})".format(
            source,
            SOURCE_ENV_VAR if os.environ.get(SOURCE_ENV_VAR) else "default",
        )
    )

    return check_link(source) if args.check else create_link(source)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
