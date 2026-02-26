#!/usr/bin/env bash
set -euo pipefail

EXIFTOOL_BASE_URL="https://exiftool.org"
CHECKSUMS_URL="${EXIFTOOL_BASE_URL}/checksums.txt"
EXIFTOOL_VERSION="${EXIFTOOL_VERSION:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -f "inited" ]]; then
  echo "已完成初始化, 开始运行(如需重新初始化, 请删除 inited 文件)"
  exit 0
fi

if [[ -z "${EXIFTOOL_VERSION}" ]]; then
  EXIFTOOL_VERSION="$(curl -fsSL --retry 3 --retry-delay 1 "${EXIFTOOL_BASE_URL}/ver.txt" | tr -d '\r\n')"
fi

if [[ ! "${EXIFTOOL_VERSION}" =~ ^[0-9]+(\.[0-9]+)+$ ]]; then
  echo "Invalid ExifTool version: ${EXIFTOOL_VERSION}"
  exit 1
fi

EXIFTOOL_FILE_NAME="Image-ExifTool-${EXIFTOOL_VERSION}.tar.gz"
EXIFTOOL_FILE_DOWNLOAD_URL="${EXIFTOOL_BASE_URL}/${EXIFTOOL_FILE_NAME}"

expected_sha256="$(
  curl -fsSL --retry 3 --retry-delay 1 "${CHECKSUMS_URL}" \
    | awk -v archive="${EXIFTOOL_FILE_NAME}" '
      index($0, "SHA2-256(" archive ")=") {
        gsub(/\r/, "");
        print $NF;
        exit;
      }
    '
)"

if [[ -z "${expected_sha256}" ]]; then
  echo "未能在 checksums 中找到 ${EXIFTOOL_FILE_NAME} 的 SHA256"
  echo "checksum 来源: ${CHECKSUMS_URL}"
  exit 1
fi

# 下载文件
curl -fL --retry 3 --retry-delay 1 "${EXIFTOOL_FILE_DOWNLOAD_URL}" -o "${EXIFTOOL_FILE_NAME}"

actual_sha256="$(shasum -a 256 "${EXIFTOOL_FILE_NAME}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${expected_sha256}" ]]; then
  echo "ExifTool SHA256 校验失败"
  echo "expected: ${expected_sha256}"
  echo "actual:   ${actual_sha256}"
  exit 1
fi

# 测试 gzip 压缩的有效性
if ! gzip -t "${EXIFTOOL_FILE_NAME}"; then
  echo "下载的 ExifTool gzip 压缩文件格式不正确"
  echo "请检查 url 的有效性： ${EXIFTOOL_FILE_DOWNLOAD_URL}"
  echo "当前下载的 ExifTool gzip 的格式为："
  file "${EXIFTOOL_FILE_NAME}"
  echo "安装未完成，初始化脚本中断"
  exit 1
fi

# 创建目录
mkdir -p ./exiftool

# 解压文件
tar -xzf "${EXIFTOOL_FILE_NAME}" -C ./exiftool --strip-components=1

# 删除压缩包
rm -f "${EXIFTOOL_FILE_NAME}"

# 统一使用项目内 .venv 安装 Python 依赖
if [[ ! -x ".venv/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 初始化完成
touch inited
echo "初始化完成, inited 文件已生成, 如需重新初始化, 请删除 inited 文件"
exit 0
