from __future__ import annotations

from runtime_paths import resolve_resource_path


def test_resolve_resource_path_works_when_cwd_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = resolve_resource_path("./fonts/Roboto-Regular.ttf")
    assert resolved.exists()
    assert resolved.name == "Roboto-Regular.ttf"
