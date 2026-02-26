# semi-utils

这是一个用于给照片批量添加水印、处理照片像素比、图像色彩和质量的处理引擎。本仓库已移除 CLI 菜单与 `config.yaml` 依赖，作为 GUI 集成的无状态处理模块使用。

**核心能力**
- 多种水印布局与样式
- 白边、阴影、等效焦距、按原比例填充、对焦距离显示
- EXIF 读取与保留
- 批量处理与可选输出目录
- 预览模式（临时文件、可指定最大边与质量）

**初始化（统一使用项目内 `.venv`）**
```bash
./install.sh
```

**快速使用（GUI）**
```bash
./start_gui.sh
```

说明：
- 启动脚本固定使用项目内 `.venv`。
- 在支持 Tk 的环境会启动桌面 GUI。
- 若检测到 macOS + Tk 8.5 不兼容运行时，会自动切换到“安全 Web 模式”：
  - 优先在 `.app` 内打开内嵌 WebView 窗口。
  - 若内嵌窗口不可用，则回退到系统浏览器（`http://127.0.0.1:8765`）。

**快速使用（Web GUI）**
```bash
./start_web_gui.sh
```

**macOS `.app` 打包（P0）**
```bash
./scripts/macos/build_app.sh
```

说明：
- 单命令产出 `dist/semi-utils.app`
- 构建脚本会自动安装 PyInstaller（在项目 `.venv` 中）
- 构建脚本会自动安装 `pywebview`（用于 Tk 不兼容场景的 app 内嵌 Web 模式）
- 构建时自动获取最新 ExifTool 版本并从官方 `checksums.txt` 动态校验 SHA256
- 打包产物内置字体、logos、images、ExifTool，无需依赖系统 `python/pip`
- 详细步骤见 `docs/macos_app.md`

GUI 支持：
- 批量选择图片与输出目录
- 左侧栏支持拖拽添加图片（追加并去重，自动过滤无效路径/格式）
- 三栏界面（图片缩略图 / 处理后预览 / 参数区），支持预览缩放与切换
- 核心布局/质量/白边/阴影/Logo 参数
- 文本元素选择与自定义文本（含颜色、粗体）
- 字体配置（字体大小级别、字体路径、备用字体路径）
- 参数可见性联动（布局切换时隐藏字段自动回落默认值）
- 预览模式（生成临时预览图并可点击打开）
- 处理进度与错误日志

Web GUI 支持：
- 浏览器上传多张图片并处理
- 左侧栏支持整块区域拖拽添加图片（追加并去重，前端过滤格式/大小/数量并提示）
- 三栏 Web 界面（输入缩略图 / 处理后预览 / 参数配置）
- 任务化处理（提交任务、轮询进度、完成后下载 ZIP）
- 任务轮询中可逐步查看可用结果（`results_available`）
- 支持按输入索引查看单图结果预览
- 参数可见性接口驱动的前后端一致行为（`POST /api/visibility`）
- 任务取消（运行中/排队中任务）
- 任务并发限流（默认最多 2 个并发处理任务）
- 下载处理结果 ZIP（仅含处理后的图片文件，位于 ZIP 根目录）
- 支持预览模式 ZIP 下载

Web API（供前端或外部调用）：
- `POST /api/process`：提交任务（multipart/form-data）
- `GET /api/jobs/<job_id>`：查询任务状态与进度（含 `results_available`）
- `GET /api/jobs/<job_id>/results/<index>`：按输入顺序获取单张处理结果图
- `POST /api/jobs/<job_id>/cancel`：取消任务
- `GET /api/jobs/<job_id>/download`：下载 ZIP 结果（任务完成后）
- `POST /api/visibility`：返回字段可见性与隐藏字段重置后的配置
- `GET /health`：健康检查
任务状态包含：`queued`、`waiting`、`running`、`cancelling`、`cancelled`、`done`、`error`。

