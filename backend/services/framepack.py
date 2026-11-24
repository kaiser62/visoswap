"""Batched frame delivery: one response, many frames.

Latency, not bandwidth, is what hurts on a tunnelled backend: ~330 ms per round
trip, so 16 parallel single-frame loads cap playback near 40 fps while the bytes
themselves are only ~2 MB/s. Fetching twelve frames in one request turns twelve
round trips into one.

Wire format (see `docs/frame-pack.md` for the client snippets):

    +-------------------+------------------+---------------------+-----+
    | 4 bytes, big-endian uint32 header length | JSON header | frames |
    +-------------------+------------------+---------------------+-----+

The header is
`{"frames":[{"key":"000051.633","offset":0,"length":35120,"status":200}, ...]}`,
where each `offset` is relative to the start of the byte blob after the header.
A frame that is missing or not yet generated still appears, with `status: 404`
and `length: 0`, so the client can tell "not ready" from "the pack broke" without
retrying the whole batch.

The header comes first and is complete before any frame byte, so a client can
parse the index and then slice the blob; it never has to guess.
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

#: A pack is one round trip's worth of frames. Larger batches save nothing (the
#: client caps concurrency anyway) and a bigger response is worse on a weak link.
MAX_KEYS = 64
#: Refuse to build a response larger than this. At 720px/q70 a frame is ~37 KB,
#: so 8 MB is over 200 preview frames -- the cap only ever trips on originals.
MAX_BYTES = 8 * 1024 * 1024
#: Header length is 4 bytes big-endian; this bounds a malicious or broken header.
MAX_HEADER_BYTES = 64 * 1024

MAGIC_CONTENT_TYPE = "application/x-visoswap-pack"


class PackError(ValueError):
    """A pack that cannot be built for reasons the client can fix."""


@dataclass
class PackedFrame:
    key: str
    offset: int
    length: int
    status: int
    data: Optional[bytes] = None

    def to_header(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "offset": self.offset,
            "length": self.length,
            "status": self.status,
        }


def build(frames: Iterable[PackedFrame]) -> bytes:
    """Header then payload. Offsets are assigned here, not by the caller."""
    entries: list[PackedFrame] = []
    offset = 0
    for frame in frames:
        if frame.data:
            frame.offset = offset
            frame.length = len(frame.data)
            offset += frame.length
        else:
            frame.offset = 0
            frame.length = 0
        entries.append(frame)

    header = json.dumps(
        {"frames": [f.to_header() for f in entries]}, separators=(",", ":")
    ).encode("utf-8")
    if len(header) > MAX_HEADER_BYTES:  # pragma: no cover - 64 keys cannot reach it
        raise PackError("pack header too large")

    out = bytearray(struct.pack(">I", len(header)))
    out += header
    for frame in entries:
        if frame.data:
            out += frame.data
    return bytes(out)


def parse(blob: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Reference parser, mirroring the client snippets in `docs/frame-pack.md`.

    Returns `(header, {key: bytes})`. Frames with a non-200 status are absent
    from the mapping rather than mapped to empty bytes: "missing" and "empty"
    are different things to a caller.
    """
    if len(blob) < 4:
        raise PackError("response shorter than the 4-byte header length")
    (header_len,) = struct.unpack(">I", blob[:4])
    if header_len > MAX_HEADER_BYTES or 4 + header_len > len(blob):
        raise PackError("header length is out of range")
    header = json.loads(blob[4 : 4 + header_len].decode("utf-8"))
    body = blob[4 + header_len :]
    out: dict[str, bytes] = {}
    for entry in header.get("frames", []):
        if entry.get("status") != 200 or not entry.get("length"):
            continue
        start = int(entry["offset"])
        end = start + int(entry["length"])
        if end > len(body):
            raise PackError(f"frame {entry.get('key')!r} runs past the payload")
        out[entry["key"]] = body[start:end]
    return header, out


def total_bytes(frames: Iterable[PackedFrame]) -> int:
    return sum(len(f.data) for f in frames if f.data)


def check_request(keys: list[str]) -> None:
    """Reject a pack request the server will not honour, before doing any work."""
    if not keys:
        raise PackError("keys must not be empty")
    if len(keys) > MAX_KEYS:
        raise PackError(f"at most {MAX_KEYS} keys per pack")
    if len(set(keys)) != len(keys):
        # Duplicates are a client bug and would double the bytes for one frame.
        raise PackError("keys must be unique")
