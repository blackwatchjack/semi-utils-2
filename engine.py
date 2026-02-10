from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
import copy
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from entity.config import Config
from entity.config import DEFAULT_CONFIG
from entity.image_container import ImageContainer
from entity.image_processor import BackgroundBlurProcessor
from entity.image_processor import BackgroundBlurWithWhiteBorderProcessor
from entity.image_processor import CustomWatermarkProcessor
from entity.image_processor import DarkWatermarkLeftLogoProcessor
from entity.image_processor import DarkWatermarkRightLogoProcessor
from entity.image_processor import MarginProcessor
from entity.image_processor import PaddingToOriginalRatioProcessor
from entity.image_processor import ProcessorChain
from entity.image_processor import PureWhiteMarginProcessor
from entity.image_processor import ShadowProcessor
from entity.image_processor import SimpleProcessor
from entity.image_processor import SquareProcessor
from entity.image_processor import WatermarkLeftLogoProcessor
from entity.image_processor import WatermarkRightLogoProcessor
from enums.constant import CAMERA_MAKE_CAMERA_MODEL_NAME
from enums.constant import CAMERA_MAKE_CAMERA_MODEL_VALUE
from enums.constant import CAMERA_MODEL_LENS_MODEL_NAME
from enums.constant import CAMERA_MODEL_LENS_MODEL_VALUE
from enums.constant import CUSTOM_NAME
from enums.constant import CUSTOM_VALUE
from enums.constant import DATE_FILENAME_NAME
from enums.constant import DATE_FILENAME_VALUE
from enums.constant import DATE_NAME
from enums.constant import DATE_VALUE
from enums.constant import DATETIME_FILENAME_NAME
from enums.constant import DATETIME_FILENAME_VALUE
from enums.constant import DATETIME_NAME
from enums.constant import DATETIME_VALUE
from enums.constant import FILENAME_NAME
from enums.constant import FILENAME_VALUE
from enums.constant import FOCUS_DISTANCE_NAME
from enums.constant import FOCUS_DISTANCE_VALUE
from enums.constant import GEO_INFO
from enums.constant import GEO_INFO_VALUE
from enums.constant import LENS_MAKE_LENS_MODEL_NAME
from enums.constant import LENS_MAKE_LENS_MODEL_VALUE
from enums.constant import LENS_NAME
from enums.constant import LENS_VALUE
from enums.constant import MAKE_NAME
from enums.constant import MAKE_VALUE
from enums.constant import MODEL_NAME
from enums.constant import MODEL_VALUE
from enums.constant import NONE_NAME
from enums.constant import NONE_VALUE
from enums.constant import PARAM_NAME
from enums.constant import PARAM_VALUE
from enums.constant import TOTAL_PIXEL_NAME
from enums.constant import TOTAL_PIXEL_VALUE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayoutSpec:
    layout_id: str
    name: str


_LAYOUT_PROCESSORS = {
    WatermarkLeftLogoProcessor.LAYOUT_ID: WatermarkLeftLogoProcessor,
    WatermarkRightLogoProcessor.LAYOUT_ID: WatermarkRightLogoProcessor,
    DarkWatermarkLeftLogoProcessor.LAYOUT_ID: DarkWatermarkLeftLogoProcessor,
    DarkWatermarkRightLogoProcessor.LAYOUT_ID: DarkWatermarkRightLogoProcessor,
    CustomWatermarkProcessor.LAYOUT_ID: CustomWatermarkProcessor,
    SquareProcessor.LAYOUT_ID: SquareProcessor,
    SimpleProcessor.LAYOUT_ID: SimpleProcessor,
    BackgroundBlurProcessor.LAYOUT_ID: BackgroundBlurProcessor,
    BackgroundBlurWithWhiteBorderProcessor.LAYOUT_ID: BackgroundBlurWithWhiteBorderProcessor,
    PureWhiteMarginProcessor.LAYOUT_ID: PureWhiteMarginProcessor,
}


