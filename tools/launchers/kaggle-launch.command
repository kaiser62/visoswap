#!/usr/bin/env bash
# Double-click in Finder (macOS). Keeps the window open so the result stays readable.
"$(cd "$(dirname "$0")" && pwd)/kaggle-launch.sh" "$@"
rc=$?
echo; read -r -p "Press Return to close... " _ || true
exit $rc
