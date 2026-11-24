#!/usr/bin/env bash
# Launcher web UI control.
#   ./run.sh start      docker compose up (127.0.0.1 only; WEBUI_BIND=0.0.0.0 for the LAN)
#   ./run.sh stop       docker compose down
#   ./run.sh native     run directly with python (recommended for Colab on macOS: its login is in the Keychain)
#   ./run.sh token      create the access token if missing (prints it once, only when created)
#   ./run.sh fake       native + fake launchers (no credentials, no network)
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
case "${1:-}" in
  start)  ensure_token; export HOST_UID="$(id -u)" HOST_GID="$(id -g)" VISOSWAP_HOME="$home" KAGGLE_DIR="${KAGGLE_DIR:-$HOME/.kaggle}" COLAB_CONFIG_DIR="${COLAB_CONFIG_DIR:-$HOME/.config/colab-cli}"
          mkdir -p "$COLAB_CONFIG_DIR" "$KAGGLE_DIR"
          docker compose -f "$here/docker-compose.yml" up -d --build
          echo "UI: http://${WEBUI_BIND:-127.0.0.1}:${WEBUI_PORT:-8765}/" ;;
  stop)   docker compose -f "$here/docker-compose.yml" down ;;
  native) ensure_token; exec "$py" "$here/app.py" ;;
  fake)   ensure_token; LAUNCHER_FAKE=1 exec "$py" "$here/app.py" ;;
  token)  ensure_token ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