def get_layout_specs() -> list[LayoutSpec]:
    return [
        LayoutSpec(WatermarkLeftLogoProcessor.LAYOUT_ID, WatermarkLeftLogoProcessor.LAYOUT_NAME),
        LayoutSpec(WatermarkRightLogoProcessor.LAYOUT_ID, WatermarkRightLogoProcessor.LAYOUT_NAME),
        LayoutSpec(DarkWatermarkLeftLogoProcessor.LAYOUT_ID, DarkWatermarkLeftLogoProcessor.LAYOUT_NAME),
        LayoutSpec(DarkWatermarkRightLogoProcessor.LAYOUT_ID, DarkWatermarkRightLogoProcessor.LAYOUT_NAME),
        LayoutSpec(CustomWatermarkProcessor.LAYOUT_ID, CustomWatermarkProcessor.LAYOUT_NAME),
        LayoutSpec(SquareProcessor.LAYOUT_ID, SquareProcessor.LAYOUT_NAME),
        LayoutSpec(SimpleProcessor.LAYOUT_ID, SimpleProcessor.LAYOUT_NAME),
        LayoutSpec(BackgroundBlurProcessor.LAYOUT_ID, BackgroundBlurProcessor.LAYOUT_NAME),
        LayoutSpec(BackgroundBlurWithWhiteBorderProcessor.LAYOUT_ID, BackgroundBlurWithWhiteBorderProcessor.LAYOUT_NAME),
        LayoutSpec(PureWhiteMarginProcessor.LAYOUT_ID, PureWhiteMarginProcessor.LAYOUT_NAME),
    ]


