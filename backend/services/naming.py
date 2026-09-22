"""Project naming utility.

Derives clean, unique project names within 10-15 characters from a filename or URL.
"""

from __future__ import annotations

import re
import secrets
import urllib.parse
from typing import Collection


def derive_clean_base(source: str | None) -> str:
    """Extract a concise alphanumeric base slug from a URL or filename."""
    if not source:
        return "proj"

    cleaned = str(source).strip()
    candidate = ""
    try:
        parsed = urllib.parse.urlparse(cleaned)
        # Check query param like ?v=...
        query_params = urllib.parse.parse_qs(parsed.query)
        if "v" in query_params and query_params["v"]:
            candidate = query_params["v"][0]
        elif parsed.path:
            unquoted = urllib.parse.unquote(parsed.path)
            candidate = unquoted.rstrip("/\\").split("/")[-1].split("\\")[-1]
            candidate = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", candidate)
        elif parsed.query:
            candidate = parsed.query
    except Exception:
        candidate = cleaned

    candidate = re.sub(r"[^a-zA-Z0-9]+", "-", candidate).strip("-")
    # Filter generic placeholder names
    if not candidate or candidate.lower() in (
        "video",
        "source",
        "input",
        "output",
        "mp4",
        "mov",
        "untitled",
        "untitled-project",
        "my-project",
        "project",
        "watch",
        "view",
        "play",
    ):
        segments = [
            s
            for s in re.split(r"[/\\?=&]+", cleaned)
            if s
            and s.lower()
            not in (
                "http:",
                "https:",
                "www",
                "video",
                "source",
                "myfiless",
                "uploads",
                "id",
                "mp4",
                "mov",
                "view",
                "api",
            )
        ]
        if segments:
            candidate = re.sub(r"[^a-zA-Z0-9]+", "-", segments[-1]).strip("-")

    if not candidate or len(candidate) < 2 or candidate.lower().startswith("untitled"):
        return "proj"

    return candidate


def generate_project_name(
    source: str | None = None,
    existing_names: Collection[str] | None = None,
) -> str:
    """Generate a unique project name strictly between 10 and 15 characters inclusive."""
    used = set(existing_names) if existing_names is not None else set()
    base = derive_clean_base(source)

    for _ in range(500):
        hex_token = secrets.token_hex(4)  # 8 hex chars
        if len(base) >= 5:
            # Take up to 8 chars of base + '-' + 4 hex chars = up to 13 chars
            b = base[:8].strip("-")
            if len(b) < 3:
                b = "proj"
            cand = f"{b}-{hex_token[:4]}"
        else:
            # Base is 2-4 chars: pad with hex to achieve 10-12 chars
            needed_hex = 10 - len(base) - 1
            cand = f"{base}-{hex_token[:needed_hex]}"

        # Guarantee strict 10-15 character bound and uniqueness
        if 10 <= len(cand) <= 15 and cand not in used:
            return cand

    # Guaranteed fallback: 'p-' (2) + 8 hex chars = 10 chars
    return f"p-{secrets.token_hex(4)}"
