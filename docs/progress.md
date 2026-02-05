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
