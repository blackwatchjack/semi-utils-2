# API 约定

## 处理入口

函数签名（现有实现）：

```python
engine.process_images(
    inputs,
    config_data=None,
    output_dir=None,
    output_map=None,
    on_progress=None,
    on_error=None,
    preview=False,
    preview_max_size=None,
    preview_quality=None,
    preview_dir=None,
    on_preview=None,
) -> list[tuple[Path, Exception]]
```

配置表单支持（新增）：

```python
engine.get_config_spec() -> dict
```

## Web GUI API（补充）

1. `POST /api/process`：提交任务，返回 `job_id`。
1. `GET /api/jobs/<job_id>`：查询任务状态与进度。
1. `POST /api/jobs/<job_id>/cancel`：取消任务（运行中/等待中/排队中）。
1. `GET /api/jobs/<job_id>/download`：任务完成后下载 ZIP。
1. 任务状态包含：`queued`、`waiting`、`running`、`cancelling`、`cancelled`、`done`、`error`。

## 入参与行为

1. `inputs`: 文件列表（字符串或 Path）。按顺序处理，进度顺序与输入顺序一致。
1. `config_data`: 配置字典（可选），传入 `Config` 构造。
1. `output_dir`: 统一输出目录（可选）。
1. `output_map`: 单独输出映射（可选），键为输入路径，值为输出路径。
1. 输出路径优先级：`output_map` > `output_dir` > 原图同目录。
1. 重名策略：默认覆盖。
1. 处理为同步阻塞调用；GUI 侧应在后台线程/任务中调用以避免阻塞 UI。
1. `preview`: 预览模式（可选）。为 `True` 时输出临时文件路径，`output_dir/output_map` 将被忽略。
1. `preview_max_size`: 预览输出最长边限制（像素，等比缩放）。
1. `preview_quality`: 预览输出质量（覆盖配置中的质量）。
1. `preview_dir`: 预览文件输出目录（可选，不传则使用系统临时目录）。
1. 预览输出不保留 EXIF，临时文件由调用方负责清理。

## 配置表单支持

`engine.get_config_spec()` 返回一个包含默认值与表单 schema 的字典，便于 GUI 自动生成配置界面。

返回结构：

1. `version`: schema 版本号。
1. `defaults`: 完整默认配置（可直接作为 `config_data`）。
1. `groups`: 字段分组（可选使用）。
1. `enums`: 枚举值（布局、位置、元素类型、字号等级）。
1. `fields`: 字段定义（`path`、`type`、`label`、`enum_ref`、`min/max/step`、`visible_when` 等）。

示例（截断，仅展示结构与部分字段）：

```jsonc
{
  "version": 1,
  "defaults": {
    "base": {
      "quality": 100,
      "font_size": 1,
      "bold_font_size": 1,
      "font": "./fonts/AlibabaPuHuiTi-2-45-Light.otf",
      "bold_font": "./fonts/AlibabaPuHuiTi-2-85-Bold.otf",
      "alternative_font": "./fonts/Roboto-Regular.ttf",
      "alternative_bold_font": "./fonts/Roboto-Medium.ttf"
    },
    "global": {
      "shadow": {"enable": false},
      "white_margin": {"enable": true, "width": 3},
      "padding_with_original_ratio": {"enable": false},
      "focal_length": {"use_equivalent_focal_length": false}
    },
    "layout": {
      "type": "watermark_right_logo",
      "background_color": "#ffffff",
      "logo_enable": false,
      "logo_position": "left",
      "elements": {
        "left_top": {"name": "LensModel", "color": "#212121", "is_bold": true},
        "left_bottom": {"name": "Model", "color": "#757575", "is_bold": false},
        "right_top": {"name": "Param", "color": "#212121", "is_bold": true},
        "right_bottom": {"name": "Datetime", "color": "#757575", "is_bold": false}
      }
    },
    "logo": {"default": {"id": "", "path": "./logos/empty.png"}}
  },
  "groups": [
    {"id": "layout", "label": "Layout"},
    {"id": "text", "label": "Text"},
    {"id": "global", "label": "Global"},
    {"id": "output", "label": "Output"},
    {"id": "advanced", "label": "Advanced"}
  ],
  "enums": {
    "layout_type": [
      {"value": "watermark_left_logo", "label": "normal(Logo 居左)"},
      {"value": "watermark_right_logo", "label": "normal(Logo 居右)"}
      // ...
    ],
    "logo_position": [
      {"value": "left", "label": "left"},
      {"value": "right", "label": "right"}
    ],
    "element_name": [
      {"value": "Model", "label": "相机型号(eg. Nikon Z7)"},
      {"value": "Param", "label": "拍摄参数(eg. 50mm f/1.8 1/1000s ISO 100)"},
      {"value": "FocusDistance", "label": "对焦距离(eg. 1.25m)"},
      {"value": "Custom", "label": "自定义"}
      // ...
    ],
    "font_size_level": [
      {"value": 1, "label": "1"},
      {"value": 2, "label": "2"},
      {"value": 3, "label": "3"}
    ]
  },
  "fields": [
    {
      "path": "layout.type",
      "type": "enum",
      "label": "Layout Type",
      "enum_ref": "layout_type",
      "group": "layout"
    },
    {
      "path": "global.white_margin.width",
      "type": "integer",
      "label": "White Margin Width (%)",
      "min": 0,
      "max": 30,
      "step": 1,
      "group": "global",
      "visible_when": {"path": "global.white_margin.enable", "equals": true}
    },
    {
      "path": "layout.elements.left_top.value",
      "type": "string",
      "label": "Left Top Custom Value",
      "group": "text",
      "visible_when": {"path": "layout.elements.left_top.name", "equals": "Custom"}
    }
  ]
}
```

## 进度回调

回调签名（沿用现有）：

```python
on_progress(current, total, source_path, error)
```

1. `current`: 1-based 索引，处理完成后触发。
1. `total`: 固定为 `len(inputs)`。
1. `source_path`: 当前输入文件路径。
1. `error`: 成功时为 `None`，失败时为 `Exception`。
1. 每张图都会触发一次，GUI 可据此计算 `current / total` 与标记失败项。

## 错误回调与返回值

回调签名（沿用现有）：

```python
on_error(source_path, exc)
```

1. 当单张图片处理失败时立即触发（可用于提示或记录）。
1. 最终错误结果以返回值为准。

返回值：

1. `list[tuple[Path, Exception]]`，包含所有失败项。
1. 元素为 `(source_path, exc)`，顺序与处理顺序一致。

## 预览回调

回调签名：

```python
on_preview(source_path, preview_path)
```

1. 仅在 `preview=True` 且处理成功时触发。
1. `preview_path` 为临时文件路径（调用方负责清理）。