def get_config_spec() -> dict:
    layout_options = [
        {"value": spec.layout_id, "label": spec.name}
        for spec in get_layout_specs()
    ]
    element_options = [
        {"value": MODEL_VALUE, "label": MODEL_NAME},
        {"value": MAKE_VALUE, "label": MAKE_NAME},
        {"value": LENS_VALUE, "label": LENS_NAME},
        {"value": PARAM_VALUE, "label": PARAM_NAME},
        {"value": DATETIME_VALUE, "label": DATETIME_NAME},
        {"value": DATE_VALUE, "label": DATE_NAME},
        {"value": CUSTOM_VALUE, "label": CUSTOM_NAME},
        {"value": NONE_VALUE, "label": NONE_NAME},
        {"value": LENS_MAKE_LENS_MODEL_VALUE, "label": LENS_MAKE_LENS_MODEL_NAME},
        {"value": CAMERA_MODEL_LENS_MODEL_VALUE, "label": CAMERA_MODEL_LENS_MODEL_NAME},
        {"value": TOTAL_PIXEL_VALUE, "label": TOTAL_PIXEL_NAME},
        {"value": CAMERA_MAKE_CAMERA_MODEL_VALUE, "label": CAMERA_MAKE_CAMERA_MODEL_NAME},
        {"value": FILENAME_VALUE, "label": FILENAME_NAME},
        {"value": DATE_FILENAME_VALUE, "label": DATE_FILENAME_NAME},
        {"value": DATETIME_FILENAME_VALUE, "label": DATETIME_FILENAME_NAME},
        {"value": GEO_INFO_VALUE, "label": GEO_INFO},
        {"value": FOCUS_DISTANCE_VALUE, "label": FOCUS_DISTANCE_NAME},
    ]

    def element_fields(position: str, label: str) -> list[dict]:
        return [
            {
                "path": f"layout.elements.{position}.name",
                "type": "enum",
                "label": f"{label} Text",
                "enum_ref": "element_name",
                "group": "text",
            },
            {
                "path": f"layout.elements.{position}.color",
                "type": "color",
                "label": f"{label} Color",
                "group": "text",
            },
            {
                "path": f"layout.elements.{position}.is_bold",
                "type": "boolean",
                "label": f"{label} Bold",
                "group": "text",
            },
            {
                "path": f"layout.elements.{position}.value",
                "type": "string",
                "label": f"{label} Custom Value",
                "group": "text",
                "visible_when": {
                    "path": f"layout.elements.{position}.name",
                    "equals": CUSTOM_VALUE,
                },
            },
        ]

    fields = [
        {
            "path": "layout.type",
            "type": "enum",
            "label": "Layout Type",
            "enum_ref": "layout_type",
            "group": "layout",
        },
        {
            "path": "layout.background_color",
            "type": "color",
            "label": "Background Color",
            "group": "layout",
        },
        {
            "path": "layout.logo_enable",
            "type": "boolean",
            "label": "Logo Enabled",
            "group": "layout",
        },
        {
            "path": "layout.logo_position",
            "type": "enum",
            "label": "Logo Position",
            "enum_ref": "logo_position",
            "group": "layout",
            "visible_when": {"path": "layout.logo_enable", "equals": True},
        },
        *element_fields("left_top", "Left Top"),
        *element_fields("left_bottom", "Left Bottom"),
        *element_fields("right_top", "Right Top"),
        *element_fields("right_bottom", "Right Bottom"),
        {
            "path": "global.shadow.enable",
            "type": "boolean",
            "label": "Shadow",
            "group": "global",
        },
        {
            "path": "global.white_margin.enable",
            "type": "boolean",
            "label": "White Margin",
            "group": "global",
        },
        {
            "path": "global.white_margin.width",
            "type": "integer",
            "label": "White Margin Width (%)",
            "min": 0,
            "max": 30,
            "step": 1,
            "group": "global",
            "visible_when": {"path": "global.white_margin.enable", "equals": True},
        },
        {
            "path": "global.padding_with_original_ratio.enable",
            "type": "boolean",
            "label": "Padding With Original Ratio",
            "group": "global",
        },
        {
            "path": "global.focal_length.use_equivalent_focal_length",
            "type": "boolean",
            "label": "Use Equivalent Focal Length",
            "group": "global",
        },
        {
            "path": "base.quality",
            "type": "integer",
            "label": "Quality",
            "min": 1,
            "max": 100,
            "step": 1,
            "group": "output",
        },
        {
            "path": "base.font_size",
            "type": "enum",
            "label": "Font Size Level",
            "enum_ref": "font_size_level",
            "group": "advanced",
        },
        {
            "path": "base.bold_font_size",
            "type": "enum",
            "label": "Bold Font Size Level",
            "enum_ref": "font_size_level",
            "group": "advanced",
        },
        {
            "path": "base.font",
            "type": "path",
            "label": "Font Path",
            "group": "advanced",
        },
        {
            "path": "base.bold_font",
            "type": "path",
            "label": "Bold Font Path",
            "group": "advanced",
        },
        {
            "path": "base.alternative_font",
            "type": "path",
            "label": "Alternative Font Path",
            "group": "advanced",
        },
        {
            "path": "base.alternative_bold_font",
            "type": "path",
            "label": "Alternative Bold Font Path",
            "group": "advanced",
        },
    ]

    return {
        "version": 1,
        "defaults": copy.deepcopy(DEFAULT_CONFIG),
        "groups": [
            {"id": "layout", "label": "Layout"},
            {"id": "text", "label": "Text"},
            {"id": "global", "label": "Global"},
            {"id": "output", "label": "Output"},
            {"id": "advanced", "label": "Advanced"},
        ],
        "enums": {
            "layout_type": layout_options,
            "logo_position": [
                {"value": "left", "label": "left"},
                {"value": "right", "label": "right"},
            ],
            "element_name": element_options,
            "font_size_level": [
                {"value": 1, "label": "1"},
                {"value": 2, "label": "2"},
                {"value": 3, "label": "3"},
            ],
        },
        "fields": fields,
    }


