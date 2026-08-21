"""VisoSwap -- Qt-free face-swap engine vendored from VisoMaster.

Nothing is imported eagerly here. Importing this package must never pull in
torch, onnxruntime, or any inference module: the Qt-reachability gate imports
leaf modules by name, and an eager __init__ would drag still-Qt-coupled modules
into the gate before they have been fixed.

See NOTICE for vendoring provenance and LICENSE for the full GPLv3 text.
"""

__version__ = "0.1.0"
