from __future__ import annotations

import argparse
import copy
import cgi
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from dataclasses import dataclass
from dataclasses import field
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image

from engine import get_config_spec
from engine import process_images
from enums.constant import CUSTOM_VALUE
from logging_setup import setup_temp_logging

SPEC = get_config_spec()
DEFAULTS = copy.deepcopy(SPEC["defaults"])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_FILES = 200
MAX_REQUEST_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_LIVE_JOBS = 500
DEFAULT_MAX_CONCURRENT_JOBS = 2
JOB_TTL_SECONDS = 30 * 60
CLEANUP_INTERVAL_SECONDS = 60

JOBS_LOCK = threading.Lock()
JOBS: dict[str, "JobRecord"] = {}
CLEANUP_THREAD_STARTED = False

try:
    _env_concurrent_jobs = int(os.environ.get("SEMI_WEB_MAX_CONCURRENT_JOBS", str(DEFAULT_MAX_CONCURRENT_JOBS)))
except (TypeError, ValueError):
    _env_concurrent_jobs = DEFAULT_MAX_CONCURRENT_JOBS

MAX_CONCURRENT_JOBS = max(1, min(_env_concurrent_jobs, 16))
RUNNING_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
POSITIONS: tuple[tuple[str, str], ...] = (
    ("left_top", "Left Top"),
    ("left_bottom", "Left Bottom"),
    ("right_top", "Right Top"),
    ("right_bottom", "Right Bottom"),
)


@dataclass
class JobRecord:
    job_id: str
    created_at: float
    updated_at: float
    status: str
    message: str
    mode: str
    total: int
    current: int
    output_count: int
    errors: list[dict[str, str]] = field(default_factory=list)
    workspace_dir: Path | None = None
    zip_path: Path | None = None
    output_filename: str | None = None
    config_data: dict[str, Any] | None = None
    input_paths: list[Path] = field(default_factory=list)
    preview_mode: bool = False
    preview_max_size: int | None = None
    preview_quality: int | None = None
    cancel_requested: bool = False


class JobCancelledError(Exception):
    pass


def _parse_int(raw: str | None, default: int, low: int | None = None, high: int | None = None) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if low is not None and value < low:
        value = low
    if high is not None and value > high:
        value = high
    return value


def _set_max_concurrent_jobs(limit: int) -> None:
    global MAX_CONCURRENT_JOBS, RUNNING_SLOTS
    normalized = max(1, limit)
    MAX_CONCURRENT_JOBS = normalized
    RUNNING_SLOTS = threading.BoundedSemaphore(normalized)


def _field_checked(form: cgi.FieldStorage, name: str) -> bool:
    return form.getfirst(name) is not None


def _checked_attr(value: bool) -> str:
    return " checked" if value else ""


def _build_options(options: list[dict[str, Any]], selected: Any) -> str:
    selected_text = str(selected)
    rows = []
    for item in options:
        value = str(item["value"])
        selected_attr = " selected" if value == selected_text else ""
        rows.append(
            f'<option value="{escape(value)}"{selected_attr}>{escape(str(item["label"]))}</option>'
        )
    return "".join(rows)


def _unique_path(base_dir: Path, filename: str, used: set[str]) -> Path:
    base_name = Path(filename).name or "image.jpg"
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix
    candidate = base_name
    index = 1
    while candidate in used:
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used.add(candidate)
    return base_dir / candidate