def build_processor_chain(config: Config) -> ProcessorChain:
    processor_chain = ProcessorChain()

    if config.has_shadow_enabled() and config.get_layout_type() != 'square':
        processor_chain.add(ShadowProcessor(config))

    layout_id = config.get_layout_type()
    processor_cls = _LAYOUT_PROCESSORS.get(layout_id, SimpleProcessor)
    processor_chain.add(processor_cls(config))

    if config.has_white_margin_enabled() and 'watermark' in layout_id:
        processor_chain.add(MarginProcessor(config))

    if config.has_padding_with_original_ratio_enabled() and layout_id != 'square':
        processor_chain.add(PaddingToOriginalRatioProcessor(config))

    return processor_chain


def _resolve_output_path(
    source_path: Path,
    output_dir: Path | None,
    output_map: Mapping[Path, Path] | None,
) -> Path:
    if output_map and source_path in output_map:
        return output_map[source_path]
    if output_dir:
        return output_dir / source_path.name
    return source_path.with_name(source_path.name)


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _process_one(
    processor_chain: ProcessorChain,
    config: Config,
    source_path: Path,
    target_path: Path,
    keep_exif: bool = True,
    max_size: int | None = None,
    quality_override: int | None = None,
) -> None:
    container = ImageContainer(source_path)
    container.is_use_equivalent_focal_length(config.use_equivalent_focal_length())
    processor_chain.process(container)
    _ensure_parent_dir(target_path)
    quality = quality_override if quality_override is not None else config.get_quality()
    container.save(target_path, quality=quality, keep_exif=keep_exif, max_size=max_size)
    container.close()


def process_images(
    inputs: Sequence[str | Path],
    config_data: dict | None = None,
    output_dir: str | Path | None = None,
    output_map: Mapping[str | Path, str | Path] | None = None,
    on_progress: Callable[[int, int, Path, Exception | None], None] | None = None,
    on_error: Callable[[Path, Exception], None] | None = None,
    preview: bool = False,
    preview_max_size: int | None = None,
    preview_quality: int | None = None,
    preview_dir: str | Path | None = None,
    on_preview: Callable[[Path, Path], None] | None = None,
) -> list[tuple[Path, Exception]]:
    config = Config(config_data)
    processor_chain = build_processor_chain(config)

    normalized_inputs = [Path(p) for p in inputs]
    normalized_output_dir = Path(output_dir) if output_dir else None
    normalized_output_map = None
    if output_map:
        normalized_output_map = {Path(k): Path(v) for k, v in output_map.items()}
    normalized_preview_dir = Path(preview_dir) if preview_dir else None
    if normalized_preview_dir:
        normalized_preview_dir.mkdir(parents=True, exist_ok=True)

    total = len(normalized_inputs)
    errors: list[tuple[Path, Exception]] = []

    for index, source_path in enumerate(normalized_inputs, start=1):
        error: Exception | None = None
        try:
            if preview:
                suffix = source_path.suffix if source_path.suffix else ".jpg"
                fd, target_name = tempfile.mkstemp(
                    prefix=f"preview_{source_path.stem}_",
                    suffix=suffix,
                    dir=normalized_preview_dir,
                )
                os.close(fd)
                target_path = Path(target_name)
                _process_one(
                    processor_chain,
                    config,
                    source_path,
                    target_path,
                    keep_exif=False,
                    max_size=preview_max_size,
                    quality_override=preview_quality,
                )
                if on_preview:
                    on_preview(source_path, target_path)
            else:
                target_path = _resolve_output_path(source_path, normalized_output_dir, normalized_output_map)
                _process_one(processor_chain, config, source_path, target_path)
        except Exception as exc:
            error = exc
            errors.append((source_path, exc))
            logger.exception("Failed to process %s", source_path)
            if on_error:
                on_error(source_path, exc)
        if on_progress:
            on_progress(index, total, source_path, error)

    return errors
