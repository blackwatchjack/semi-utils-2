from __future__ import annotations

import copy
from typing import Any
from typing import Mapping

from enums.constant import CUSTOM_VALUE

POSITIONS: tuple[str, ...] = ("left_top", "left_bottom", "right_top", "right_bottom")

WATERMARK_LAYOUTS: set[str] = {
    "watermark_left_logo",
    "watermark_right_logo",
    "dark_watermark_left_logo",
    "dark_watermark_right_logo",
    "custom_watermark",
}
CUSTOM_LAYOUTS: set[str] = {"custom_watermark"}
SIMPLE_LAYOUTS: set[str] = {"simple"}
WHITE_MARGIN_WIDTH_DIRECT_LAYOUTS: set[str] = {"background_blur_with_white_border", "pure_white_margin"}
BG_COLOR_LAYOUTS: set[str] = {"custom_watermark", "pure_white_margin"}

_MANAGED_PATHS: set[str] = {
    "layout.type",
    "layout.background_color",
    "layout.logo_enable",
    "layout.logo_position",
    "global.shadow.enable",
    "global.white_margin.enable",
    "global.white_margin.width",
    "global.padding_with_original_ratio.enable",
    "global.focal_length.use_equivalent_focal_length",
    "base.quality",
    "base.font_size",
    "base.bold_font_size",
    "base.font",
    "base.bold_font",
    "base.alternative_font",
    "base.alternative_bold_font",
}
for _position in POSITIONS:
    _MANAGED_PATHS.add(f"layout.elements.{_position}.name")
    _MANAGED_PATHS.add(f"layout.elements.{_position}.color")
    _MANAGED_PATHS.add(f"layout.elements.{_position}.is_bold")
    _MANAGED_PATHS.add(f"layout.elements.{_position}.value")


_UNSET = object()
_MISSING = object()


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def merge_with_defaults(config_data: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(defaults)
    return _deep_merge(merged, config_data)


def _get_path(data: Mapping[str, Any], path: str, default: Any = _UNSET) -> Any:
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            if default is _UNSET:
                raise KeyError(path)
            return default
        current = current[segment]
    return current


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    current: dict[str, Any] = data
    segments = path.split(".")
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[segments[-1]] = copy.deepcopy(value)


def _delete_path(data: dict[str, Any], path: str) -> None:
    current: dict[str, Any] = data
    segments = path.split(".")
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            return
        current = child
    current.pop(segments[-1], None)


def evaluate_visibility(config_data: Mapping[str, Any]) -> dict[str, bool]:
    layout_type = str(_get_path(config_data, "layout.type", ""))
    is_watermark_layout = layout_type in WATERMARK_LAYOUTS
    is_custom_layout = layout_type in CUSTOM_LAYOUTS
    is_simple_layout = layout_type in SIMPLE_LAYOUTS

    visibility: dict[str, bool] = {
        "layout.type": True,
        "layout.background_color": layout_type in BG_COLOR_LAYOUTS,
        "layout.logo_enable": is_custom_layout,
        "layout.logo_position": is_custom_layout and bool(_get_path(config_data, "layout.logo_enable", False)),
        "global.shadow.enable": layout_type != "square",
        "global.white_margin.enable": is_watermark_layout,
        "global.padding_with_original_ratio.enable": layout_type != "square",
        "global.focal_length.use_equivalent_focal_length": is_watermark_layout or is_simple_layout,
        "base.quality": True,
        "base.font_size": is_watermark_layout,
        "base.bold_font_size": is_watermark_layout,
        "base.font": is_watermark_layout,
        "base.bold_font": is_watermark_layout,
        "base.alternative_font": is_simple_layout,
        "base.alternative_bold_font": is_simple_layout,
    }

    white_margin_enabled = bool(_get_path(config_data, "global.white_margin.enable", False))
    visibility["global.white_margin.width"] = (
        (is_watermark_layout and white_margin_enabled)
        or layout_type in WHITE_MARGIN_WIDTH_DIRECT_LAYOUTS
    )

    for position in POSITIONS:
        name_path = f"layout.elements.{position}.name"
        value_path = f"layout.elements.{position}.value"
        visibility[name_path] = is_watermark_layout
        visibility[f"layout.elements.{position}.color"] = is_custom_layout
        visibility[f"layout.elements.{position}.is_bold"] = is_custom_layout
        current_name = _get_path(config_data, name_path, "")
        visibility[value_path] = is_watermark_layout and current_name == CUSTOM_VALUE

    return visibility


def _reset_hidden_fields(config_data: dict[str, Any], defaults: Mapping[str, Any], visibility: Mapping[str, bool]) -> dict[str, Any]:
    updated = copy.deepcopy(config_data)
    for path in _MANAGED_PATHS:
        if visibility.get(path, True):
            continue
        default_value = _get_path(defaults, path, _MISSING)
        if default_value is _MISSING:
            _delete_path(updated, path)
        else:
            _set_path(updated, path, default_value)
    return updated


def sanitize_config(config_data: Mapping[str, Any], defaults: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, bool]]:
    sanitized = merge_with_defaults(config_data, defaults)

    for _ in range(4):
        visibility = evaluate_visibility(sanitized)
        next_config = _reset_hidden_fields(sanitized, defaults, visibility)
        if next_config == sanitized:
            break
        sanitized = next_config

    final_visibility = evaluate_visibility(sanitized)
    return sanitized, final_visibility


def managed_paths() -> list[str]:
    return sorted(_MANAGED_PATHS)