def _build_config(form: cgi.FieldStorage) -> dict:
    config = copy.deepcopy(DEFAULTS)
    config["layout"]["type"] = form.getfirst("layout", DEFAULTS["layout"]["type"])
    config["layout"]["background_color"] = form.getfirst("background_color", DEFAULTS["layout"]["background_color"])
    config["layout"]["logo_enable"] = _field_checked(form, "logo_enable")
    config["layout"]["logo_position"] = form.getfirst("logo_position", DEFAULTS["layout"]["logo_position"])

    config["base"]["quality"] = _parse_int(form.getfirst("quality"), DEFAULTS["base"]["quality"], 1, 100)
    config["base"]["font_size"] = _parse_int(form.getfirst("font_size"), DEFAULTS["base"]["font_size"], 1, 3)
    config["base"]["bold_font_size"] = _parse_int(
        form.getfirst("bold_font_size"),
        DEFAULTS["base"]["bold_font_size"],
        1,
        3,
    )
    config["base"]["font"] = form.getfirst("font", DEFAULTS["base"]["font"])
    config["base"]["bold_font"] = form.getfirst("bold_font", DEFAULTS["base"]["bold_font"])
    config["base"]["alternative_font"] = form.getfirst("alternative_font", DEFAULTS["base"]["alternative_font"])
    config["base"]["alternative_bold_font"] = form.getfirst(
        "alternative_bold_font",
        DEFAULTS["base"]["alternative_bold_font"],
    )

    config["global"]["shadow"]["enable"] = _field_checked(form, "shadow")
    config["global"]["white_margin"]["enable"] = _field_checked(form, "white_margin")
    config["global"]["white_margin"]["width"] = _parse_int(
        form.getfirst("white_margin_width"),
        DEFAULTS["global"]["white_margin"]["width"],
        0,
        30,
    )
    config["global"]["padding_with_original_ratio"]["enable"] = _field_checked(form, "padding_ratio")
    config["global"]["focal_length"]["use_equivalent_focal_length"] = _field_checked(form, "equivalent_focal_length")

    for position, _label in POSITIONS:
        name_key = f"element_{position}_name"
        value_key = f"element_{position}_value"
        color_key = f"element_{position}_color"
        bold_key = f"element_{position}_is_bold"
        element_name = form.getfirst(name_key, config["layout"]["elements"][position]["name"])
        element = config["layout"]["elements"][position]
        element["name"] = element_name
        element["color"] = form.getfirst(color_key, element.get("color", "#212121"))
        element["is_bold"] = _field_checked(form, bold_key)
        if element_name == CUSTOM_VALUE:
            element["value"] = form.getfirst(value_key, "")
        elif "value" in element:
            element.pop("value")

    return config


def _error_response(status: HTTPStatus, code: str, message: str) -> tuple[int, bytes]:
    payload = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    return status.value, json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _serialize_job(job: JobRecord) -> dict[str, Any]:
    done = job.status == "done"
    return {
        "ok": True,
        "job": {
            "job_id": job.job_id,
            "status": job.status,
            "message": job.message,
            "mode": job.mode,
            "progress": {
                "current": job.current,
                "total": job.total,
                "percent": 0 if job.total <= 0 else round(job.current / job.total * 100, 2),
            },
            "output_count": job.output_count,
            "error_count": len(job.errors),
            "errors": job.errors,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "cancel_requested": job.cancel_requested,
            "can_cancel": job.status in {"queued", "waiting", "running", "cancelling"},
            "cancel_url": f"/api/jobs/{job.job_id}/cancel",
            "download_url": f"/api/jobs/{job.job_id}/download" if done else None,
        },
    }


def _write_with_limit(src, dst, max_bytes: int) -> int:
    total = 0
    while True:
        chunk = src.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"File exceeds maximum size: {max_bytes} bytes")
        dst.write(chunk)
    return total


def _validate_image(path: Path) -> None:
    with Image.open(path) as img:
        img.verify()


def _extract_uploads(form: cgi.FieldStorage, input_dir: Path) -> list[Path]:
    files_field = form["files"] if "files" in form else []
    if not isinstance(files_field, list):
        files_field = [files_field]

    upload_items = [item for item in files_field if getattr(item, "filename", None)]
    if not upload_items:
        raise ValueError("No input files provided")
    if len(upload_items) > MAX_FILES:
        raise ValueError(f"Too many files. Maximum allowed: {MAX_FILES}")

    input_paths: list[Path] = []
    used_names: set[str] = set()
    for item in upload_items:
        filename = str(item.filename)
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise ValueError(f"Unsupported file type: {filename}. Allowed: {allowed}")

        target_path = _unique_path(input_dir, filename, used_names)
        with target_path.open("wb") as out:
            _write_with_limit(item.file, out, MAX_FILE_BYTES)

        try:
            _validate_image(target_path)
        except Exception as exc:
            raise ValueError(f"Invalid image file: {filename}. {exc}") from exc

        input_paths.append(target_path)

    return input_paths


def _create_zip(output_files: list[Path], report: dict[str, Any], target_zip: Path, folder: str) -> None:
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in output_files:
            zf.write(file_path, arcname=f"{folder}/{file_path.name}")
        zf.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=2))


def _cleanup_expired_jobs() -> None:
    now = time.time()
    expired_ids: list[str] = []

    with JOBS_LOCK:
        for job_id, job in JOBS.items():
            done_like = job.status in {"done", "error", "cancelled"}
            age = now - job.updated_at
            if done_like and age >= JOB_TTL_SECONDS:
                expired_ids.append(job_id)

        for job_id in expired_ids:
            job = JOBS.pop(job_id, None)
            if not job:
                continue
            if job.workspace_dir and job.workspace_dir.exists():
                shutil.rmtree(job.workspace_dir, ignore_errors=True)


def reset_jobs_for_tests() -> None:
    with JOBS_LOCK:
        jobs = list(JOBS.values())
        JOBS.clear()
    for job in jobs:
        if job.workspace_dir and job.workspace_dir.exists():
            shutil.rmtree(job.workspace_dir, ignore_errors=True)
    _set_max_concurrent_jobs(DEFAULT_MAX_CONCURRENT_JOBS)


