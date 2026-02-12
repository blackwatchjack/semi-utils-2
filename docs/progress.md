# 进度

1. 已移除 CLI 交互与 `config.yaml` 依赖，项目变为无状态处理引擎。
1. 新增统一处理入口 `engine.process_images(...)`，以“文件列表 + 输出目录/映射”驱动处理。
1. 新增临时日志初始化与自动清理 `logging_setup.setup_temp_logging()`。
1. 已删除视频相关功能与依赖。
1. 已修复 `custom_watermark` 的 Logo 位置判断。
1. README 已更新为无状态引擎说明。
1. 新增预览模式支持（`process_images` 预览参数与回调，输出临时文件）。
1. 新增配置表单支持（`engine.get_config_spec()` 提供默认值与 schema）。
1. 新增异常退出后的日志清理策略（启动时清理旧日志，默认保留 1 天）。
1. 新增桌面 GUI 应用入口 `gui_app.py`（文件选择、参数配置、进度日志、预览列表）。
1. 新增浏览器 GUI 入口 `web_gui_app.py`（上传图片、下载 ZIP 结果）。
1. 桌面 GUI 增加 Tk 运行时兼容检测，不兼容时自动回退到 Web GUI。
1. Web GUI 改为任务化 API（任务提交、进度轮询、完成下载）。
1. Web GUI 增加上传大小/格式校验与任务结果清理机制。
1. 增加 GUI 一键启动脚本（`start_gui.sh`、`start_web_gui.sh`）。
1. 修复 GPS 经纬度分支调用参数错误，避免特定 EXIF 下崩溃。
1. 新增测试覆盖（核心 API、GPS、GUI 回退判定、Web API 端到端）。
1. 已修复 CI 失效配置，改为跨平台测试流水线。
1. 新增运行时资源路径解析（源码运行与 PyInstaller 运行统一处理）。
1. ExifTool 解析逻辑升级为“环境变量 > 内置资源 > 系统 PATH”，缺失时降级并告警。
1. 新增 macOS 一键打包脚本（`scripts/macos/build_app.sh`）与 ExifTool 固定下载校验脚本（`scripts/macos/fetch_exiftool.sh`）。
1. 新增 macOS 打包文档 `docs/macos_app.md`，明确产物路径与验收流程。
1. 桌面 GUI 参数与状态文案已中文化，提升可用性。
1. Tk 不兼容场景回退升级为“app 内嵌 WebView 优先，浏览器兜底”。
1. 修复内嵌 WebView 下任务提交后跳 JSON 页的问题（前端脚本降级为兼容模式）。
