from engine import get_config_spec
from engine import get_layout_specs


def test_get_layout_specs_has_values():
    specs = get_layout_specs()
    assert specs
    assert all(spec.layout_id for spec in specs)
    assert all(spec.name for spec in specs)


def test_get_config_spec_basics():
    spec = get_config_spec()
    assert spec["version"] == 1
    assert "defaults" in spec
    assert "fields" in spec
    assert any(field["path"] == "layout.type" for field in spec["fields"])
    element_values = {item["value"] for item in spec["enums"]["element_name"]}
    assert "FocusDistance" in element_values