def set_max_concurrent_jobs_for_tests(limit: int) -> None:
    _set_max_concurrent_jobs(limit)


def _cleanup_loop() -> None:
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        _cleanup_expired_jobs()


def _ensure_cleanup_thread() -> None:
    global CLEANUP_THREAD_STARTED
    if CLEANUP_THREAD_STARTED:
        return
    CLEANUP_THREAD_STARTED = True
    thread = threading.Thread(target=_cleanup_loop, daemon=True)
    thread.start()


def _update_job(job_id: str, **changes: Any) -> JobRecord | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        return job


def _is_cancel_requested(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.cancel_requested)


def _request_cancel(job_id: str) -> tuple[JobRecord | None, str]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None, "not_found"
        if job.status in {"done", "error"}:
            return job, "terminal"
        if job.status == "cancelled":
            return job, "already_cancelled"
        job.cancel_requested = True
        if job.status in {"queued", "waiting"}:
            job.status = "cancelled"
            job.message = "Cancelled"
        elif job.status in {"running", "cancelling"}:
            job.status = "cancelling"
            job.message = "Cancellation requested, waiting for current step"
        else:
            job.status = "cancelling"
            job.message = "Cancellation requested"
        job.updated_at = time.time()
        return job, "accepted"


def _count_output_files(job: JobRecord, output_dir: Path | None, preview_paths: list[Path]) -> int:
    if job.preview_mode:
        return len([path for path in preview_paths if path.exists()])
    if output_dir is None:
        return 0
    count = 0
    for source_path in job.input_paths:
        if (output_dir / source_path.name).exists():
            count += 1
    return count


def _run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return

    if _is_cancel_requested(job_id):
        _update_job(job_id, status="cancelled", message="Cancelled", cancel_requested=True)
        return

    _update_job(job_id, status="waiting", message="Waiting for available worker slot")

    slot_acquired = False
    preview_paths: list[Path] = []
    output_dir: Path | None = None
    preview_dir: Path | None = None
    error_items: list[dict[str, str]] = []

    try:
        while True:
            if _is_cancel_requested(job_id):
                _update_job(job_id, status="cancelled", message="Cancelled", cancel_requested=True)
                return
            if RUNNING_SLOTS.acquire(timeout=0.2):
                slot_acquired = True
                break

        if _is_cancel_requested(job_id):
            _update_job(job_id, status="cancelled", message="Cancelled", cancel_requested=True)
            return

        _update_job(job_id, status="running", message="Processing started")

        if job.workspace_dir is None:
            raise RuntimeError("Missing workspace directory")

        if job.preview_mode:
            preview_dir = job.workspace_dir / "preview"
            preview_dir.mkdir(parents=True, exist_ok=True)
        else:
            output_dir = job.workspace_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

        def on_progress(current: int, total: int, source_path: Path, error: Exception | None) -> None:
            if _is_cancel_requested(job_id):
                raise JobCancelledError("Job cancelled by user")
            if error is None:
                msg = f"Processed {source_path.name}"
            else:
                msg = f"Failed {source_path.name}: {error}"
            _update_job(job_id, current=current, total=total, message=msg)

        def on_error(source_path: Path, exc: Exception) -> None:
            error_items.append({"source": str(source_path), "error": str(exc)})

        errors = process_images(
            inputs=job.input_paths,
            config_data=job.config_data,
            output_dir=output_dir,
            preview=job.preview_mode,
            preview_dir=preview_dir,
            preview_max_size=job.preview_max_size,
            preview_quality=job.preview_quality,
            on_progress=on_progress,
            on_error=on_error,
            on_preview=(lambda _src, p: preview_paths.append(Path(p))) if job.preview_mode else None,
        )

        if job.preview_mode:
            output_files = [path for path in preview_paths if path.exists()]
            folder = "preview"
            filename = "semi-utils-preview.zip"
            mode = "preview"
        else:
            output_files = [
                (output_dir / source_path.name)
                for source_path in job.input_paths
                if output_dir is not None and (output_dir / source_path.name).exists()
            ]
            folder = "output"
            filename = "semi-utils-output.zip"
            mode = "normal"

        report = {
            "total_inputs": len(job.input_paths),
            "mode": mode,
            "output_count": len(output_files),
            "error_count": len(errors),
            "errors": [{"source": str(src), "error": str(exc)} for src, exc in errors],
        }

        zip_path = job.workspace_dir / filename
        _create_zip(output_files, report, zip_path, folder)

        _update_job(
            job_id,
            status="done",
            message="Completed",
            output_count=len(output_files),
            errors=report["errors"],
            zip_path=zip_path,
            output_filename=filename,
            current=len(job.input_paths),
            total=len(job.input_paths),
        )
    except JobCancelledError:
        _update_job(
            job_id,
            status="cancelled",
            message="Cancelled",
            output_count=_count_output_files(job, output_dir, preview_paths),
            errors=error_items,
            cancel_requested=True,
        )
    except Exception as exc:
        _update_job(job_id, status="error", message=f"Processing failed: {exc}")
    finally:
        if slot_acquired:
            RUNNING_SLOTS.release()


