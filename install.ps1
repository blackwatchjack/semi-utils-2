Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$baseUrl = "https://exiftool.org"
$checksumsUrl = "$baseUrl/checksums.txt"
$version = $env:EXIFTOOL_VERSION
$pythonBin = if ([string]::IsNullOrWhiteSpace($env:PYTHON_BIN)) { "python" } else { $env:PYTHON_BIN }

if (Test-Path "inited") {
    Write-Host "已完成初始化, 开始运行(如需重新初始化, 请删除 inited 文件)"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($version)) {
    $version = (Invoke-RestMethod -Uri "$baseUrl/ver.txt").Trim()
}

if ($version -notmatch '^\d+(\.\d+)+$') {
    throw "Invalid ExifTool version: $version"
}

$fileName = "Image-ExifTool-$version.tar.gz"
$downloadUrl = "$baseUrl/$fileName"
$checksumsText = Invoke-RestMethod -Uri $checksumsUrl
$shaPattern = "SHA2-256\($([regex]::Escape($fileName))\)=\s*([0-9a-fA-F]{64})"
$shaMatch = [regex]::Match($checksumsText, $shaPattern)
if (-not $shaMatch.Success) {
    throw "未能在 checksums 中找到 $fileName 的 SHA256: $checksumsUrl"
}

$expectedSha256 = $shaMatch.Groups[1].Value.ToLowerInvariant()

# 下载文件
Invoke-WebRequest -Uri $downloadUrl -OutFile $fileName

$actualSha256 = (Get-FileHash -Path $fileName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    Remove-Item $fileName -ErrorAction SilentlyContinue
    throw "ExifTool SHA256 校验失败 expected=$expectedSha256 actual=$actualSha256"
}

# 创建目录
New-Item -ItemType Directory -Force -Path exiftool | Out-Null

# 解压文件
tar -xzf $fileName -C exiftool --strip-components=1

# 删除压缩包
Remove-Item $fileName

# 统一使用项目内 .venv 安装 Python 依赖
$venvPython = Join-Path ".venv" "Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
    & $pythonBin -m venv .venv
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 初始化完成
New-Item -ItemType File -Path inited -Force | Out-Null
Write-Host "初始化完成, inited 文件已生成, 如需重新初始化, 请删除 inited 文件"
exit 0
