from __future__ import annotations

import pytest
from PIL import Image

import entity.image_container as image_container_module
from entity.config import ElementConfig
from entity.image_container import ImageContainer
from entity.image_container import extract_bird_species_from_filename
from enums.constant import BIRD_SPECIES_VALUE


@pytest.mark.parametrize(
    ("filename_stem", "expected"),
    [
        ("IMG_1234_白鹭_ISO100", "白鹭"),
        ("DXO_0001_great_egret_400mm", "great egret"),
        ("PXL_20240201_073000_夜鹭_copy", "夜鹭"),
        ("DSCF1234_DJI_0008", "--"),
        ("MVIMG_20240101_朱鹮_EDITED_FINAL", "朱鹮"),
        ("IMG_1234_白鹭_已增强_降噪", "白鹭"),
        ("白鹭已增强降噪", "白鹭"),
    ],
)
def test_extract_bird_species_from_filename(filename_stem, expected):
    assert extract_bird_species_from_filename(filename_stem) == expected


def test_image_container_exposes_bird_species(monkeypatch, tmp_path):
    monkeypatch.setattr(image_container_module, "get_exif", lambda _: {})
    image_path = tmp_path / "IMG_1234_白鹭_ISO100.jpg"
    with Image.new("RGB", (12, 12), color="white") as image:
        image.save(image_path)

    container = ImageContainer(image_path)
    element = ElementConfig({"name": BIRD_SPECIES_VALUE})
    assert container.get_attribute_str(element) == "白鹭"

    # close() 会关闭 watermark_img，先确保它已初始化。
    container.get_watermark_img()
    container.close()
