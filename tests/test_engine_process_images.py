from __future__ import annotations

from pathlib import Path

import engine


def test_output_path_priority_output_map_over_output_dir(monkeypatch, tmp_path):
    captured_targets: list[Path] = []
    sources = [
        tmp_path / "a.jpg",
        tmp_path / "b.jpg",
        tmp_path / "c.jpg",
    ]
    output_dir = tmp_path / "out"
    output_map = {sources[0]: tmp_path / "custom" / "a_out.jpg"}

    def fake_process_one(processor_chain, config, source_path, target_path, **kwargs):
        captured_targets.append(Path(target_path))

    monkeypatch.setattr(engine, "_process_one", fake_process_one)

    errors = engine.process_images(
        inputs=sources,
        output_dir=output_dir,
        output_map=output_map,
    )

    assert errors == []
    assert captured_targets == [
        output_map[sources[0]],
        output_dir / sources[1].name,
        output_dir / sources[2].name,
    ]


def test_output_path_falls_back_to_source_path_without_output_options(monkeypatch, tmp_path):
    captured_targets: list[Path] = []
    sources = [tmp_path / "a.jpg", tmp_path / "b.jpg"]

    def fake_process_one(processor_chain, config, source_path, target_path, **kwargs):
        captured_targets.append(Path(target_path))

    monkeypatch.setattr(engine, "_process_one", fake_process_one)

    errors = engine.process_images(inputs=sources)

    assert errors == []
    assert captured_targets == sources


def test_on_progress_and_on_error_callbacks(monkeypatch, tmp_path):
    sources = [tmp_path / "ok.jpg", tmp_path / "bad.jpg", tmp_path / "ok2.jpg"]
    failures = {sources[1]}
    on_error_calls: list[tuple[Path, str]] = []
    on_progress_calls: list[tuple[int, int, Path, bool]] = []

    def fake_process_one(processor_chain, config, source_path, target_path, **kwargs):
        if Path(source_path) in failures:
            raise RuntimeError(f"boom:{Path(source_path).name}")

    monkeypatch.setattr(engine, "_process_one", fake_process_one)

    def on_error(source_path: Path, exc: Exception):
        on_error_calls.append((Path(source_path), str(exc)))

    def on_progress(current: int, total: int, source_path: Path, error: Exception | None):
        on_progress_calls.append((current, total, Path(source_path), error is None))

    errors = engine.process_images(
        inputs=sources,
        output_dir=tmp_path / "out",
        on_error=on_error,
        on_progress=on_progress,
    )

    assert len(errors) == 1
    assert errors[0][0] == sources[1]
    assert "boom:bad.jpg" in str(errors[0][1])

    assert on_error_calls == [(sources[1], "boom:bad.jpg")]
    assert on_progress_calls == [
        (1, 3, sources[0], True),
        (2, 3, sources[1], False),
        (3, 3, sources[2], True),
    ]


def test_preview_mode_uses_preview_paths_and_disables_exif(monkeypatch, tmp_path):
    calls: list[dict] = []
    preview_pairs: list[tuple[Path, Path]] = []
    sources = [tmp_path / "p1.jpg", tmp_path / "p2.jpg"]
    preview_dir = tmp_path / "preview"
    ignored_output_dir = tmp_path / "ignored-output"

    def fake_process_one(
        processor_chain,
        config,
        source_path,
        target_path,
        keep_exif=True,
        max_size=None,
        quality_override=None,
    ):
        calls.append(
            {
                "source": Path(source_path),
                "target": Path(target_path),
                "keep_exif": keep_exif,
                "max_size": max_size,
                "quality_override": quality_override,
            }
        )

    def on_preview(source_path: Path, preview_path: Path):
        preview_pairs.append((Path(source_path), Path(preview_path)))

    monkeypatch.setattr(engine, "_process_one", fake_process_one)

    errors = engine.process_images(
        inputs=sources,
        output_dir=ignored_output_dir,
        output_map={sources[0]: tmp_path / "custom" / "ignored.jpg"},
        preview=True,
        preview_max_size=900,
        preview_quality=70,
        preview_dir=preview_dir,
        on_preview=on_preview,
    )

    assert errors == []
    assert len(calls) == 2
    assert [item["source"] for item in calls] == sources
    assert all(item["keep_exif"] is False for item in calls)
    assert all(item["max_size"] == 900 for item in calls)
    assert all(item["quality_override"] == 70 for item in calls)
    assert all(item["target"].parent == preview_dir for item in calls)
    assert ignored_output_dir.exists() is False

    assert len(preview_pairs) == 2
    assert [source for source, _ in preview_pairs] == sources
    assert all(preview_path.parent == preview_dir for _, preview_path in preview_pairs)
    assert all(preview_path.exists() for _, preview_path in preview_pairs)
