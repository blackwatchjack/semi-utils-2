#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python web_gui_app.py "$@"
fi

exec python3 web_gui_app.py "$@"
