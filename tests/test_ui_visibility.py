from __future__ import annotations

import copy

from engine import get_config_spec
from enums.constant import CUSTOM_VALUE
from ui_visibility import evaluate_visibility
from ui_visibility import sanitize_config


def _defaults() -> dict:
    return copy.deepcopy(get_config_spec()["defaults"])


def test_custom_layout_visibility_fields():
    defaults = _defaults()
    config = copy.deepcopy(defaults)
    config["layout"]["type"] = "custom_watermark"
    config["layout"]["logo_enable"] = True

    visibility = evaluate_visibility(config)

    assert visibility["layout.logo_enable"] is True
    assert visibility["layout.logo_position"] is True
    assert visibility["layout.background_color"] is True
    assert visibility["base.font"] is True
    assert visibility["base.alternative_font"] is False
    assert visibility["layout.elements.left_top.color"] is True
    assert visibility["layout.elements.left_top.is_bold"] is True


def test_switch_to_simple_resets_hidden_fields_to_defaults():
    defaults = _defaults()
    config = copy.deepcopy(defaults)
    config["layout"]["type"] = "simple"
    config["layout"]["background_color"] = "#ff00aa"
    config["layout"]["logo_enable"] = True
    config["global"]["white_margin"]["enable"] = False
    config["base"]["font"] = "./fonts/custom-font.ttf"
    config["base"]["alternative_font"] = "./fonts/custom-alt.ttf"

    sanitized, visibility = sanitize_config(config, defaults)

    assert visibility["layout.background_color"] is False
    assert visibility["layout.logo_enable"] is False
    assert visibility["global.white_margin.enable"] is False
    assert visibility["base.font"] is False
    assert visibility["base.alternative_font"] is True

    assert sanitized["layout"]["background_color"] == defaults["layout"]["background_color"]
    assert sanitized["layout"]["logo_enable"] == defaults["layout"]["logo_enable"]
    assert sanitized["global"]["white_margin"]["enable"] == defaults["global"]["white_margin"]["enable"]
    assert sanitized["base"]["font"] == defaults["base"]["font"]
    assert sanitized["base"]["alternative_font"] == "./fonts/custom-alt.ttf"


def test_white_margin_width_visibility_by_layout_and_toggle():
    defaults = _defaults()

    watermark_config = copy.deepcopy(defaults)
    watermark_config["layout"]["type"] = "watermark_right_logo"
    watermark_config["global"]["white_margin"]["enable"] = False
    visibility = evaluate_visibility(watermark_config)
    assert visibility["global.white_margin.width"] is False

    blur_border_config = copy.deepcopy(defaults)
    blur_border_config["layout"]["type"] = "background_blur_with_white_border"
    blur_border_config["global"]["white_margin"]["enable"] = False
    visibility = evaluate_visibility(blur_border_config)
    assert visibility["global.white_margin.width"] is True


def test_non_custom_element_value_is_reset_when_hidden():
    defaults = _defaults()
    config = copy.deepcopy(defaults)
    config["layout"]["type"] = "watermark_right_logo"
    config["layout"]["elements"]["left_top"]["name"] = "Model"
    config["layout"]["elements"]["left_top"]["value"] = "dirty"

    sanitized, visibility = sanitize_config(config, defaults)

    assert visibility["layout.elements.left_top.value"] is False
    assert sanitized["layout"]["elements"]["left_top"].get("value") == defaults["layout"]["elements"]["left_top"].get(
        "value"
    )

    config_custom = copy.deepcopy(defaults)
    config_custom["layout"]["type"] = "watermark_right_logo"
    config_custom["layout"]["elements"]["left_top"]["name"] = CUSTOM_VALUE
    config_custom["layout"]["elements"]["left_top"]["value"] = "Semi Utils"

    sanitized_custom, visibility_custom = sanitize_config(config_custom, defaults)
    assert visibility_custom["layout.elements.left_top.value"] is True
    assert sanitized_custom["layout"]["elements"]["left_top"]["value"] == "Semi Utils"
