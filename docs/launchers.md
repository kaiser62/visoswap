# One-click GPU launchers (Kaggle, Colab) and the optional web UI

Two separate launchers start the VisoSwap backend on a free cloud GPU and print a stable URL
(web UI, `/mobile`). Python 3.9+ and the standard library only.

| | Kaggle | Colab |
|---|---|---|
| Command | `tools/launchers/kaggle-launch.sh` | `tools/launchers/colab-launch.sh` |
| Double-click (macOS) | `kaggle-launch.command` | `colab-launch.command` |
| Needs | `kaggle` CLI, logged in | `colab` CLI (`uv tool install google-colab-cli`), logged in |
| Default hostname | `swap.basic.int.eu.org` (tunnel `visoswap`) | `clswap.basic.int.eu.org` (tunnel `visoswap-colab`) |
| Config | `~/.visoswap/kaggle.env` | `~/.visoswap/colab.env` |
| URL saved to | `~/.visoswap/kaggle_url` (+ legacy `backend_url`) | `~/.visoswap/colab_url` |

`tools/kaggle_up.py` still works (thin wrapper around `kaggle_launch.py`, old flags kept).

## Flags (both)

`--setup` first-run walk-through / provision only - `--check` report logins, changes nothing - `--login` - `--store-cf-token` (reads stdin) - `--status` - `--restart` - `--down` (asks first; `--yes` skips) - `--dry-run`
(mutating commands are printed, never run; read-only queries still run) - `--json` (JSON lines, last line
is the result; used by the web UI) - `--tunnel auto|named|quick` - `--concurrency N|auto` - `--profile/--no-profile` -
`--no-clipboard`. Kaggle: `--max-minutes N`, `--accelerator t4x2|p100|<raw>`, `--models-dir`.
Colab: `--ref <branch|tag|sha>`, `--gpu T4|L4|...` (new sessions only), `--session NAME`, `--max-wait SECONDS`,
`--logs [uvicorn|tunnel|both]`, `--refresh-token`.
Env overrides: `VISOSWAP_HOME` (default `~/.visoswap`), `KAGGLE_BIN`, `COLAB_BIN`, `CLOUDFLARED_BIN`.

## Preflight, login and token: one click means the launcher owns it

Every launch runs the same preflight that `--check` reports and `--setup` walks through (idempotent; each step
checks before it acts). `--check` is read-only by construction (the command runner refuses any mutating call; no
lock, no files written) and exits non-zero if something blocking is wrong.

| Service | Checked | If it fails |
|---|---|---|
| Kaggle CLI | installed | prints `pip install kaggle` |
| Kaggle login | `kaggle kernels list --mine` probe; credentials rejected = *invalid* | in a terminal the launcher runs `kaggle auth login --force` and re-checks; otherwise it stops and prints that exact command |
| Kaggle quota | `kaggle quota` (shown as information) | "unknown": never blocks. The CLI exposes no free-GPU-slot count (limit: 2 GPU kernels) |
| Colab CLI | installed | prints `uv tool install google-colab-cli` |
| Colab login | stored Google login exists (macOS Keychain entry existence only, never its content), then `colab usage` | in a terminal the launcher runs `colab usage`: open the printed URL, sign in, paste the code; otherwise it prints that command |
| Colab units | `colab usage` balance, informational | 0 units = free tier, a T4 is not guaranteed; never blocks |
| Cloudflare | token + hostname stored in `~/.visoswap/<target>.env`, or (Colab) token already on the VM | order: stored token (never re-prompted, never overwritten) -> `CF_API_TOKEN` -> `cloudflared tunnel login` cert -> hidden prompt for a pasted tunnel token, stored mode 600 and never echoed -> quick tunnel |

