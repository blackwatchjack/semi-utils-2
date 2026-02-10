from utils import extract_gps_lat_and_long


def test_extract_gps_lat_and_long():
    latitude, longitude = extract_gps_lat_and_long(
        "31 deg 10' 00.00\" N",
        "121 deg 30' 00.00\" E",
    )
    assert latitude == "31°10'N"
    assert longitude == "121°30'E"
