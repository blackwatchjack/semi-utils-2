# Todo List

## P0（已完成）

1. 已产出可直接双击运行的 macOS `.app`（`dist/semi-utils.app`）。
验收结果：在未激活终端环境下可启动；基础流程（选图、处理、输出）可完成；失败时有可定位日志。
1. 已固化 `.app` 打包流水线（PyInstaller）。
验收结果：仓库内提供单命令脚本 `scripts/macos/build_app.sh`；产物路径固定；已包含字体、logos、默认资源与运行依赖。
1. 已解决 `.app` 运行时依赖内置（重点 ExifTool 与 Python 依赖）。
验收结果：`.app` 不依赖用户全局 `python/pip`；ExifTool 来源可追溯且版本固定；缺失 ExifTool 不导致启动崩溃（降级并记录日志）。
1. 已补充 Tk 不兼容场景的无风险回退路径。
验收结果：macOS + Tk 8.5 下优先走 app 内嵌 WebView；仅在内嵌不可用时回退系统浏览器。

## P1（近期）

1. 建立批量处理性能基线。
验收标准：提供固定样本的基准脚本或测试，输出处理耗时/吞吐，便于后续优化对比。

## P2（优化）

1. 将性能基线结果接入 CI 可选流水线（手动触发），用于版本间对比。
验收标准：提供独立 workflow，不阻塞主 CI。

## 已确认

1. 不需要额外的“引擎并发处理参数”（当前由调用方控制并发）。

## 已完成

1. 已移除 CLI 与 `config.yaml` 依赖，项目改为无状态处理引擎。
1. 已明确 GUI 调用约定（进度回调、错误模型、预览模式）。
1. 已支持预览模式（临时文件、`max_size/quality`、不保留 EXIF、调用方清理）。
1. 已提供配置表单支持（`engine.get_config_spec()`）。
1. 已增加启动时清理旧日志策略（异常退出场景，默认保留 1 天）。
1. 已提供桌面 GUI 与 Web GUI 入口（含一键脚本）。
1. 已提供 Web GUI 任务化进度、取消、下载与并发限流。
1. 已补齐核心 API / GPS / GUI 回退 / Web API 测试并接入 CI。
1. 已新增 macOS 打包文档（`docs/macos_app.md`）与 ExifTool 来源固化说明。
1. 已完成桌面 GUI 参数中文化（含状态、提示、弹窗文案）。
1. 已修复内嵌 WebView 下任务提交跳 JSON 页问题（前端脚本兼容降级）。
1. 已统一运行环境约束（安装/启动脚本与文档默认使用项目内 `.venv`，不再混用全局 `python/pip3`）。
1. 已补齐 `engine.process_images(...)` 核心行为测试（输出优先级、回调、预览 EXIF 行为）。
1. 已补齐 Web 任务生命周期异常路径测试（下载前校验、任务过期清理、取消一致性）。
1. 已完成 Web 关键阈值参数化（`MAX_FILES`、`MAX_REQUEST_BYTES`、`MAX_FILE_BYTES`、`JOB_TTL_SECONDS` 支持环境变量覆盖并补充文档）。
