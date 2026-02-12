# Todo List

1. 可选：恢复/重建可分发打包流程（如 PyInstaller/安装包）并在 CI 发布产物。

## 已确认

1. 不需要并发处理参数。

## 已完成

1. 已明确 GUI 调用约定（进度回调、错误模型、预览模式）。
1. 已支持预览模式（临时文件、`max_size/quality`、不保留 EXIF、调用方清理）。
1. 已提供配置表单支持（`engine.get_config_spec()`）。
1. 已增加启动时清理旧日志策略（异常退出场景，默认保留 1 天）。
1. 已提供可直接运行的桌面 GUI 入口（`python gui_app.py`）。
1. 已提供可直接运行的浏览器 GUI 入口（`python web_gui_app.py`）。
1. 已提供 Web GUI 任务化进度查询与下载接口（`/api/jobs/<id>`）。
1. 已提供 Web GUI 任务取消与并发限流（`POST /api/jobs/<id>/cancel`，运行并发可配置）。
1. 已提供 GUI 一键启动脚本（`start_gui.sh`、`start_web_gui.sh`）。
1. 已补齐 GUI 配置项可视化（颜色/粗体/字体路径/字体尺寸）覆盖 `get_config_spec()` 全字段。