A timeout or network error is reported as `unknown` and **never** starts a login. Prompts only happen on a real
terminal and never with `--json`, `--dry-run` or `--check` (so the web UI never hangs on a prompt). The web UI can
also pipe a pasted tunnel token to `--store-cf-token` (stdin); it refuses when one is already stored.
`--login` runs the login flow: Kaggle works from a non-interactive host (it opens the host's browser); Colab needs a
terminal because the CLI asks for a pasted code and keeps the result in the Keychain.

`--restart` keeps your tuning. Precedence for `GENERATION_CONCURRENCY`, `VISOSWAP_PROFILE` and the other whitelisted
settings: flags (`--concurrency N|auto`, `--profile/--no-profile`) > `colab.env` (`GENERATION_CONCURRENCY`,
`VISOSWAP_PROFILE`) > the environment of the running uvicorn (read from `/proc` before it is killed; only whitelisted
keys, and only they are printed) > `/content/visoswap_launch.json` from the last launch > defaults (profile off,
`VISOSWAP_PROCESS_WORKERS=auto`, concurrency auto). A restart never adds `VISOSWAP_PROCESS_WORKERS` if the old process
had none. The same logic runs when a launch restarts because the code changed.

## First-time setup

1. Cloudflare (once, per target). Pick one:
   - API token: put `CF_API_TOKEN=...` (Account: Cloudflare Tunnel Edit; Zone: DNS Edit, Zone Read) in the
     env file. The launcher creates a remote-managed tunnel with ingress `hostname -> http://localhost:8000`.
   - or `cloudflared tunnel login` (writes `~/.cloudflared/cert.pem`); the launcher creates/finds the tunnel,
     routes DNS and fetches the token.
   - or nothing: a `trycloudflare.com` quick tunnel is used (URL changes every launch; Kaggle has no log API, so
     you copy the URL from the kernel log page).
   Set `VISOSWAP_HOSTNAME` (and optionally `CF_ZONE`, `CF_TUNNEL_NAME`) to use your own domain. If token and
   hostname are already in the env file this step does nothing. Existing tunnels are never modified or deleted
   and DNS is never overwritten.
2. Kaggle: `pip install kaggle`, then `kaggle-launch.sh --login`. Colab: install the CLI, then `colab-launch.sh --login`
   (Google login, stored in the OS keychain). Both are also offered automatically on the first launch from a terminal.
3. `kaggle-launch.sh --setup` / `colab-launch.sh --setup` (or just launch; setup runs automatically).

## What each step does

Kaggle: CLI + auth probe -> kernel slug (`<user>/visoswap-studio`) -> Cloudflare -> model dataset
`<user>/visoswap-models` (if missing and `model_assets_owned/` exists locally, offers to create it; otherwise the
notebook downloads models itself, about 30 s) -> kernel status -> build a private bundle of
`visoswap_kaggle.ipynb` with the token injected (temp dir, deleted afterwards) -> push -> wait for RUNNING
(delete + re-push every 5 min while queued) -> wait for `/api/health` to answer `"status":"ok"` (about 5 min) -> save URL.
A kernel that is already running and healthy is reused; unknown status or expired auth aborts without touching it.

Colab: CLI + login -> reuse the session (`t4`, or the only existing one; **never creates a second one**) or request
a GPU with retries every 30 s up to `--max-wait` (re-checking `colab sessions` around every attempt) -> probe the VM
-> Cloudflare (a token already at `/content/cf_token` on the VM is reused) -> pip install only what is missing
(torch untouched; `onnxruntime-gpu` chosen by torch's CUDA major) -> git fetch/checkout the ref -> model bootstrap
-> upload token as a 600 file -> start uvicorn and cloudflared detached -> verify health through the public URL.
`--restart` restarts the two processes in the same session. `--down` is the only code path that runs `colab stop`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Kaggle CLI is not authenticated` | credentials expired: `tools/launchers/kaggle-launch.sh --login` |
| `kernel status ... was denied` | Kaggle's message for a kernel that does not exist; the launcher cross-checks `kernels list` and refuses to guess if they disagree |
| Colab: `no Google login stored` / login expired | run `tools/launchers/colab-launch.sh --login` in a terminal (it runs `colab usage`; paste the code; the launcher does this itself when run from a terminal); cannot be done from the web UI or Docker |
| `--check` says `unknown` | CLI timed out or no network; nothing was changed and no login was started: retry |
| Kernel QUEUED forever | launcher deletes and re-pushes every 5 min; otherwise Kaggle has no free GPU |
| Push fails / GPU session limit | max 2 GPU kernels per account; run `--down` on old ones, check phone verification |
| Kernel disappears after 90 min | `MAX_RUNTIME_MIN` guard; use `--max-minutes N` (0 = never) with `--restart` |
| Browser shows Cloudflare 1033 / 502 | tunnel or uvicorn still starting (about 5 min); health only counts when the body has `"status":"ok"` |
| `--concurrency/--profile` refused on a running kernel | they only apply to a new kernel: add `--restart` |
| Colab: no T4 after N minutes (`Service Unavailable`) | free T4s are scarce; retry later, raise `--max-wait`, or `--gpu L4` |
| Colab `no session has a GPU` | it was created as CPU: `--down` then launch |
| Colab "N sessions exist and none is named" | set `COLAB_SESSION` in `colab.env`; no second session is ever created |
| Colab remote step failed | steps are idempotent: re-run; `--logs both` for uvicorn/tunnel logs |
| `DNS record ... already points elsewhere` | the launcher never overwrites DNS; fix by hand or change `VISOSWAP_HOSTNAME` |
| Another launcher run in progress | per-target lock (`~/.visoswap/<target>.lock`), shared with the web UI |

## Web UI (optional)

`tools/launchers/webui/`: FastAPI + a single mobile-friendly page. Shows both targets (state, health, URL, copy),
buttons Launch / Restart / Provision / Stop, a login-status panel per service (Kaggle, Colab, Cloudflare) and a live log.
The panel shows each check; a failing login shows the exact command to run in a terminal, with a **Log in** button for
Kaggle when the UI runs natively (not in Docker, where `~/.kaggle` is read-only), and a hidden-input form to paste a
Cloudflare tunnel token (stored mode 600, never shown again, refused if one exists). It shells out to the same launchers (`--json`).

```
tools/launchers/webui/run.sh start      # Docker, http://127.0.0.1:8765  (stop: run.sh stop)
tools/launchers/webui/run.sh native     # without Docker (use this for Colab on macOS, see below)
tools/launchers/webui/run.sh fake       # fake launchers, no credentials (UI development)
make -C tools/launchers webui-up        # same as start
```
The access token is created on first start (`~/.visoswap/webui_token`, mode 600) and printed once; paste it into
the page (kept in the browser's localStorage; "Sign out" forgets it).

Phone access:
- LAN: `WEBUI_BIND=0.0.0.0 tools/launchers/webui/run.sh start` (native: `WEBUI_HOST=0.0.0.0`), open
  `http://<mac-lan-ip>:8765/`, enter the token. Plain HTTP: only on a network you trust.
- Away from home: put it behind a Cloudflare tunnel with **Cloudflare Access** (SSO) in front, keeping the bind
  on 127.0.0.1. Not set up by this repo.

Colab in Docker: the Colab CLI keeps its Google login in the macOS Keychain, which a Linux container cannot read.
So the containerised UI can manage Kaggle but Colab will report "not logged in". Run `run.sh native` for both
(or use the CLI's `--auth adc` with mounted Application Default Credentials, untested here).

### Threat model

The UI can spend GPU quota and its host holds credentials. Controls: bearer token on every `/api` route
(constant-time compare, header only, never in URLs); failed-auth lockout (10 per 5 min per IP) and 6 actions
per minute; one running action per target; fixed target/action allowlist (no arguments from the client); Stop
needs an explicit confirm; loopback bind by default; no endpoint returns env-file or credential contents and all
streamed output passes a redactor (known secret values, JWT/token shapes, `--token X`); CSP, `no-store`; container
runs as non-root, read-only root fs, all capabilities dropped, repo and `~/.kaggle` mounted read-only.
Not covered: TLS (use Cloudflare/Access or a trusted LAN), anyone with the token (full control), a compromised
host. `~/.visoswap` is mounted read-write because launchers save URLs and tokens there.

## Tests

`python -m pytest tests/launchers` (no network; fake `kaggle`/`colab` binaries in `tests/launchers/fakes`).
