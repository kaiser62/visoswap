#!/usr/bin/env bash
# One-click kaggle launcher. Passes every flag through (try --dry-run first).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${VISOSWAP_PYTHON:-}"
if [ -z "$py" ]; then
  for c in "$here/../../.venv/bin/python" python3 python; do
    if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then py="$c"; break; fi
  done
fi
exec "$py" "$here/kaggle_launch.py" "$@"
