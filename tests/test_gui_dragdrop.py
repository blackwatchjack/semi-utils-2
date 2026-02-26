from __future__ import annotations

from pathlib import Path

from gui_app import build_input_identity
from gui_app import parse_dropped_paths
from gui_app import select_valid_input_paths


def test_parse_dropped_paths_with_braces_and_spaces():
    parsed = parse_dropped_paths("{/tmp/a b.jpg} {/tmp/c.png}")
    assert parsed == [Path("/tmp/a b.jpg"), Path("/tmp/c.png")]


def test_parse_dropped_paths_from_file_uri():
    parsed = parse_dropped_paths("file:///tmp/a%20b.jpg", splitlist=lambda data: (data,))
    assert parsed == [Path("/tmp/a b.jpg")]


def test_select_valid_input_paths_filters_invalid_and_duplicate(tmp_path: Path):
    image_path = tmp_path / "ok.jpg"
    image_path.write_bytes(b"jpg-data")
    invalid_type = tmp_path / "bad.txt"
    invalid_type.write_text("text", encoding="utf-8")
    dir_path = tmp_path / "dir"
    dir_path.mkdir()
    missing_path = tmp_path / "missing.jpeg"

    accepted, skipped, _ = select_valid_input_paths(
        [image_path, image_path, invalid_type, dir_path, missing_path],
    )
    assert accepted == [image_path]
    assert skipped["duplicate"] == 1
    assert skipped["invalid_type"] == 1
    assert skipped["not_file"] == 2


def test_select_valid_input_paths_respects_existing_identity(tmp_path: Path):
    image_path = tmp_path / "ok.jpeg"
    image_path.write_bytes(b"jpg-data")
    identity = build_input_identity(image_path)
    assert identity is not None

    accepted, skipped, _ = select_valid_input_paths([image_path], existing_identities={identity})
    assert accepted == []
    assert skipped["duplicate"] == 1
