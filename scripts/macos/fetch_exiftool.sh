#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXIFTOOL_BASE_URL="https://exiftool.org"
CHECKSUMS_URL="${EXIFTOOL_BASE_URL}/checksums.txt"
EXIFTOOL_VERSION="${EXIFTOOL_VERSION:-}"
TARGET_DIR="${ROOT_DIR}/third_party/exiftool"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

if [[ -z "${EXIFTOOL_VERSION}" ]]; then
  EXIFTOOL_VERSION="$(curl -fsSL --retry 3 --retry-delay 1 "${EXIFTOOL_BASE_URL}/ver.txt" | tr -d '\r\n')"
fi

if [[ ! "${EXIFTOOL_VERSION}" =~ ^[0-9]+(\.[0-9]+)+$ ]]; then
  echo "Invalid ExifTool version: ${EXIFTOOL_VERSION}"
  exit 1
fi

EXIFTOOL_ARCHIVE="Image-ExifTool-${EXIFTOOL_VERSION}.tar.gz"
EXIFTOOL_URL="${EXIFTOOL_BASE_URL}/${EXIFTOOL_ARCHIVE}"

EXIFTOOL_SHA256="$(
  curl -fsSL --retry 3 --retry-delay 1 "${CHECKSUMS_URL}" \
    | awk -v archive="${EXIFTOOL_ARCHIVE}" '
      index($0, "SHA2-256(" archive ")=") {
        gsub(/\r/, "");
        print $NF;
        exit;
      }
    '
)"

if [[ -z "${EXIFTOOL_SHA256}" ]]; then
  echo "Failed to find SHA256 for ${EXIFTOOL_ARCHIVE} in ${CHECKSUMS_URL}"
  exit 1
fi

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
checksum_source=${CHECKSUMS_URL}
EOF

echo "ExifTool prepared at ${TARGET_DIR}"
