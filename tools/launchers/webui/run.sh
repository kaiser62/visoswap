#!/usr/bin/env bash
# Launcher web UI control.
#   ./run.sh start      docker compose up (127.0.0.1 only; WEBUI_BIND=0.0.0.0 for the LAN)
#   ./run.sh stop       docker compose down
#   ./run.sh native     run directly with python (recommended for Colab on macOS: its login is in the Keychain)
#   ./run.sh token      create the access token if missing (prints it once, only when created)
#   ./run.sh fake       native + fake launchers, scratch home (no credentials, no network)
#   ./run.sh bridge start|stop|status   host bridge so the container can use the Mac's Colab login (Keychain)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
home="${VISOSWAP_HOME:-$HOME/.visoswap}"
py="${VISOSWAP_PYTHON:-$here/../../../.venv/bin/python}"; [ -x "$py" ] || py=python3
ensure_token() {
  mkdir -p "$home"
  if [ ! -s "$home/webui_token" ]; then
    ( umask 077; "$py" -c 'import secrets;print(secrets.token_urlsafe(32))' > "$home/webui_token" )
    chmod 600 "$home/webui_token"
    echo "Access token (shown once; stored in $home/webui_token):"; cat "$home/webui_token"
  else
    echo "Access token exists in $home/webui_token (not shown again)."
  fi
}
bridge_pid="$home/bridge.pid"
bridge() {
  case "${1:-status}" in
    start) ensure_token >/dev/null
           if [ -f "$bridge_pid" ] && kill -0 "$(cat "$bridge_pid")" 2>/dev/null; then echo "bridge already running"; return; fi
           VISOSWAP_HOME="$home" BRIDGE_HOST="${BRIDGE_HOST:-0.0.0.0}" nohup "$py" "$here/bridge.py" >"$home/bridge.log" 2>&1 &
           echo $! >"$bridge_pid"; echo "bridge started (pid $(cat "$bridge_pid"), port ${BRIDGE_PORT:-8766}); log $home/bridge.log" ;;
    stop)  if [ -f "$bridge_pid" ]; then kill "$(cat "$bridge_pid")" 2>/dev/null || true; rm -f "$bridge_pid"; echo "bridge stopped"; else echo "bridge not running"; fi ;;
    *)     if [ -f "$bridge_pid" ] && kill -0 "$(cat "$bridge_pid")" 2>/dev/null; then echo "bridge running (pid $(cat "$bridge_pid"))"; else echo "bridge not running"; fi ;;
  esac
}
case "${1:-}" in
  start)  ensure_token; export HOST_UID="$(id -u)" HOST_GID="$(id -g)" VISOSWAP_HOME="$home"
          mkdir -p "$home/kaggle" "$home/home"
          if [ "$(uname)" = Darwin ] && [ "${WEBUI_BRIDGE:-1}" != 0 ]; then bridge start; export LAUNCHER_BRIDGE_URL="http://host.docker.internal:${BRIDGE_PORT:-8766}"; fi
          docker compose -f "$here/docker-compose.yml" up -d --build
          echo "UI: http://${WEBUI_BIND:-127.0.0.1}:${WEBUI_PORT:-8765}/" ;;
  stop)   docker compose -f "$here/docker-compose.yml" down; bridge stop ;;
  bridge) bridge "${2:-status}" ;;
  native) ensure_token; exec "$py" "$here/app.py" ;;
  fake)   home="${VISOSWAP_HOME:-$(mktemp -d)/vshome}"; export VISOSWAP_HOME="$home" KAGGLE_CONFIG_DIR="${KAGGLE_CONFIG_DIR:-$home/kaggle}"
          ensure_token; LAUNCHER_FAKE=1 exec "$py" "$here/app.py" ;;
  token)  ensure_token ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
