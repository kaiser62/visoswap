"""The frame-processing worker, vendored from VisoMaster's ``app/processors/workers``.

Upstream has no ``__init__.py`` here -- ``app/processors/workers`` is an implicit
namespace package. VisoSwap adds this file so the module is a regular package
member and the Qt gate's ``visoswap.processors.workers.frame_worker`` name
resolves the same way on every interpreter.

Intentionally empty of imports. ``frame_worker`` pulls in torch, torchvision and
skimage, and importing a package should never cost that.
"""
