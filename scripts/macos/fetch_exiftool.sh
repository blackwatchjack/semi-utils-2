#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXIFTOOL_VERSION="13.50"
EXIFTOOL_SHA256="27e2d66eb21568cc0d59520f89afcaaa50735e1ad9fa4b36d0a4ccf916c70d31"
EXIFTOOL_ARCHIVE="Image-ExifTool-${EXIFTOOL_VERSION}.tar.gz"
EXIFTOOL_URL="https://exiftool.org/${EXIFTOOL_ARCHIVE}"
TARGET_DIR="${ROOT_DIR}/third_party/exiftool"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

archive_path="${tmp_dir}/${EXIFTOOL_ARCHIVE}"
echo "Downloading ${EXIFTOOL_URL}"
curl -fL --retry 3 --retry-delay 1 "${EXIFTOOL_URL}" -o "${archive_path}"

actual_sha256="$(shasum -a 256 "${archive_path}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXIFTOOL_SHA256}" ]]; then
  echo "ExifTool SHA256 mismatch"
  echo "expected: ${EXIFTOOL_SHA256}"
  echo "actual:   ${actual_sha256}"
  exit 1
fi

rm -rf "${TARGET_DIR}"
mkdir -p "${TARGET_DIR}"
tar -xzf "${archive_path}" -C "${TARGET_DIR}" --strip-components=1
chmod +x "${TARGET_DIR}/exiftool" || true

# ExifTool test suite contains binary fixtures (e.g. t/images/EXE.macho)
# that can break PyInstaller's binary processing on macOS. They are not
# needed at runtime, so prune them from the bundled payload.
rm -rf "${TARGET_DIR}/t"

cat > "${TARGET_DIR}/VERSION.txt" <<EOF
version=${EXIFTOOL_VERSION}
url=${EXIFTOOL_URL}
sha256=${EXIFTOOL_SHA256}
EOF

echo "ExifTool prepared at ${TARGET_DIR}"
