"""Model metadata, download and integrity checking, vendored from VisoMaster.

``models_data`` comes from upstream's ``app/processors/models_data.py`` and
``downloader``/``integrity_checker`` from ``app/helpers``. They live together
here because they are one concern -- what the model set is, where it comes
from, and whether the bytes on disk are the right ones -- and because the
design puts model data outside ``processors``.

Intentionally empty of imports, for the same reason
``visoswap/processors/__init__.py`` is: eager package imports would pull
modules into the Qt-reachability gate before plans 01-03 and 01-04 have fixed
them.
"""
