import pytest
from backend.services.naming import generate_project_name, derive_clean_base


def test_derive_clean_base_from_urls():
    url1 = "https://cdn.kamababa1.com/2026/07/Sexy-Mumbai-girl-records-her-multiple-Indian-nude-MMS.mp4"
    assert "Sexy-Mumbai" in derive_clean_base(url1)

    url2 = "https://server18.mmsbee1.xyz/uploads/myfiless/id/48437.mp4"
    assert "48437" in derive_clean_base(url2)

    url3 = "https://example.com/watch?v=dQw4w9WgXcQ"
    assert "dQw4w9WgXcQ" in derive_clean_base(url3)


def test_generate_project_name_bounds_and_uniqueness():
    used = set()
    sources = [
        "https://cdn.kamababa1.com/2026/07/Sexy-Mumbai-girl.mp4",
        "https://server18.mmsbee1.xyz/uploads/myfiless/id/48437.mp4",
        "https://cdn.xxxindianstories.com/0/48/48.mp4",
        "my_short_clip.mov",
        "a.mp4",
        "",
        None,
        "Untitled project",
        "video.mp4",
    ]

    for src in sources:
        name = generate_project_name(src, used)
        used.add(name)
        assert 10 <= len(name) <= 15, f"Length outside 10-15 chars: '{name}' ({len(name)})"
        # Only letters, digits, and hyphens
        assert all(c.isalnum() or c == "-" for c in name), f"Invalid characters in '{name}'"

    # Test collision avoidance
    fixed_src = "sample-video.mp4"
    for _ in range(50):
        name = generate_project_name(fixed_src, used)
        assert name not in used
        assert 10 <= len(name) <= 15
        used.add(name)
