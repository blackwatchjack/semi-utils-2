import pytest

from entity.image_container import get_focus_distance


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1.20 m", "1.2m"),
        ("1 m", "1m"),
        ("125 cm", "1.25m"),
        ("750 mm", "0.75m"),
        ("0.001 km", "1m"),
        ("3.28084 ft", "1m"),
        ("39.37 in", "1m"),
    ],
)
def test_focus_distance_unit_normalization(raw_value, expected):
    exif = {"FocusDistance": raw_value}
    assert get_focus_distance(exif) == expected


def test_focus_distance_fallback_order():
    exif = {
        "FocusDistance": "Unknown",
        "SubjectDistance": "125 cm",
        "ApproximateFocusDistance": "0.4 m",
        "HyperfocalDistance": "8 m",
    }
    assert get_focus_distance(exif) == "1.25m"


@pytest.mark.parametrize(
    "raw_value",
    [
        "Unknown",
        "inf",
        "abc",
        "0.5-1.2 m",
    ],
)
def test_focus_distance_unparseable_returns_empty(raw_value):
    exif = {"FocusDistance": raw_value}
    assert get_focus_distance(exif) == ""