Web 运行阈值支持环境变量覆盖（括号内为默认值）：
- `SEMI_WEB_MAX_FILES`（`200`）
- `SEMI_WEB_MAX_REQUEST_BYTES`（`536870912`，即 `512MB`）
- `SEMI_WEB_MAX_FILE_BYTES`（`67108864`，即 `64MB`）
- `SEMI_WEB_JOB_TTL_SECONDS`（`1800`，即 `30` 分钟）
- `SEMI_WEB_MAX_CONCURRENT_JOBS`（`2`，最小 `1`，最大 `16`）

**当前进度（截至 2026-02-25）**
- 已完成无状态化改造：移除 CLI 与 `config.yaml` 依赖，统一入口为 `engine.process_images(...)`
- 已完成桌面 GUI + Web GUI 双入口及一键启动脚本（`start_gui.sh`、`start_web_gui.sh`）
- 已完成 Web 任务化流程：提交、轮询、取消、下载 ZIP，并支持并发限流
- 已完成 macOS `.app` 打包流水线与 Tk 不兼容场景“内嵌 WebView 优先、浏览器兜底”安全回退
- 已完成桌面/Web 三栏 UI 重构，支持按输入索引查看单图结果预览
- 已完成参数可见性联动与隐藏字段默认值回落（`ui_visibility.sanitize_config`、`POST /api/visibility`）
- 已补齐相关测试覆盖：核心 API、GUI 回退、Web API、可见性规则、EXIFTool 缺失降级等
- 已补齐 `engine.process_images(...)` 核心行为测试（输出路径优先级、回调、预览 EXIF 行为）
- 已补齐 Web 生命周期异常路径测试（下载前校验、任务过期清理、取消一致性）
- 已统一开发/运行脚本默认使用项目内 `.venv`
- 已支持 Web 关键阈值环境变量覆盖（含默认值文档）

**快速使用（Python 调用）**
```python
from engine import process_images

# inputs: 文件路径列表
# output_dir: 批量输出目录；不传则默认输出到原图同目录
process_images(
    inputs=["/path/to/a.jpg", "/path/to/b.jpg"],
    output_dir="/path/to/output",
)
```

**接口约定**
详见 `docs/api.md`。

**配置方式**
- 通过 `config_data` 传入配置（字典），不落盘
- 未提供的字段会使用默认值
- GUI 可通过 `engine.get_config_spec()` 获取默认值与表单 schema

```python
from engine import process_images

config_data = {
    "layout": {"type": "watermark_right_logo"},
    "global": {"shadow": {"enable": True}},
    "base": {"quality": 95},
}

process_images(
    inputs=["/path/to/a.jpg"],
    config_data=config_data,
)
```

**预览模式**
```python
from engine import process_images

preview_paths = []

def on_preview(src, preview_path):
    preview_paths.append(preview_path)

process_images(
    inputs=["/path/to/a.jpg"],
    preview=True,
    preview_max_size=1600,
    preview_quality=80,
    on_preview=on_preview,
)
# preview_paths 里是临时文件路径，调用方负责清理
```

**日志**
```python
from logging_setup import setup_temp_logging

log_path = setup_temp_logging()
# 默认应用正常退出时自动清理该日志文件
# 异常退出时，下一次启动会清理旧日志（默认保留 1 天）
```

桌面 GUI / Web GUI 默认会保留运行日志（便于定位失败）：
- 桌面 GUI：`/tmp/semi-utils-desktop-<pid>.log`
- Web GUI：`/tmp/semi-utils-web-<pid>.log`

**近期计划（P1）**
- 建立批量处理性能基线（固定样本、耗时与吞吐输出）

**开发/测试**
- 建议统一使用项目内 `.venv`：
  - `./install.sh`
  - `.venv/bin/python -m pytest -q`

**布局列表**
通过 `engine.get_layout_specs()` 获取布局列表（id 与名称）。
