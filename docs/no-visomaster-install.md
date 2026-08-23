# No VisoMaster install required

Plan 04-04 severs every dependency this repository has on a VisoMaster install
being present, and proves the severance at runtime rather than by grep. It closes
`ENGINE-01`, which had been `Pending` since Phase 2 with its first clause proven
and its second false.

## The five dependencies as found

The phase brief named three; measurement found five. Each is listed with what it
was and what it became.

1. **The weights.** `visoswap/models/models_data.py:6` was `models_dir =
   './model_assets'`, and `model_assets` is a junction whose real path is
   `D:\VisoMaster\model_assets` — all 56 required model files physically lived
   inside the VisoMaster install. **Now:** `models_dir` reads `MODELS_DIR` with a
   project-owned default (`model_assets_owned/`), a verified copy made by
   `tools/copy_model_assets.py` and gitignored. The change is a single named edit
   site with a provenance comment; all 62 tracked paths follow it for free.

2. **The engine interpreter default.** `tests/conftest.py` defaulted the engine
   interpreter to `D:/Visomaster/dependencies/Python/python.exe`. **Now:** it
   defaults to the project's own combined interpreter, `.venv-clean/Scripts/python.exe`,
   which carries the full inference stack and no PySide6 — so the default run
   proves ENGINE-01 clause 1 (the engine runs where Qt is genuinely absent). The
   Qt-carrying run remains reachable by pointing `VISOSWAP_ENGINE_PYTHON` at a
   Qt-bearing interpreter (see `docs/engine-test-assets.md` for the command).

3. **The seal's non-inertness proof.** `tests/test_engine_seal.py` asserted
   `app=yes` — that VisoMaster's `app` package genuinely resolved before the seal
   armed — which depended on a VisoMaster checkout being on `sys.path`. **Now:**
   the runner builds a minimal self-contained `app` package and puts it on
   `sys.path`, so the proof holds on any machine with no VisoMaster install. A
   real checkout via `VISOMASTER_DIR` is still the sharper run.

4. **The test media.** `tests/_engine_runner.py` and `tests/_backend_runner.py`
   defaulted the smoke video and source face to `D:/Visomaster/output` and
   `D:/Visomaster/inputt`. **Now:** they default to `tests/media/`, a project-owned,
   gitignored directory (personal media, never committed, T-02-10). A missing file
   stays `ASSET_MISSING (3)`, never a skip.

5. **The face-source picker on the product path (found, not in the brief).**
   The reference `config-parallel-setup` listed face sources from
   `<visomaster_dir>/inputt` and served thumbnails from the same folder — a
   shipped feature reading someone else's install, live. The 04-01 port had
   already replaced this with a **server-assigned `source_face_path` column** on
   the project row, stripped from the public API, so no face source is read from a
   VisoMaster folder. This plan verified that: no `inputt` and no `visomaster_dir`
   reference remains on the product path (asserted by
   `tests/test_no_visomaster_install.py`'s literal scan).

## The junction is left deliberately in place

`model_assets/` still resolves into `D:/Visomaster`. It is **not** removed,
because removing it is the one action in this phase that cannot be undone by hand,
and it costs nothing to leave — what made clause 2 false was the *default pointing
at it*, not the junction's existence. The project-owned set is a verified copy;
nothing ever writes, moves, renames or deletes anything under the junction's
target.

## The `islink`-versus-`realpath` trap

`os.path.islink('model_assets')` returns **`False`** on this machine while
`os.path.realpath` returns `D:\VisoMaster\model_assets`. It is a Windows junction
created by `mklink /J`, not a symlink. Any static severance check written against
link-ness would report clean while every swap read out of the borrowed tree. Only
a check on the *resolved* path sees it, so every assertion in
`tests/test_no_visomaster_install.py` is written against `realpath`, never
`islink`.

## The runtime open guard

A grep proves no *literal* names a VisoMaster path. Only a guard watching real
opens proves no *resolved* path reaches one. `tests/_open_guard.py` wraps the
builtin `open` and low-level `os.open` for the duration of a run, resolves every
path they receive, and raises when one lands inside a forbidden root. The
`--no-visomaster` modes of `_engine_runner.py` and `_backend_runner.py` run a real
swap / a real generation under it.

**Non-vacuity has two halves, both mandatory:**

- **The guard reports how many opens it observed.** The engine no-visomaster swap
  reported `guard_opens=11` and the backend tracer `guard_opens=67`; the test
  asserts the count is non-zero, so a guard that saw nothing cannot pass.
- **The guard is shown capable of firing.** `_open_guard.assert_capable_of_firing`
  opens a file inside a freshly planted forbidden directory and asserts the guard
  raises — a guard with a broken comparison is indistinguishable from a clean run.

## The two clause measurements (ENGINE-01, stated together)

**Clause 1, re-measured not inherited:** on `.venv-clean` (the combined
interpreter), `pip show PySide6` reports `Package(s) not found: PySide6`, and the
no-visomaster swap completes (provider CUDA, `reachable_before_seal` shows
`PySide6=no`, `app=yes` from the self-built subject).

**Clause 2, newly true:** the same swap and a full generation request complete
with the open guard armed against every VisoMaster root, having observed real
opens (`guard_opens=11` / `67`) and having been shown capable of firing.

```
$ .venv-clean/Scripts/python.exe -m pip show PySide6        # "not found" -> clause 1
$ .venv-clean/Scripts/python.exe -B tests/_engine_runner.py --no-visomaster
  CLEAN:smoke:... provider=CUDA ... PySide6=no,app=yes ... guard_opens=11
$ .venv-clean/Scripts/python.exe -B tests/_backend_runner.py --tracer --no-visomaster
  CLEAN:tracer:frame=000000.000.jpg ... PySide6=no ... guard_opens=67
```

## What is still true

A developer **may** still point `MODELS_DIR`, `VISOMASTER_DIR`,
`VISOSWAP_ENGINE_PYTHON` and the media overrides at a VisoMaster checkout. The
requirement is that the project does not *need* one, not that it refuses to use
one. Every severance is a default change plus an environment variable, so the old
configuration is one export away.
