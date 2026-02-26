# macOS App Build

## 目标

产出可双击运行的 `semi-utils.app`，且打包过程可复现。

## 一键构建

```bash
./scripts/macos/build_app.sh
```

固定产物路径：

```text
dist/semi-utils.app
```

## 构建脚本做了什么

1. 仅允许在 macOS 运行。
1. 使用项目内 `.venv` 安装依赖、`pyinstaller` 与 `pywebview`。
1. 执行 `scripts/macos/fetch_exiftool.sh` 下载并校验 ExifTool。
1. 打包 `gui_app.py` 为 `.app`，并内置以下资源：
   - `fonts/`
   - `logos/`
   - `images/`
   - `third_party/exiftool/`
1. Tk 不兼容（如 macOS + Tk 8.5）时，优先在 app 内嵌 WebView 打开 Web GUI。

## ExifTool 自动来源与校验

- 版本来源：`https://exiftool.org/ver.txt`（可用 `EXIFTOOL_VERSION` 环境变量覆盖）
- 下载地址：`https://exiftool.org/Image-ExifTool-<version>.tar.gz`
- SHA256 来源：`https://exiftool.org/checksums.txt`（构建时动态解析并校验）
- 本地元信息文件：`third_party/exiftool/VERSION.txt`（含 version/url/sha256/checksum_source）

## 验收建议

1. 在未激活终端环境时，Finder 双击 `dist/semi-utils.app`。
1. 完成一次基础流程：选图、处理、输出。
1. 在 Tk 8.5 环境验证“安全 Web 模式”可在 app 内窗口提交并轮询任务（不应跳到 JSON 页）。
1. 出错时在临时目录查看日志：
   - `/tmp/semi-utils-desktop-<pid>.log`
