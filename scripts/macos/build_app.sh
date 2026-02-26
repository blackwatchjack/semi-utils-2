#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script only supports macOS."
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_NAME="semi-utils"
APP_PATH="${ROOT_DIR}/dist/${APP_NAME}.app"
BACKUP_DIR=""
BACKUP_APP_PATH=""

restore_previous_app_on_failure() {
  local exit_code=$?
  set +e
  if [[ "${exit_code}" -ne 0 && -n "${BACKUP_APP_PATH}" && -d "${BACKUP_APP_PATH}" ]]; then
    rm -rf "${APP_PATH}" 2>/dev/null || true
    mkdir -p "$(dirname "${APP_PATH}")"
    mv "${BACKUP_APP_PATH}" "${APP_PATH}"
    echo "Build failed; restored previous app bundle: ${APP_PATH}"
  fi
  if [[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]]; then
    rm -rf "${BACKUP_DIR}"
  fi
  return "${exit_code}"
}

trap restore_previous_app_on_failure EXIT

cd "${ROOT_DIR}"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m pip install pywebview

"${ROOT_DIR}/scripts/macos/fetch_exiftool.sh"

if [[ -d "${APP_PATH}" ]]; then
  BACKUP_DIR="$(mktemp -d "${ROOT_DIR}/.app-backup.XXXXXX")"
  BACKUP_APP_PATH="${BACKUP_DIR}/${APP_NAME}.app"
  mv "${APP_PATH}" "${BACKUP_APP_PATH}"
fi

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
  --collect-all "tkinterdnd2" \
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

if [[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]]; then
  rm -rf "${BACKUP_DIR}"
  BACKUP_DIR=""
  BACKUP_APP_PATH=""
fi

echo "Build completed."
echo "App bundle: ${APP_PATH}"
echo "Run with: open \"${APP_PATH}\""
