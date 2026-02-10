# semi-utils

这是一个用于给照片批量添加水印、处理照片像素比、图像色彩和质量的处理引擎。本仓库已移除 CLI 菜单与 `config.yaml` 依赖，作为 GUI 集成的无状态处理模块使用。

**核心能力**
- 多种水印布局与样式
- 白边、阴影、等效焦距、按原比例填充、对焦距离显示
- EXIF 读取与保留
- 批量处理与可选输出目录
- 预览模式（临时文件、可指定最大边与质量）

**快速使用（GUI）**
```bash
./start_gui.sh
```

说明：
- 在支持 Tk 的环境会启动桌面 GUI。
- 若检测到 macOS + Tk 8.5 不兼容运行时，会自动切换到浏览器 GUI（`http://127.0.0.1:8765`）。

**快速使用（Web GUI）**
```bash
./start_web_gui.sh
```

GUI 支持：
- 批量选择图片与输出目录
- 核心布局/质量/白边/阴影/Logo 参数
- 文本元素选择与自定义文本
- 预览模式（生成临时预览图并可点击打开）
- 处理进度与错误日志

Web GUI 支持：
- 浏览器上传多张图片并处理
- 任务化处理（提交任务、轮询进度、完成后下载 ZIP）
- 下载处理结果 ZIP（含 `report.json` 与失败明细）
- 支持预览模式 ZIP 下载

Web API（供前端或外部调用）：
- `POST /api/process`：提交任务（multipart/form-data）
- `GET /api/jobs/<job_id>`：查询任务状态与进度
- `GET /api/jobs/<job_id>/download`：下载 ZIP 结果（任务完成后）
- `GET /health`：健康检查

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
# 应用正常退出时自动清理该日志文件
# 异常退出时，下一次启动会清理旧日志（默认保留 1 天）
```

**开发/测试**
- 本项目自带虚拟环境，运行脚本或测试前先激活：`source .venv/bin/activate`
- 若需要测试框架，请在虚拟环境内安装依赖（例如 `pytest`）

**布局列表**
通过 `engine.get_layout_specs()` 获取布局列表（id 与名称）。
