"""CLIPseg and the trimmed CLIP library, vendored from VisoMaster's ``app/processors/external``.

Upstream has no ``__init__.py`` here -- it relies on namespace packages. This
file exists so the subtree is an explicit package, and it is written by this
project rather than copied, which is why it carries no attribution header.

Intentionally empty of imports. ``clipseg`` pulls in ``torch`` and ``cliplib``,
so an eager re-export would drag the whole CLIP stack into every import of
``visoswap.processors`` -- including the Qt-reachability gate, which imports
each module deliberately and one at a time.
"""