def _build_html() -> bytes:
    layout_options = _build_options(SPEC["enums"]["layout_type"], DEFAULTS["layout"]["type"])
    logo_position_options = _build_options(SPEC["enums"]["logo_position"], DEFAULTS["layout"]["logo_position"])
    font_size_options = _build_options(SPEC["enums"]["font_size_level"], DEFAULTS["base"]["font_size"])
    bold_font_size_options = _build_options(SPEC["enums"]["font_size_level"], DEFAULTS["base"]["bold_font_size"])
    element_options_by_position = {
        position: _build_options(SPEC["enums"]["element_name"], DEFAULTS["layout"]["elements"][position]["name"])
        for position, _label in POSITIONS
    }

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>semi-utils Web GUI</title>
  <style>
    :root {{
      --bg: #edf1f7;
      --card: #ffffff;
      --text: #111827;
      --muted: #4b5563;
      --line: #d1d5db;
      --brand: #0f4c81;
      --brand2: #0b6fb8;
      --ok: #166534;
      --err: #b91c1c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      background: radial-gradient(circle at 20% 20%, #f8fbff, var(--bg));
      color: var(--text);
    }}
    .container {{
      max-width: 1120px;
      margin: 20px auto;
      padding: 14px;
    }}
    .header {{
      margin-bottom: 14px;
    }}
    .title {{
      font-size: 28px;
      font-weight: 700;
      margin: 0;
    }}
    .subtitle {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 14px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 8px 30px rgba(15, 76, 129, 0.08);
      padding: 16px;
      margin-bottom: 12px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 14px;
    }}
    .full {{ grid-column: 1 / -1; }}
    label {{
      display: block;
      font-size: 13px;
      margin-bottom: 5px;
      color: #243447;
    }}
    input, select, button {{
      font-size: 14px;
      border-radius: 10px;
      border: 1px solid var(--line);
      padding: 9px 11px;
      width: 100%;
      background: #fff;
    }}
    input[type="checkbox"] {{ width: auto; padding: 0; }}
    .inline {{
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .inline label {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
      margin: 0;
      font-size: 14px;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      align-items: center;
      margin-top: 14px;
      flex-wrap: wrap;
    }}
    button {{
      max-width: 260px;
      background: linear-gradient(120deg, var(--brand), var(--brand2));
      color: #fff;
      border: 0;
      cursor: pointer;
      font-weight: 600;
    }}
    button:disabled {{ opacity: 0.65; cursor: not-allowed; }}
    .status {{
      margin-top: 8px;
      white-space: pre-wrap;
      font-size: 14px;
      color: var(--muted);
    }}
    .result {{
      border-top: 1px dashed var(--line);
      margin-top: 12px;
      padding-top: 12px;
      display: none;
    }}
    .ok {{ color: var(--ok); }}
    .err {{ color: var(--err); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    a.download {{
      display: inline-block;
      margin-top: 8px;
      color: #0a4a7c;
      text-decoration: none;
      font-weight: 600;
    }}
    @media (max-width: 900px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1 class="title">semi-utils Web GUI</h1>
      <div class="subtitle">上传图片后后台处理，可轮询进度并下载 ZIP 结果。</div>
    </div>

    <form id="processForm" class="card" method="post" action="/api/process" enctype="multipart/form-data">
      <div class="grid">
        <div class="full">
          <label>输入图片（支持多选）</label>
          <input type="file" name="files" accept=".jpg,.jpeg,.png,.JPG,.JPEG,.PNG" multiple required />
        </div>

        <div>
          <label>Layout</label>
          <select name="layout">{layout_options}</select>
        </div>
        <div>
          <label>Quality (1-100)</label>
          <input type="number" name="quality" min="1" max="100" value="{DEFAULTS["base"]["quality"]}" />
        </div>

        <div>
          <label>Background Color</label>
          <input type="color" name="background_color" value="{escape(DEFAULTS["layout"]["background_color"])}" />
        </div>
        <div>
          <label>Logo Position</label>
          <select id="logoPositionInput" name="logo_position">{logo_position_options}</select>
        </div>

        <div class="full inline">
          <label><input type="checkbox" name="shadow"{_checked_attr(DEFAULTS["global"]["shadow"]["enable"])} /> Shadow</label>
          <label><input id="whiteMarginCheck" type="checkbox" name="white_margin"{_checked_attr(DEFAULTS["global"]["white_margin"]["enable"])} /> White Margin</label>
          <label><input type="checkbox" name="padding_ratio"{_checked_attr(DEFAULTS["global"]["padding_with_original_ratio"]["enable"])} /> Padding With Original Ratio</label>
          <label><input type="checkbox" name="equivalent_focal_length"{_checked_attr(DEFAULTS["global"]["focal_length"]["use_equivalent_focal_length"])} /> Use Equivalent Focal Length</label>
          <label><input id="logoEnableCheck" type="checkbox" name="logo_enable"{_checked_attr(DEFAULTS["layout"]["logo_enable"])} /> Logo Enable</label>
        </div>

        <div>
          <label>White Margin Width (%)</label>
          <input id="whiteMarginWidthInput" type="number" name="white_margin_width" min="0" max="30" value="{DEFAULTS["global"]["white_margin"]["width"]}" />
        </div>

        <div class="full inline">
          <label><input id="previewCheck" type="checkbox" name="preview" /> Preview Mode</label>
        </div>
        <div>
          <label>Preview Max Size</label>
          <input id="previewMaxSizeInput" type="number" name="preview_max_size" min="200" max="8000" value="1600" />
        </div>
        <div>
          <label>Preview Quality</label>
          <input id="previewQualityInput" type="number" name="preview_quality" min="1" max="100" value="80" />
        </div>

        <div>
          <label>Left Top Element</label>
          <select id="element_left_top_name" name="element_left_top_name">{element_options_by_position["left_top"]}</select>
        </div>
        <div>
          <label>Left Top Custom Value</label>
          <input id="element_left_top_value" type="text" name="element_left_top_value" value="{escape(DEFAULTS["layout"]["elements"]["left_top"].get("value", ""))}" />
        </div>
        <div>
          <label>Left Top Color</label>
          <input type="color" name="element_left_top_color" value="{escape(DEFAULTS["layout"]["elements"]["left_top"].get("color", "#212121"))}" />
        </div>
        <div>
          <label><input type="checkbox" name="element_left_top_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["left_top"].get("is_bold", False))} /> Left Top Bold</label>
        </div>

        <div>
          <label>Left Bottom Element</label>
          <select id="element_left_bottom_name" name="element_left_bottom_name">{element_options_by_position["left_bottom"]}</select>
        </div>
        <div>
          <label>Left Bottom Custom Value</label>
          <input id="element_left_bottom_value" type="text" name="element_left_bottom_value" value="{escape(DEFAULTS["layout"]["elements"]["left_bottom"].get("value", ""))}" />
        </div>
        <div>
          <label>Left Bottom Color</label>
          <input type="color" name="element_left_bottom_color" value="{escape(DEFAULTS["layout"]["elements"]["left_bottom"].get("color", "#212121"))}" />
        </div>
        <div>
          <label><input type="checkbox" name="element_left_bottom_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["left_bottom"].get("is_bold", False))} /> Left Bottom Bold</label>
        </div>

        <div>
          <label>Right Top Element</label>
          <select id="element_right_top_name" name="element_right_top_name">{element_options_by_position["right_top"]}</select>
        </div>
        <div>
          <label>Right Top Custom Value</label>
          <input id="element_right_top_value" type="text" name="element_right_top_value" value="{escape(DEFAULTS["layout"]["elements"]["right_top"].get("value", ""))}" />
        </div>
        <div>
          <label>Right Top Color</label>
          <input type="color" name="element_right_top_color" value="{escape(DEFAULTS["layout"]["elements"]["right_top"].get("color", "#212121"))}" />
        </div>
        <div>
          <label><input type="checkbox" name="element_right_top_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["right_top"].get("is_bold", False))} /> Right Top Bold</label>
        </div>

        <div>
          <label>Right Bottom Element</label>
          <select id="element_right_bottom_name" name="element_right_bottom_name">{element_options_by_position["right_bottom"]}</select>
        </div>
        <div>
          <label>Right Bottom Custom Value</label>
          <input id="element_right_bottom_value" type="text" name="element_right_bottom_value" value="{escape(DEFAULTS["layout"]["elements"]["right_bottom"].get("value", ""))}" />
        </div>
        <div>
          <label>Right Bottom Color</label>
          <input type="color" name="element_right_bottom_color" value="{escape(DEFAULTS["layout"]["elements"]["right_bottom"].get("color", "#212121"))}" />
        </div>
        <div>
          <label><input type="checkbox" name="element_right_bottom_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["right_bottom"].get("is_bold", False))} /> Right Bottom Bold</label>
        </div>

        <div>
          <label>Font Size Level</label>
          <select name="font_size">{font_size_options}</select>
        </div>
        <div>
          <label>Bold Font Size Level</label>
          <select name="bold_font_size">{bold_font_size_options}</select>
        </div>

        <div class="full">
          <label>Font Path</label>
          <input type="text" name="font" value="{escape(DEFAULTS["base"]["font"])}" />
        </div>
        <div class="full">
          <label>Bold Font Path</label>
          <input type="text" name="bold_font" value="{escape(DEFAULTS["base"]["bold_font"])}" />
        </div>
        <div class="full">
          <label>Alternative Font Path</label>
          <input type="text" name="alternative_font" value="{escape(DEFAULTS["base"]["alternative_font"])}" />
        </div>
        <div class="full">
          <label>Alternative Bold Font Path</label>
          <input type="text" name="alternative_bold_font" value="{escape(DEFAULTS["base"]["alternative_bold_font"])}" />
        </div>
      </div>

      <div class="actions">
        <button id="submitBtn" type="submit">开始处理</button>
        <button id="cancelBtn" type="button" style="display:none; max-width: 160px; background: #b42318;">取消任务</button>
        <span>处理完成后会提供 ZIP 下载（包含 report.json）</span>
      </div>
      <div id="status" class="status"></div>

      <div id="resultPanel" class="result">
        <div id="resultSummary"></div>
        <a id="downloadLink" class="download" href="#" target="_blank" rel="noopener" style="display:none;">下载结果 ZIP</a>
        <div id="errorList" class="status"></div>
      </div>
    </form>
  </div>

  <script>
    const form = document.getElementById("processForm");
    const submitBtn = document.getElementById("submitBtn");
    const cancelBtn = document.getElementById("cancelBtn");
    const statusEl = document.getElementById("status");
    const resultPanel = document.getElementById("resultPanel");
    const resultSummary = document.getElementById("resultSummary");
    const errorList = document.getElementById("errorList");
    const downloadLink = document.getElementById("downloadLink");

    let pollTimer = null;
    let currentJobId = null;
    const customValueKey = {json.dumps(CUSTOM_VALUE)};

    const previewCheck = document.getElementById("previewCheck");
    const previewMaxSizeInput = document.getElementById("previewMaxSizeInput");
    const previewQualityInput = document.getElementById("previewQualityInput");
    const logoEnableCheck = document.getElementById("logoEnableCheck");
    const logoPositionInput = document.getElementById("logoPositionInput");
    const whiteMarginCheck = document.getElementById("whiteMarginCheck");
    const whiteMarginWidthInput = document.getElementById("whiteMarginWidthInput");

    function updateDependentInputs() {{
      logoPositionInput.disabled = !logoEnableCheck.checked;
      whiteMarginWidthInput.disabled = !whiteMarginCheck.checked;
      previewMaxSizeInput.disabled = !previewCheck.checked;
      previewQualityInput.disabled = !previewCheck.checked;
    }}

    function updateCustomValueInput(position) {{
      const select = document.getElementById(`element_${{position}}_name`);
      const input = document.getElementById(`element_${{position}}_value`);
      if (!select || !input) {{
        return;
      }}
      input.disabled = select.value !== customValueKey;
    }}

    ["left_top", "left_bottom", "right_top", "right_bottom"].forEach((position) => {{
      const select = document.getElementById(`element_${{position}}_name`);
      if (select) {{
        select.addEventListener("change", () => updateCustomValueInput(position));
      }}
      updateCustomValueInput(position);
    }});

    previewCheck.addEventListener("change", updateDependentInputs);
    logoEnableCheck.addEventListener("change", updateDependentInputs);
    whiteMarginCheck.addEventListener("change", updateDependentInputs);
    updateDependentInputs();

    function setRunningState(running) {{
      submitBtn.disabled = running;
      cancelBtn.style.display = running ? "inline-block" : "none";
      cancelBtn.disabled = !running;
    }}

    function resetResult() {{
      resultPanel.style.display = "none";
      resultSummary.textContent = "";
      errorList.textContent = "";
      downloadLink.style.display = "none";
      downloadLink.href = "#";
    }}

    function renderJob(job) {{
      const percent = job.progress.percent;
      statusEl.textContent = `状态: ${{job.status}} | 进度: ${{job.progress.current}}/${{job.progress.total}} (${{percent}}%)\n${{job.message || ""}}`;

      if (job.status === "done" || job.status === "error" || job.status === "cancelled") {{
        setRunningState(false);
        currentJobId = null;
        if (pollTimer) {{
          clearInterval(pollTimer);
          pollTimer = null;
        }}

        resultPanel.style.display = "block";
        if (job.status === "done") {{
          resultSummary.innerHTML = `<span class="ok">处理完成：</span>输出 ${{job.output_count}} 张，失败 ${{job.error_count}} 张。`;
          downloadLink.href = `/api/jobs/${{job.job_id}}/download`;
          downloadLink.style.display = "inline-block";
        }} else if (job.status === "cancelled") {{
          resultSummary.innerHTML = `<span class="err">任务已取消：</span>已处理 ${{job.output_count}} 张。`;
        }} else {{
          resultSummary.innerHTML = `<span class="err">处理失败：</span>${{job.message}}`;
        }}

        if (job.errors && job.errors.length > 0) {{
          errorList.innerHTML = "失败明细:\n" + job.errors.map(e => `- ${{e.source}} => ${{e.error}}`).join("\n");
        }} else {{
          errorList.textContent = "";
        }}
      }}
    }}

    async function pollJob(jobId) {{
      try {{
        const resp = await fetch(`/api/jobs/${{jobId}}`);
        const payload = await resp.json();
        if (!resp.ok || !payload.ok) {{
          throw new Error(payload.error?.message || `HTTP ${{resp.status}}`);
        }}
        renderJob(payload.job);
      }} catch (err) {{
        statusEl.textContent = `查询任务状态失败: ${{err.message}}`;
      }}
    }}

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      setRunningState(true);
      resetResult();
      statusEl.textContent = "任务提交中...";

      try {{
        const resp = await fetch("/api/process", {{
          method: "POST",
          body: new FormData(form),
        }});
        const payload = await resp.json();
        if (!resp.ok || !payload.ok) {{
          throw new Error(payload.error?.message || `HTTP ${{resp.status}}`);
        }}

        const jobId = payload.job_id;
        currentJobId = jobId;
        statusEl.textContent = `任务已创建: ${{jobId}}，开始轮询进度...`;
        await pollJob(jobId);
        pollTimer = setInterval(() => pollJob(jobId), 800);
      }} catch (err) {{
        setRunningState(false);
        currentJobId = null;
        statusEl.textContent = `提交失败: ${{err.message}}`;
      }}
    }});

    cancelBtn.addEventListener("click", async () => {{
      if (!currentJobId) {{
        return;
      }}
      cancelBtn.disabled = true;
      try {{
        const resp = await fetch(`/api/jobs/${{currentJobId}}/cancel`, {{ method: "POST" }});
        const payload = await resp.json();
        if (!resp.ok || !payload.ok) {{
          throw new Error(payload.error?.message || `HTTP ${{resp.status}}`);
        }}
        renderJob(payload.job);
      }} catch (err) {{
        cancelBtn.disabled = false;
        statusEl.textContent = `取消失败: ${{err.message}}`;
      }}
    }});
  </script>
</body>
</html>
"""
    return html.encode("utf-8")


def _pick_available_port(host: str, preferred_port: int, max_tries: int = 20) -> int:
    # First try caller's preferred port, then scan forward for a free port.
    if preferred_port <= 0:
        return 0
    candidates = [preferred_port + idx for idx in range(max_tries)]
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise OSError(f"No available port found starting from {preferred_port}")


class SemiWebHandler(BaseHTTPRequestHandler):
    server_version = "semi-utils-web/2.0"

    def _path_only(self) -> str:
        return self.path.split("?", 1)[0]

    def do_GET(self) -> None:
        path = self._path_only()
        if path == "/":
            self._send_bytes(HTTPStatus.OK, _build_html(), "text/html; charset=utf-8")
            return

        if path == "/health":
            self._send_json(HTTPStatus.OK.value, _json_bytes({"ok": True, "time": int(time.time())}))
            return

        if path.startswith("/api/jobs/"):
            self._handle_get_job()
            return

        self._send_bytes(HTTPStatus.NOT_FOUND, b"Not Found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = self._path_only()
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            self._handle_cancel_job()
            return

        if path != "/api/process":
            self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "not_found", "Endpoint not found"))
            return

        content_length = _parse_int(self.headers.get("Content-Length"), 0)
        if content_length <= 0:
            self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "bad_request", "Empty request body"))
            return
        if content_length > MAX_REQUEST_BYTES:
            self._send_json(
                *_error_response(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "request_too_large",
                    f"Request exceeds maximum size ({MAX_REQUEST_BYTES} bytes)",
                )
            )
            return

        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_json(
                *_error_response(
                    HTTPStatus.BAD_REQUEST,
                    "bad_content_type",
                    "Content-Type must be multipart/form-data",
                )
            )
            return

        try:
            with JOBS_LOCK:
                if len(JOBS) >= MAX_LIVE_JOBS:
                    self._send_json(
                        *_error_response(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            "job_queue_full",
                            "Too many active jobs. Please retry later.",
                        )
                    )
                    return

            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
                keep_blank_values=True,
            )
        except Exception as exc:
            self._send_json(
                *_error_response(HTTPStatus.BAD_REQUEST, "invalid_form", f"Cannot parse form data: {exc}")
            )
            return

        workspace_dir: Path | None = None
        try:
            config_data = _build_config(form)
            preview_mode = _field_checked(form, "preview")
            preview_max_size = _parse_int(form.getfirst("preview_max_size"), 1600, 200, 8000)
            preview_quality = _parse_int(form.getfirst("preview_quality"), 80, 1, 100)

            workspace_dir = Path(tempfile.mkdtemp(prefix="semi_web_job_"))
            input_dir = workspace_dir / "input"
            input_dir.mkdir(parents=True, exist_ok=True)

            input_paths = _extract_uploads(form, input_dir)

            job_id = uuid.uuid4().hex
            mode = "preview" if preview_mode else "normal"
            now = time.time()
            record = JobRecord(
                job_id=job_id,
                created_at=now,
                updated_at=now,
                status="queued",
                message="Queued",
                mode=mode,
                total=len(input_paths),
                current=0,
                output_count=0,
                workspace_dir=workspace_dir,
                config_data=config_data,
                input_paths=input_paths,
                preview_mode=preview_mode,
                preview_max_size=preview_max_size,
                preview_quality=preview_quality,
            )

            with JOBS_LOCK:
                JOBS[job_id] = record

            worker = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
            worker.start()

            self._send_json(
                HTTPStatus.ACCEPTED.value,
                _json_bytes({
                    "ok": True,
                    "job_id": job_id,
                    "status_url": f"/api/jobs/{job_id}",
                    "cancel_url": f"/api/jobs/{job_id}/cancel",
                    "download_url": f"/api/jobs/{job_id}/download",
                }),
            )
        except ValueError as exc:
            if workspace_dir and workspace_dir.exists():
                shutil.rmtree(workspace_dir, ignore_errors=True)
            self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "invalid_input", str(exc)))
        except Exception as exc:
            if workspace_dir and workspace_dir.exists():
                shutil.rmtree(workspace_dir, ignore_errors=True)
            self._send_json(*_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc)))

    def _handle_get_job(self) -> None:
        # /api/jobs/<job_id> or /api/jobs/<job_id>/download
        parts = [part for part in self._path_only().split("/") if part]
        if len(parts) < 3:
            self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "not_found", "Invalid job path"))
            return

        job_id = parts[2]
        with JOBS_LOCK:
            job = JOBS.get(job_id)

        if not job:
            self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found or expired"))
            return

        if len(parts) == 3:
            self._send_json(HTTPStatus.OK.value, _json_bytes(_serialize_job(job)))
            return

        if len(parts) == 4 and parts[3] == "download":
            if job.status != "done" or not job.zip_path or not job.zip_path.exists():
                self._send_json(
                    *_error_response(
                        HTTPStatus.CONFLICT,
                        "result_not_ready",
                        "Job is not completed yet",
                    )
                )
                return

            payload = job.zip_path.read_bytes()
            filename = job.output_filename or "semi-utils-output.zip"
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                "application/zip",
                {"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return

        self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "not_found", "Invalid job endpoint"))

    def _handle_cancel_job(self) -> None:
        # /api/jobs/<job_id>/cancel
        parts = [part for part in self._path_only().split("/") if part]
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "jobs" or parts[3] != "cancel":
            self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "not_found", "Invalid job path"))
            return

        job_id = parts[2]
        job, state = _request_cancel(job_id)
        if not job:
            self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found or expired"))
            return
        if state == "terminal":
            self._send_json(
                *_error_response(HTTPStatus.CONFLICT, "cannot_cancel", f"Job already finished with status {job.status}")
            )
            return
        self._send_json(HTTPStatus.ACCEPTED.value, _json_bytes(_serialize_job(job)))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: bytes) -> None:
        self._send_bytes(HTTPStatus(status), payload, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str, port: int, open_browser: bool) -> None:
    os.chdir(Path(__file__).resolve().parent)
    _ensure_cleanup_thread()
    log_path = setup_temp_logging(name_prefix="semi-utils-web")

    selected_port = _pick_available_port(host, port)
    if selected_port != port:
        print(f"Requested port {port} is occupied. Switched to {selected_port}.")

    server = ThreadingHTTPServer((host, selected_port), SemiWebHandler)
    real_port = int(server.server_address[1])
    url = f"http://{host}:{real_port}/"

    print(f"Web GUI listening at {url}")
    print(f"Runtime log file: {log_path}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="semi-utils web GUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open browser")
    args = parser.parse_args()
    run_server(args.host, args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
