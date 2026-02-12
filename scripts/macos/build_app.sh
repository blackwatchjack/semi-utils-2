#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script only supports macOS."
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_NAME="semi-utils"
APP_PATH="${ROOT_DIR}/dist/${APP_NAME}.app"

cd "${ROOT_DIR}"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m pip install pywebview

"${ROOT_DIR}/scripts/macos/fetch_exiftool.sh"

chflags -R nouchg,noschg "${ROOT_DIR}/build" 2>/dev/null || true
chflags -R nouchg,noschg "${ROOT_DIR}/dist" 2>/dev/null || true
chmod -R u+w "${ROOT_DIR}/build" 2>/dev/null || true
chmod -R u+w "${ROOT_DIR}/dist" 2>/dev/null || true
rm -rf "${ROOT_DIR}/build" "${ROOT_DIR}/dist"

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "${APP_NAME}" \
  --collect-all "webview" \
  --add-data "fonts:fonts" \
  --add-data "logos:logos" \
  --add-data "images:images" \
  --add-data "third_party/exiftool:exiftool" \
  gui_app.py

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Build failed: app bundle not found at ${APP_PATH}"
  exit 1
fi

find "${APP_PATH}" -type f -path "*/exiftool/exiftool" -exec chmod +x {} \; || true

echo "Build completed."
echo "App bundle: ${APP_PATH}"
echo "Run with: open \"${APP_PATH}\""
