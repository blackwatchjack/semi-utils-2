#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "未找到项目虚拟环境 .venv，请先执行 ./install.sh"
  exit 1
fi

exec .venv/bin/python gui_app.py "$@"
