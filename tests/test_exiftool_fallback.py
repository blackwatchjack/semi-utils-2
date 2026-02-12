from __future__ import annotations

import utils


def test_get_exif_returns_empty_when_exiftool_missing(monkeypatch):
    monkeypatch.setattr(utils, "_resolve_exiftool_command", lambda: None)
    monkeypatch.setattr(utils, "_EXIFTOOL_MISSING_WARNED", False)
    assert utils.get_exif("missing.jpg") == {}


def test_insert_exif_noop_when_exiftool_missing(monkeypatch):
    monkeypatch.setattr(utils, "_resolve_exiftool_command", lambda: None)
    monkeypatch.setattr(utils, "_EXIFTOOL_MISSING_WARNED", False)
    assert utils.insert_exif("a.jpg", "b.jpg") is None
