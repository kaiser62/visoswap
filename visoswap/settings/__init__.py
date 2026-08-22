"""Three-tier settings storage and resolution.

``db.py`` holds the DDL. ``store.py`` holds the reads and writes and applies the
face/project/global/default chain. ``validate.py`` holds the rules every write
and every read is held to, all of them read out of the generated schema.
``faces.py`` turns a recognition embedding into the key the face tier is stored
under.

Every module here is standard library only and imports nothing outside
``visoswap.schema`` and this package -- no numpy, no torch, no Qt. Importing
settings must never drag the inference stack into a process that only wanted to
know what type a slider is.
"""
