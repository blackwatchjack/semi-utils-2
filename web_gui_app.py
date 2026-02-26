from __future__ import annotations

import argparse
import copy
import cgi
import io
import json
import mimetypes
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
from typing import Mapping

from PIL import Image

from engine import get_config_spec
from engine import process_images
from enums.constant import CUSTOM_VALUE
from logging_setup import setup_temp_logging
from ui_visibility import managed_paths
from ui_visibility import sanitize_config

SPEC = get_config_spec()
DEFAULTS = copy.deepcopy(SPEC["defaults"])
VISIBILITY_PATHS = managed_paths()

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_REQUEST_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_LIVE_JOBS = 500
DEFAULT_MAX_CONCURRENT_JOBS = 2
DEFAULT_JOB_TTL_SECONDS = 30 * 60
CLEANUP_INTERVAL_SECONDS = 60

JOBS_LOCK = threading.Lock()
JOBS: dict[str, "JobRecord"] = {}
CLEANUP_THREAD_STARTED = False

def _env_int(
    environ: Mapping[str, str],
    key: str,
    default: int,
    low: int | None = None,
    high: int | None = None,
) -> int:
    raw = environ.get(key)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if low is not None and value < low:
        value = low
    if high is not None and value > high:
        value = high
    return value


def _load_runtime_limits(environ: Mapping[str, str] | None = None) -> dict[str, int]:
    env = os.environ if environ is None else environ
    return {
        "max_files": _env_int(env, "SEMI_WEB_MAX_FILES", DEFAULT_MAX_FILES, low=1),
        "max_request_bytes": _env_int(env, "SEMI_WEB_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES, low=1),
        "max_file_bytes": _env_int(env, "SEMI_WEB_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES, low=1),
        "job_ttl_seconds": _env_int(env, "SEMI_WEB_JOB_TTL_SECONDS", DEFAULT_JOB_TTL_SECONDS, low=1),
        "max_concurrent_jobs": _env_int(
            env,
            "SEMI_WEB_MAX_CONCURRENT_JOBS",
            DEFAULT_MAX_CONCURRENT_JOBS,
            low=1,
            high=16,
        ),
    }


_RUNTIME_LIMITS = _load_runtime_limits()
MAX_FILES = _RUNTIME_LIMITS["max_files"]
MAX_REQUEST_BYTES = _RUNTIME_LIMITS["max_request_bytes"]
MAX_FILE_BYTES = _RUNTIME_LIMITS["max_file_bytes"]
JOB_TTL_SECONDS = _RUNTIME_LIMITS["job_ttl_seconds"]
MAX_CONCURRENT_JOBS = _RUNTIME_LIMITS["max_concurrent_jobs"]
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
    result_paths: list[Path | None] = field(default_factory=list)
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
        label = str(item.get("label", value))
        if value == "left" and label == "left":
            label = "左侧"
        elif value == "right" and label == "right":
            label = "右侧"
        rows.append(
            f'<option value="{escape(value)}"{selected_attr}>{escape(label)}</option>'
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
        else:
            element.pop("value", None)

    sanitized, _visibility = sanitize_config(config, DEFAULTS)
    return sanitized


def _build_visibility_payload(config_data: dict[str, Any]) -> dict[str, Any]:
    sanitized, visibility = sanitize_config(config_data, DEFAULTS)
    return {
        "ok": True,
        "visibility": {path: bool(visibility.get(path, True)) for path in VISIBILITY_PATHS},
        "config": sanitized,
    }


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


def _guess_image_content_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(str(path))
    if guessed and guessed.startswith("image/"):
        return guessed
    return "application/octet-stream"


def _serialize_job(job: JobRecord) -> dict[str, Any]:
    done = job.status == "done"
    results_available = [bool(path and Path(path).exists()) for path in job.result_paths]
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
            "input_files": [path.name for path in job.input_paths],
            "results_available": results_available,
            "result_url_template": f"/api/jobs/{job.job_id}/results/{{index}}",
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


def _create_zip(output_files: list[Path], target_zip: Path) -> None:
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in output_files:
            zf.write(file_path, arcname=file_path.name)


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


def _set_job_result_path(job_id: str, index: int, result_path: Path) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if index < 0 or index >= len(job.result_paths):
            return
        job.result_paths[index] = Path(result_path)
        job.updated_at = time.time()


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
    preview_lookup: dict[Path, Path] = {}
    input_index_map: dict[Path, int] = {
        source_path: index
        for index, source_path in enumerate(job.input_paths)
    }
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
                result_candidate: Path | None = None
                if job.preview_mode:
                    result_candidate = preview_lookup.get(source_path)
                elif output_dir is not None:
                    result_candidate = output_dir / source_path.name
                if result_candidate and result_candidate.exists():
                    _set_job_result_path(job_id, current - 1, result_candidate)
            else:
                msg = f"Failed {source_path.name}: {error}"
            _update_job(job_id, current=current, total=total, message=msg)

        def on_error(source_path: Path, exc: Exception) -> None:
            error_items.append({"source": str(source_path), "error": str(exc)})

        def on_preview(source_path: Path, preview_path: Path) -> None:
            normalized = Path(preview_path)
            preview_paths.append(normalized)
            preview_lookup[Path(source_path)] = normalized
            index = input_index_map.get(Path(source_path))
            if index is not None:
                _set_job_result_path(job_id, index, normalized)

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
            on_preview=on_preview if job.preview_mode else None,
        )

        if job.preview_mode:
            output_files = [path for path in preview_paths if path.exists()]
            filename = "semi-utils-preview.zip"
        else:
            output_files = [
                (output_dir / source_path.name)
                for source_path in job.input_paths
                if output_dir is not None and (output_dir / source_path.name).exists()
            ]
            filename = "semi-utils-output.zip"
        processed_errors = [{"source": str(src), "error": str(exc)} for src, exc in errors]

        zip_path = job.workspace_dir / filename
        _create_zip(output_files, zip_path)

        _update_job(
            job_id,
            status="done",
            message="Completed",
            output_count=len(output_files),
            errors=processed_errors,
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
  <title>semi-utils 网页界面</title>
  <style>
    :root {{
      --page-bg: #f1f4f8;
      --panel-bg: #ffffff;
      --line: #d7dee9;
      --text: #102135;
      --muted: #50647d;
      --brand: #0f5f9d;
      --brand-strong: #084c83;
      --danger: #b42318;
      --ok: #1f7a4f;
      --shadow: 0 8px 24px rgba(12, 41, 69, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at 0% 0%, #fdfefe 0%, #f5f8fb 42%, #eaf0f6 100%);
      color: var(--text);
    }}
    .page {{
      max-width: 1580px;
      margin: 16px auto;
      padding: 12px;
    }}
    .header {{
      margin-bottom: 10px;
    }}
    .title {{
      margin: 0;
      font-size: 28px;
      font-weight: 700;
      letter-spacing: 0.2px;
    }}
    .subtitle {{
      margin-top: 4px;
      font-size: 14px;
      color: var(--muted);
    }}
    .workspace {{
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .main-grid {{
      display: grid;
      grid-template-columns: 290px minmax(380px, 1fr) 380px;
      gap: 10px;
      min-height: 620px;
    }}
    .panel {{
      background: var(--panel-bg);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
    }}
    .panel-title {{
      font-size: 14px;
      font-weight: 700;
      color: #17314e;
      padding: 10px 12px 0 12px;
    }}
    .left-panel {{
      display: flex;
      flex-direction: column;
      overflow: hidden;
      transition: border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
    }}
    .left-panel.drop-active {{
      border-color: #6aa4d3;
      box-shadow: 0 0 0 2px rgba(57, 137, 199, 0.18), var(--shadow);
      background: linear-gradient(165deg, #fafdff, #f2f8ff);
    }}
    .thumb-toolbar {{
      display: flex;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }}
    .thumb-toolbar button {{
      flex: 1;
      padding: 8px 10px;
      border-radius: 9px;
      border: 1px solid #bdd0e5;
      background: #e9f2fb;
      color: #0b4d80;
      cursor: pointer;
      font-weight: 600;
      font-size: 13px;
    }}
    .thumb-toolbar button:disabled {{
      opacity: 0.6;
      cursor: not-allowed;
    }}
    .thumb-list {{
      flex: 1;
      overflow: auto;
      padding: 8px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .thumb-item {{
      display: grid;
      grid-template-columns: 70px 1fr auto;
      gap: 8px;
      align-items: center;
      padding: 6px;
      border: 1px solid #dfe8f3;
      border-radius: 10px;
      background: #fbfdff;
      cursor: pointer;
    }}
    .thumb-item.active {{
      border-color: #69a6d8;
      box-shadow: inset 0 0 0 1px #69a6d8;
      background: #f3f9ff;
    }}
    .thumb-item img {{
      width: 68px;
      height: 68px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid #d6e2ef;
      background: #f5f7fa;
    }}
    .thumb-name {{
      font-size: 12px;
      color: #23374f;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .thumb-done {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #c7d3e2;
    }}
    .thumb-done.done {{
      background: var(--ok);
    }}
    .center-panel {{
      display: flex;
      flex-direction: column;
      min-width: 0;
    }}
    .preview-toolbar {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }}
    .preview-toolbar button, .preview-toolbar select {{
      border-radius: 9px;
      border: 1px solid #c9d7e7;
      background: #f8fbff;
      color: #1a3651;
      padding: 6px 9px;
      font-size: 13px;
    }}
    .preview-stage {{
      flex: 1;
      margin: 10px;
      border: 1px dashed #bfd0e5;
      border-radius: 10px;
      background: linear-gradient(150deg, #f8fbff, #f4f8fc);
      overflow: auto;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 380px;
      position: relative;
    }}
    .preview-stage img {{
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      display: none;
    }}
    .preview-stage img.zoom-100 {{
      max-width: none;
      max-height: none;
    }}
    .preview-empty {{
      color: var(--muted);
      font-size: 14px;
      padding: 12px;
      text-align: center;
      line-height: 1.6;
    }}
    .preview-meta {{
      padding: 0 12px 10px 12px;
      font-size: 13px;
      color: #38526f;
    }}
    .right-panel {{
      display: flex;
      flex-direction: column;
      min-width: 0;
    }}
    .param-scroll {{
      flex: 1;
      overflow: auto;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .group {{
      border: 1px solid #dbe5f1;
      border-radius: 10px;
      padding: 8px;
      background: #fcfdff;
    }}
    .group-title {{
      margin: 0 0 6px 0;
      font-size: 13px;
      font-weight: 700;
      color: #15324d;
    }}
    .field-row {{
      margin-bottom: 7px;
    }}
    .field-row:last-child {{
      margin-bottom: 0;
    }}
    .field-row label {{
      display: block;
      font-size: 12px;
      color: #35506d;
      margin-bottom: 4px;
    }}
    .field-row input,
    .field-row select {{
      width: 100%;
      border: 1px solid #cfd9e5;
      border-radius: 8px;
      padding: 7px 9px;
      font-size: 13px;
      color: #153048;
      background: #fff;
    }}
    .check-row {{
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 7px;
      font-size: 13px;
      color: #22415f;
    }}
    .check-row input[type="checkbox"] {{
      width: auto;
    }}
    .field-hidden {{
      display: none !important;
    }}
    .actions {{
      padding: 10px;
      border-top: 1px solid var(--line);
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .actions button {{
      flex: 1;
      min-width: 120px;
      border-radius: 9px;
      border: 0;
      padding: 9px 10px;
      font-weight: 700;
      cursor: pointer;
    }}
    #submitBtn {{
      background: linear-gradient(125deg, var(--brand), var(--brand-strong));
      color: #fff;
    }}
    #cancelBtn {{
      background: #fbe7e7;
      color: var(--danger);
      border: 1px solid #f1c9c9;
    }}
    #downloadLink {{
      width: 100%;
      text-align: center;
      text-decoration: none;
      border: 1px dashed #85a9cb;
      border-radius: 8px;
      padding: 8px;
      color: #0b4f82;
      font-size: 13px;
      display: none;
    }}
    .bottom-grid {{
      display: grid;
      grid-template-columns: 1.1fr 1fr;
      gap: 10px;
    }}
    .bottom-panel {{
      padding: 10px 12px;
      min-height: 100px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .status-line {{
      font-size: 13px;
      color: #243f5b;
      white-space: pre-wrap;
    }}
    .muted {{
      color: var(--muted);
      font-size: 13px;
    }}
    .error-list {{
      color: var(--danger);
      font-size: 13px;
      white-space: pre-wrap;
      max-height: 140px;
      overflow: auto;
    }}
    progress {{
      width: 100%;
      height: 11px;
    }}
    @media (max-width: 1240px) {{
      .main-grid {{
        grid-template-columns: 1fr;
        min-height: 0;
      }}
      .bottom-grid {{
        grid-template-columns: 1fr;
      }}
      .center-panel .preview-stage {{
        min-height: 300px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="header">
      <h1 class="title">semi-utils 三栏界面（Web）</h1>
      <div class="subtitle">左侧管理输入与缩略图，中间仅展示处理后预览，右侧配置参数；底部提供状态与说明。</div>
    </header>

    <form id="processForm" class="workspace" method="post" action="/api/process" enctype="multipart/form-data">
      <div class="main-grid">
        <section id="leftPanel" class="panel left-panel">
          <div class="panel-title">输入图片与缩略图</div>
          <div class="thumb-toolbar">
            <button id="addFilesBtn" type="button">上传</button>
            <button id="removeFileBtn" type="button">移除</button>
            <button id="clearFilesBtn" type="button">清空</button>
          </div>
          <input id="fileInput" type="file" accept=".jpg,.jpeg,.png,.JPG,.JPEG,.PNG" multiple style="display:none;" />
          <div id="thumbList" class="thumb-list"></div>
        </section>

        <section class="panel center-panel">
          <div class="panel-title">处理后预览</div>
          <div class="preview-toolbar">
            <button id="prevBtn" type="button">上一张</button>
            <button id="nextBtn" type="button">下一张</button>
            <select id="zoomSelect">
              <option value="FIT">FIT</option>
              <option value="100">100%</option>
            </select>
            <span class="muted">仅展示处理结果图</span>
          </div>
          <div class="preview-stage" id="previewStage">
            <img id="resultPreview" alt="处理后预览" />
            <div id="previewEmpty" class="preview-empty">请先在左侧上传图片并开始处理。</div>
          </div>
          <div id="previewMeta" class="preview-meta"></div>
        </section>

        <section class="panel right-panel">
          <div class="panel-title">参数配置</div>
          <div class="param-scroll">
            <div class="group">
              <div class="group-title">布局与输出</div>
              <div class="field-row" data-field-path="layout.type">
                <label>布局</label>
                <select id="layoutInput" name="layout">{layout_options}</select>
              </div>
              <div class="field-row" data-field-path="base.quality">
                <label>画质 (1-100)</label>
                <input id="qualityInput" type="number" name="quality" min="1" max="100" value="{DEFAULTS["base"]["quality"]}" />
              </div>
              <div class="field-row" data-field-path="layout.background_color">
                <label>背景颜色</label>
                <input id="backgroundColorInput" type="color" name="background_color" value="{escape(DEFAULTS["layout"]["background_color"])}" />
              </div>
              <div class="check-row field-row" data-field-path="layout.logo_enable">
                <input id="logoEnableCheck" type="checkbox" name="logo_enable"{_checked_attr(DEFAULTS["layout"]["logo_enable"])} />
                <label for="logoEnableCheck" style="margin:0;">启用徽标</label>
              </div>
              <div class="field-row" data-field-path="layout.logo_position">
                <label>徽标位置</label>
                <select id="logoPositionInput" name="logo_position">{logo_position_options}</select>
              </div>
              <div class="check-row">
                <input id="previewCheck" type="checkbox" name="preview" />
                <label for="previewCheck" style="margin:0;">预览模式（下载 ZIP 为预览图）</label>
              </div>
              <div class="field-row">
                <label>预览最大边长</label>
                <input id="previewMaxSizeInput" type="number" name="preview_max_size" min="200" max="8000" value="1600" />
              </div>
              <div class="field-row">
                <label>预览质量</label>
                <input id="previewQualityInput" type="number" name="preview_quality" min="1" max="100" value="80" />
              </div>
            </div>

            <div class="group">
              <div class="group-title">文字参数</div>
              <div class="field-row" data-field-path="layout.elements.left_top.name">
                <label>左上元素</label>
                <select id="element_left_top_name" name="element_left_top_name">{element_options_by_position["left_top"]}</select>
              </div>
              <div class="field-row" data-field-path="layout.elements.left_top.value">
                <label>左上自定义内容</label>
                <input id="element_left_top_value" type="text" name="element_left_top_value" value="{escape(DEFAULTS["layout"]["elements"]["left_top"].get("value", ""))}" />
              </div>
              <div class="field-row" data-field-path="layout.elements.left_top.color">
                <label>左上颜色</label>
                <input id="element_left_top_color" type="color" name="element_left_top_color" value="{escape(DEFAULTS["layout"]["elements"]["left_top"].get("color", "#212121"))}" />
              </div>
              <div class="check-row field-row" data-field-path="layout.elements.left_top.is_bold">
                <input id="element_left_top_is_bold" type="checkbox" name="element_left_top_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["left_top"].get("is_bold", False))} />
                <label for="element_left_top_is_bold" style="margin:0;">左上加粗</label>
              </div>

              <div class="field-row" data-field-path="layout.elements.left_bottom.name">
                <label>左下元素</label>
                <select id="element_left_bottom_name" name="element_left_bottom_name">{element_options_by_position["left_bottom"]}</select>
              </div>
              <div class="field-row" data-field-path="layout.elements.left_bottom.value">
                <label>左下自定义内容</label>
                <input id="element_left_bottom_value" type="text" name="element_left_bottom_value" value="{escape(DEFAULTS["layout"]["elements"]["left_bottom"].get("value", ""))}" />
              </div>
              <div class="field-row" data-field-path="layout.elements.left_bottom.color">
                <label>左下颜色</label>
                <input id="element_left_bottom_color" type="color" name="element_left_bottom_color" value="{escape(DEFAULTS["layout"]["elements"]["left_bottom"].get("color", "#212121"))}" />
              </div>
              <div class="check-row field-row" data-field-path="layout.elements.left_bottom.is_bold">
                <input id="element_left_bottom_is_bold" type="checkbox" name="element_left_bottom_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["left_bottom"].get("is_bold", False))} />
                <label for="element_left_bottom_is_bold" style="margin:0;">左下加粗</label>
              </div>

              <div class="field-row" data-field-path="layout.elements.right_top.name">
                <label>右上元素</label>
                <select id="element_right_top_name" name="element_right_top_name">{element_options_by_position["right_top"]}</select>
              </div>
              <div class="field-row" data-field-path="layout.elements.right_top.value">
                <label>右上自定义内容</label>
                <input id="element_right_top_value" type="text" name="element_right_top_value" value="{escape(DEFAULTS["layout"]["elements"]["right_top"].get("value", ""))}" />
              </div>
              <div class="field-row" data-field-path="layout.elements.right_top.color">
                <label>右上颜色</label>
                <input id="element_right_top_color" type="color" name="element_right_top_color" value="{escape(DEFAULTS["layout"]["elements"]["right_top"].get("color", "#212121"))}" />
              </div>
              <div class="check-row field-row" data-field-path="layout.elements.right_top.is_bold">
                <input id="element_right_top_is_bold" type="checkbox" name="element_right_top_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["right_top"].get("is_bold", False))} />
                <label for="element_right_top_is_bold" style="margin:0;">右上加粗</label>
              </div>

              <div class="field-row" data-field-path="layout.elements.right_bottom.name">
                <label>右下元素</label>
                <select id="element_right_bottom_name" name="element_right_bottom_name">{element_options_by_position["right_bottom"]}</select>
              </div>
              <div class="field-row" data-field-path="layout.elements.right_bottom.value">
                <label>右下自定义内容</label>
                <input id="element_right_bottom_value" type="text" name="element_right_bottom_value" value="{escape(DEFAULTS["layout"]["elements"]["right_bottom"].get("value", ""))}" />
              </div>
              <div class="field-row" data-field-path="layout.elements.right_bottom.color">
                <label>右下颜色</label>
                <input id="element_right_bottom_color" type="color" name="element_right_bottom_color" value="{escape(DEFAULTS["layout"]["elements"]["right_bottom"].get("color", "#212121"))}" />
              </div>
              <div class="check-row field-row" data-field-path="layout.elements.right_bottom.is_bold">
                <input id="element_right_bottom_is_bold" type="checkbox" name="element_right_bottom_is_bold"{_checked_attr(DEFAULTS["layout"]["elements"]["right_bottom"].get("is_bold", False))} />
                <label for="element_right_bottom_is_bold" style="margin:0;">右下加粗</label>
              </div>
            </div>

            <div class="group">
              <div class="group-title">全局效果</div>
              <div class="check-row field-row" data-field-path="global.shadow.enable">
                <input id="shadowCheck" type="checkbox" name="shadow"{_checked_attr(DEFAULTS["global"]["shadow"]["enable"])} />
                <label for="shadowCheck" style="margin:0;">阴影</label>
              </div>
              <div class="check-row field-row" data-field-path="global.white_margin.enable">
                <input id="whiteMarginCheck" type="checkbox" name="white_margin"{_checked_attr(DEFAULTS["global"]["white_margin"]["enable"])} />
                <label for="whiteMarginCheck" style="margin:0;">白边</label>
              </div>
              <div class="field-row" data-field-path="global.white_margin.width">
                <label>白边宽度 (%)</label>
                <input id="whiteMarginWidthInput" type="number" name="white_margin_width" min="0" max="30" value="{DEFAULTS["global"]["white_margin"]["width"]}" />
              </div>
              <div class="check-row field-row" data-field-path="global.padding_with_original_ratio.enable">
                <input id="paddingRatioCheck" type="checkbox" name="padding_ratio"{_checked_attr(DEFAULTS["global"]["padding_with_original_ratio"]["enable"])} />
                <label for="paddingRatioCheck" style="margin:0;">按原图比例填充</label>
              </div>
              <div class="check-row field-row" data-field-path="global.focal_length.use_equivalent_focal_length">
                <input id="equivalentFocalCheck" type="checkbox" name="equivalent_focal_length"{_checked_attr(DEFAULTS["global"]["focal_length"]["use_equivalent_focal_length"])} />
                <label for="equivalentFocalCheck" style="margin:0;">使用等效焦距</label>
              </div>
            </div>

            <div class="group">
              <div class="group-title">字体设置</div>
              <div class="field-row" data-field-path="base.font_size">
                <label>字体大小级别</label>
                <select id="fontSizeInput" name="font_size">{font_size_options}</select>
              </div>
              <div class="field-row" data-field-path="base.bold_font_size">
                <label>加粗字体大小级别</label>
                <select id="boldFontSizeInput" name="bold_font_size">{bold_font_size_options}</select>
              </div>
              <div class="field-row" data-field-path="base.font">
                <label>字体路径</label>
                <input id="fontInput" type="text" name="font" value="{escape(DEFAULTS["base"]["font"])}" />
              </div>
              <div class="field-row" data-field-path="base.bold_font">
                <label>加粗字体路径</label>
                <input id="boldFontInput" type="text" name="bold_font" value="{escape(DEFAULTS["base"]["bold_font"])}" />
              </div>
              <div class="field-row" data-field-path="base.alternative_font">
                <label>备用字体路径</label>
                <input id="alternativeFontInput" type="text" name="alternative_font" value="{escape(DEFAULTS["base"]["alternative_font"])}" />
              </div>
              <div class="field-row" data-field-path="base.alternative_bold_font">
                <label>备用加粗字体路径</label>
                <input id="alternativeBoldFontInput" type="text" name="alternative_bold_font" value="{escape(DEFAULTS["base"]["alternative_bold_font"])}" />
              </div>
            </div>
          </div>

          <div class="actions">
            <button id="submitBtn" type="submit">开始处理</button>
            <button id="cancelBtn" type="button" style="display:none;">取消任务</button>
            <a id="downloadLink" href="#" target="_blank" rel="noopener">下载结果 ZIP</a>
          </div>
        </section>
      </div>

      <div class="bottom-grid">
        <section class="panel bottom-panel">
          <div class="panel-title" style="padding:0;">状态与进度</div>
          <div id="statusText" class="status-line">就绪</div>
          <progress id="progressBar" max="100" value="0"></progress>
          <div id="currentFileText" class="muted">当前文件：-</div>
        </section>
        <section class="panel bottom-panel">
          <div class="panel-title" style="padding:0;">说明与错误汇总</div>
          <div id="summaryText" class="status-line muted">提示：切换布局后，参数会自动收敛并重置隐藏项。</div>
          <div id="errorList" class="error-list"></div>
        </section>
      </div>
    </form>
  </div>

  <script>
    var form = document.getElementById("processForm");
    var leftPanel = document.getElementById("leftPanel");
    var fileInput = document.getElementById("fileInput");
    var addFilesBtn = document.getElementById("addFilesBtn");
    var removeFileBtn = document.getElementById("removeFileBtn");
    var clearFilesBtn = document.getElementById("clearFilesBtn");
    var thumbList = document.getElementById("thumbList");
    var submitBtn = document.getElementById("submitBtn");
    var cancelBtn = document.getElementById("cancelBtn");
    var downloadLink = document.getElementById("downloadLink");
    var statusText = document.getElementById("statusText");
    var progressBar = document.getElementById("progressBar");
    var currentFileText = document.getElementById("currentFileText");
    var summaryText = document.getElementById("summaryText");
    var errorList = document.getElementById("errorList");
    var prevBtn = document.getElementById("prevBtn");
    var nextBtn = document.getElementById("nextBtn");
    var zoomSelect = document.getElementById("zoomSelect");
    var resultPreview = document.getElementById("resultPreview");
    var previewEmpty = document.getElementById("previewEmpty");
    var previewMeta = document.getElementById("previewMeta");

    var layoutInput = document.getElementById("layoutInput");
    var qualityInput = document.getElementById("qualityInput");
    var backgroundColorInput = document.getElementById("backgroundColorInput");
    var logoEnableCheck = document.getElementById("logoEnableCheck");
    var logoPositionInput = document.getElementById("logoPositionInput");
    var shadowCheck = document.getElementById("shadowCheck");
    var whiteMarginCheck = document.getElementById("whiteMarginCheck");
    var whiteMarginWidthInput = document.getElementById("whiteMarginWidthInput");
    var paddingRatioCheck = document.getElementById("paddingRatioCheck");
    var equivalentFocalCheck = document.getElementById("equivalentFocalCheck");
    var fontSizeInput = document.getElementById("fontSizeInput");
    var boldFontSizeInput = document.getElementById("boldFontSizeInput");
    var fontInput = document.getElementById("fontInput");
    var boldFontInput = document.getElementById("boldFontInput");
    var alternativeFontInput = document.getElementById("alternativeFontInput");
    var alternativeBoldFontInput = document.getElementById("alternativeBoldFontInput");
    var previewCheck = document.getElementById("previewCheck");
    var previewMaxSizeInput = document.getElementById("previewMaxSizeInput");
    var previewQualityInput = document.getElementById("previewQualityInput");

    var customValueKey = {json.dumps(CUSTOM_VALUE)};
    var maxFiles = {MAX_FILES};
    var maxFileBytes = {MAX_FILE_BYTES};
    var positions = ["left_top", "left_bottom", "right_top", "right_bottom"];
    var inputNames = [];
    var inputFiles = [];
    var previewUrls = [];
    var selectedIndex = -1;
    var currentJobId = null;
    var pollTimer = null;
    var lastJob = null;
    var resultsAvailable = [];
    var currentVisibility = {{}};
    var visibilitySyncTimer = null;
    var isApplyingConfig = false;

    function getErrorMessage(payload, statusCode) {{
      if (payload && payload.error && payload.error.message) {{
        return payload.error.message;
      }}
      return "HTTP " + statusCode;
    }}

    function requestJson(method, url, body, onSuccess, onError) {{
      if (typeof XMLHttpRequest === "undefined") {{
        onError("当前环境不支持网络请求能力");
        return;
      }}
      var xhr = new XMLHttpRequest();
      xhr.open(method, url, true);
      xhr.onreadystatechange = function() {{
        if (xhr.readyState !== 4) {{
          return;
        }}
        var payload = null;
        try {{
          payload = JSON.parse(xhr.responseText);
        }} catch (_err) {{
          onError("响应解析失败");
          return;
        }}
        if (xhr.status >= 200 && xhr.status < 300 && payload && payload.ok) {{
          onSuccess(payload);
          return;
        }}
        onError(getErrorMessage(payload, xhr.status));
      }};
      xhr.onerror = function() {{
        onError("网络请求失败");
      }};
      xhr.send(body || null);
    }}

    function setRunningState(running) {{
      submitBtn.disabled = running;
      cancelBtn.style.display = running ? "inline-block" : "none";
      cancelBtn.disabled = !running;
      addFilesBtn.disabled = running;
      removeFileBtn.disabled = running;
      clearFilesBtn.disabled = running;
    }}

    function releaseAllUrls() {{
      for (var i = 0; i < previewUrls.length; i += 1) {{
        try {{ URL.revokeObjectURL(previewUrls[i]); }} catch (_err) {{}}
      }}
      previewUrls = [];
    }}

    function isAllowedImageExt(filename) {{
      if (!filename) {{
        return false;
      }}
      var lower = String(filename).toLowerCase();
      return lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".png");
    }}

    function fileIdentityKey(file) {{
      return (
        String(file.name || "") +
        "|" +
        String(file.size || 0) +
        "|" +
        String(file.lastModified || 0)
      );
    }}

    function buildAddSummary(sourceLabel, result) {{
      var details = [];
      if (result.duplicate > 0) {{
        details.push("重复 " + result.duplicate + " 张");
      }}
      if (result.invalidType > 0) {{
        details.push("格式不支持 " + result.invalidType + " 张");
      }}
      if (result.tooLarge > 0) {{
        details.push("超过单文件限制 " + result.tooLarge + " 张");
      }}
      if (result.limitExceeded > 0) {{
        details.push("超过最大数量 " + result.limitExceeded + " 张");
      }}
      if (result.emptyName > 0) {{
        details.push("无有效文件名 " + result.emptyName + " 项");
      }}

      if (result.added > 0 && details.length === 0) {{
        return sourceLabel + "新增 " + result.added + " 张图片。";
      }}
      if (result.added > 0) {{
        return sourceLabel + "新增 " + result.added + " 张，跳过：" + details.join("，") + "。";
      }}
      if (details.length > 0) {{
        return sourceLabel + "未新增图片，跳过：" + details.join("，") + "。";
      }}
      return sourceLabel + "未新增图片。";
    }}

    function addFiles(fileList, sourceLabel) {{
      if (submitBtn.disabled) {{
        summaryText.textContent = "处理中不可添加图片，请等待任务完成。";
        return;
      }}
      if (!fileList || !fileList.length) {{
        return;
      }}

      var label = sourceLabel || "上传";
      var result = {{
        added: 0,
        duplicate: 0,
        invalidType: 0,
        tooLarge: 0,
        limitExceeded: 0,
        emptyName: 0,
      }};

      var existingKeys = new Set();
      for (var i = 0; i < inputFiles.length; i += 1) {{
        existingKeys.add(fileIdentityKey(inputFiles[i]));
      }}

      var acceptedFiles = [];
      for (var index = 0; index < fileList.length; index += 1) {{
        var file = fileList[index];
        if (!file || !file.name) {{
          result.emptyName += 1;
          continue;
        }}

        var identity = fileIdentityKey(file);
        if (existingKeys.has(identity)) {{
          result.duplicate += 1;
          continue;
        }}
        if (!isAllowedImageExt(file.name)) {{
          result.invalidType += 1;
          continue;
        }}
        if (file.size > maxFileBytes) {{
          result.tooLarge += 1;
          continue;
        }}
        if (inputFiles.length + acceptedFiles.length >= maxFiles) {{
          result.limitExceeded += 1;
          continue;
        }}

        existingKeys.add(identity);
        acceptedFiles.push(file);
      }}

      for (var acceptedIndex = 0; acceptedIndex < acceptedFiles.length; acceptedIndex += 1) {{
        var accepted = acceptedFiles[acceptedIndex];
        inputFiles.push(accepted);
        inputNames.push(accepted.name);
        previewUrls.push(URL.createObjectURL(accepted));
      }}

      result.added = acceptedFiles.length;
      if (selectedIndex < 0 && inputFiles.length > 0) {{
        selectedIndex = 0;
      }}
      renderThumbList();
      renderPreview();
      summaryText.textContent = buildAddSummary(label, result);
    }}

    function removeSelectedFile() {{
      if (selectedIndex < 0 || selectedIndex >= inputFiles.length) {{
        return;
      }}
      try {{ URL.revokeObjectURL(previewUrls[selectedIndex]); }} catch (_err) {{}}
      inputFiles.splice(selectedIndex, 1);
      inputNames.splice(selectedIndex, 1);
      previewUrls.splice(selectedIndex, 1);
      if (resultsAvailable.length > selectedIndex) {{
        resultsAvailable.splice(selectedIndex, 1);
      }}
      if (selectedIndex >= inputFiles.length) {{
        selectedIndex = inputFiles.length - 1;
      }}
      renderThumbList();
      renderPreview();
    }}

    function clearFiles() {{
      releaseAllUrls();
      inputFiles = [];
      inputNames = [];
      resultsAvailable = [];
      selectedIndex = -1;
      renderThumbList();
      renderPreview();
    }}

    function selectIndex(index) {{
      if (index < 0 || index >= inputNames.length) {{
        return;
      }}
      selectedIndex = index;
      renderThumbList();
      renderPreview();
    }}

    function renderThumbList() {{
      thumbList.innerHTML = "";
      var names = inputNames;
      if (names.length === 0 && lastJob && lastJob.input_files) {{
        names = lastJob.input_files;
      }}
      for (var i = 0; i < names.length; i += 1) {{
        var item = document.createElement("div");
        item.className = "thumb-item" + (i === selectedIndex ? " active" : "");
        item.dataset.index = String(i);

        var img = document.createElement("img");
        if (previewUrls[i]) {{
          img.src = previewUrls[i];
        }} else {{
          img.alt = "No thumb";
        }}
        item.appendChild(img);

        var name = document.createElement("div");
        name.className = "thumb-name";
        name.textContent = names[i];
        item.appendChild(name);

        var done = document.createElement("div");
        done.className = "thumb-done" + (resultsAvailable[i] ? " done" : "");
        item.appendChild(done);

        item.addEventListener("click", function(event) {{
          var idx = parseInt(event.currentTarget.dataset.index, 10);
          selectIndex(idx);
        }});
        thumbList.appendChild(item);
      }}
      removeFileBtn.disabled = names.length === 0;
      clearFilesBtn.disabled = names.length === 0;
      updateNavButtons();
    }}

    function updateNavButtons() {{
      var total = inputNames.length || (lastJob && lastJob.input_files ? lastJob.input_files.length : 0);
      prevBtn.disabled = selectedIndex <= 0;
      nextBtn.disabled = selectedIndex < 0 || selectedIndex >= total - 1;
    }}

    function applyZoomMode() {{
      if (zoomSelect.value === "100") {{
        resultPreview.classList.add("zoom-100");
      }} else {{
        resultPreview.classList.remove("zoom-100");
      }}
    }}

    function renderPreview() {{
      updateNavButtons();
      var names = inputNames.length ? inputNames : (lastJob && lastJob.input_files ? lastJob.input_files : []);
      if (selectedIndex < 0 || selectedIndex >= names.length) {{
        resultPreview.style.display = "none";
        previewEmpty.style.display = "block";
        previewEmpty.textContent = "请先在左侧上传图片并选择文件。";
        previewMeta.textContent = "";
        currentFileText.textContent = "当前文件：-";
        return;
      }}

      currentFileText.textContent = "当前文件：" + names[selectedIndex];
      var total = names.length;
      previewMeta.textContent = "索引 " + (selectedIndex + 1) + " / " + total;

      if (!currentJobId || !resultsAvailable[selectedIndex]) {{
        resultPreview.style.display = "none";
        previewEmpty.style.display = "block";
        previewEmpty.textContent = "该索引的处理结果暂不可用，请先开始处理或等待该张完成。";
        return;
      }}

      var stamp = lastJob && lastJob.updated_at ? String(lastJob.updated_at) : String(Date.now());
      var src = "/api/jobs/" + currentJobId + "/results/" + selectedIndex + "?t=" + encodeURIComponent(stamp);
      if (resultPreview.dataset.src !== src) {{
        resultPreview.dataset.src = src;
        resultPreview.src = src;
      }}
      resultPreview.style.display = "block";
      previewEmpty.style.display = "none";
      applyZoomMode();
    }}

    function setFormValuesFromConfig(config) {{
      if (!config) {{
        return;
      }}
      layoutInput.value = config.layout.type;
      qualityInput.value = String(config.base.quality);
      backgroundColorInput.value = config.layout.background_color;
      logoEnableCheck.checked = !!config.layout.logo_enable;
      logoPositionInput.value = config.layout.logo_position;
      shadowCheck.checked = !!config.global.shadow.enable;
      whiteMarginCheck.checked = !!config.global.white_margin.enable;
      whiteMarginWidthInput.value = String(config.global.white_margin.width);
      paddingRatioCheck.checked = !!config.global.padding_with_original_ratio.enable;
      equivalentFocalCheck.checked = !!config.global.focal_length.use_equivalent_focal_length;
      fontSizeInput.value = String(config.base.font_size);
      boldFontSizeInput.value = String(config.base.bold_font_size);
      fontInput.value = config.base.font;
      boldFontInput.value = config.base.bold_font;
      alternativeFontInput.value = config.base.alternative_font;
      alternativeBoldFontInput.value = config.base.alternative_bold_font;

      for (var i = 0; i < positions.length; i += 1) {{
        var p = positions[i];
        var element = config.layout.elements[p];
        document.getElementById("element_" + p + "_name").value = element.name;
        document.getElementById("element_" + p + "_color").value = element.color;
        document.getElementById("element_" + p + "_is_bold").checked = !!element.is_bold;
        document.getElementById("element_" + p + "_value").value = element.value || "";
      }}
    }}

    function applyVisibility(visibility) {{
      currentVisibility = visibility || {{}};
      var fields = document.querySelectorAll("[data-field-path]");
      for (var i = 0; i < fields.length; i += 1) {{
        var row = fields[i];
        var path = row.getAttribute("data-field-path");
        var isVisible = currentVisibility[path] !== false;
        if (isVisible) {{
          row.classList.remove("field-hidden");
        }} else {{
          row.classList.add("field-hidden");
        }}
      }}
      previewMaxSizeInput.disabled = !previewCheck.checked;
      previewQualityInput.disabled = !previewCheck.checked;
    }}

    function buildConfigFormData() {{
      var data = new FormData();
      data.append("layout", layoutInput.value);
      data.append("quality", qualityInput.value);
      data.append("background_color", backgroundColorInput.value);
      data.append("logo_position", logoPositionInput.value);
      data.append("white_margin_width", whiteMarginWidthInput.value);
      data.append("font_size", fontSizeInput.value);
      data.append("bold_font_size", boldFontSizeInput.value);
      data.append("font", fontInput.value);
      data.append("bold_font", boldFontInput.value);
      data.append("alternative_font", alternativeFontInput.value);
      data.append("alternative_bold_font", alternativeBoldFontInput.value);
      data.append("preview_max_size", previewMaxSizeInput.value);
      data.append("preview_quality", previewQualityInput.value);

      if (logoEnableCheck.checked) data.append("logo_enable", "on");
      if (shadowCheck.checked) data.append("shadow", "on");
      if (whiteMarginCheck.checked) data.append("white_margin", "on");
      if (paddingRatioCheck.checked) data.append("padding_ratio", "on");
      if (equivalentFocalCheck.checked) data.append("equivalent_focal_length", "on");
      if (previewCheck.checked) data.append("preview", "on");

      for (var i = 0; i < positions.length; i += 1) {{
        var p = positions[i];
        data.append("element_" + p + "_name", document.getElementById("element_" + p + "_name").value);
        data.append("element_" + p + "_value", document.getElementById("element_" + p + "_value").value);
        data.append("element_" + p + "_color", document.getElementById("element_" + p + "_color").value);
        if (document.getElementById("element_" + p + "_is_bold").checked) {{
          data.append("element_" + p + "_is_bold", "on");
        }}
      }}
      return data;
    }}

    function buildProcessFormData() {{
      var data = buildConfigFormData();
      for (var i = 0; i < inputFiles.length; i += 1) {{
        data.append("files", inputFiles[i], inputFiles[i].name);
      }}
      return data;
    }}

    function syncVisibility() {{
      requestJson(
        "POST",
        "/api/visibility",
        buildConfigFormData(),
        function(payload) {{
          isApplyingConfig = true;
          setFormValuesFromConfig(payload.config);
          applyVisibility(payload.visibility);
          isApplyingConfig = false;
        }},
        function(message) {{
          summaryText.textContent = "可见性规则同步失败：" + message;
        }}
      );
    }}

    function scheduleVisibilitySync() {{
      if (isApplyingConfig) {{
        return;
      }}
      if (visibilitySyncTimer) {{
        clearTimeout(visibilitySyncTimer);
      }}
      visibilitySyncTimer = setTimeout(syncVisibility, 40);
    }}

    function renderJob(job) {{
      lastJob = job;
      currentJobId = job.job_id;
      resultsAvailable = job.results_available || [];
      if (!inputNames.length && job.input_files) {{
        inputNames = job.input_files.slice();
      }}
      if (selectedIndex < 0 && inputNames.length > 0) {{
        selectedIndex = 0;
      }}
      renderThumbList();

      var percent = job.progress.percent;
      progressBar.value = percent;
      statusText.textContent =
        "状态: " + job.status + " | 进度: " + job.progress.current + "/" + job.progress.total + " (" + percent + "%)\\n" + (job.message || "");
      summaryText.textContent = "当前任务: " + job.job_id;

      if (job.errors && job.errors.length > 0) {{
        var lines = [];
        for (var i = 0; i < job.errors.length; i += 1) {{
          lines.push("- " + job.errors[i].source + " => " + job.errors[i].error);
        }}
        errorList.textContent = lines.join("\\n");
      }} else {{
        errorList.textContent = "";
      }}

      if (job.status === "done" || job.status === "error" || job.status === "cancelled") {{
        setRunningState(false);
        if (pollTimer) {{
          clearInterval(pollTimer);
          pollTimer = null;
        }}
        if (job.status === "done") {{
          summaryText.textContent = "处理完成：输出 " + job.output_count + " 张，失败 " + job.error_count + " 张。";
          downloadLink.href = "/api/jobs/" + job.job_id + "/download";
          downloadLink.style.display = "block";
        }} else if (job.status === "cancelled") {{
          summaryText.textContent = "任务已取消。";
          downloadLink.style.display = "none";
        }} else {{
          summaryText.textContent = "任务失败：" + (job.message || "");
          downloadLink.style.display = "none";
        }}
      }}
      renderPreview();
    }}

    function pollJob(jobId) {{
      requestJson(
        "GET",
        "/api/jobs/" + jobId,
        null,
        function(payload) {{
          renderJob(payload.job);
        }},
        function(message) {{
          statusText.textContent = "查询任务状态失败：" + message;
        }}
      );
    }}

    function onSubmit(event) {{
      if (event && event.preventDefault) {{
        event.preventDefault();
      }}
      if (!inputFiles.length) {{
        statusText.textContent = "请先上传至少一张图片。";
        return false;
      }}
      setRunningState(true);
      downloadLink.style.display = "none";
      errorList.textContent = "";
      progressBar.value = 0;
      summaryText.textContent = "任务提交中...";

      requestJson(
        "POST",
        "/api/process",
        buildProcessFormData(),
        function(payload) {{
          currentJobId = payload.job_id;
          summaryText.textContent = "任务已创建：" + payload.job_id;
          pollJob(payload.job_id);
          pollTimer = setInterval(function() {{
            pollJob(payload.job_id);
          }}, 700);
        }},
        function(message) {{
          setRunningState(false);
          summaryText.textContent = "提交失败：" + message;
        }}
      );
      return false;
    }}

    function onCancelClick() {{
      if (!currentJobId) {{
        return;
      }}
      cancelBtn.disabled = true;
      requestJson(
        "POST",
        "/api/jobs/" + currentJobId + "/cancel",
        null,
        function(payload) {{
          cancelBtn.disabled = false;
          renderJob(payload.job);
        }},
        function(message) {{
          cancelBtn.disabled = false;
          summaryText.textContent = "取消失败：" + message;
        }}
      );
    }}

    function attachParamChangeEvents() {{
      var controls = form.querySelectorAll("input, select");
      for (var i = 0; i < controls.length; i += 1) {{
        var ctrl = controls[i];
        if (ctrl.id === "fileInput") {{
          continue;
        }}
        ctrl.addEventListener("change", scheduleVisibilitySync);
      }}
    }}

    var leftPanelDragDepth = 0;

    function clearLeftPanelDropActive() {{
      leftPanelDragDepth = 0;
      leftPanel.classList.remove("drop-active");
    }}

    function onLeftPanelDragEnter(event) {{
      event.preventDefault();
      if (submitBtn.disabled) {{
        return;
      }}
      leftPanelDragDepth += 1;
      leftPanel.classList.add("drop-active");
    }}

    function onLeftPanelDragOver(event) {{
      event.preventDefault();
      if (submitBtn.disabled) {{
        return;
      }}
      event.dataTransfer.dropEffect = "copy";
      leftPanel.classList.add("drop-active");
    }}

    function onLeftPanelDragLeave(event) {{
      if (!leftPanel.classList.contains("drop-active")) {{
        return;
      }}
      if (leftPanelDragDepth > 0) {{
        leftPanelDragDepth -= 1;
      }}
      if (leftPanelDragDepth > 0) {{
        return;
      }}

      var rect = leftPanel.getBoundingClientRect();
      var inside =
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom;
      if (!inside) {{
        clearLeftPanelDropActive();
      }}
    }}

    function onLeftPanelDrop(event) {{
      event.preventDefault();
      clearLeftPanelDropActive();
      if (!event.dataTransfer || !event.dataTransfer.files || event.dataTransfer.files.length <= 0) {{
        return;
      }}
      addFiles(event.dataTransfer.files, "拖拽");
    }}

    addFilesBtn.addEventListener("click", function() {{
      fileInput.click();
    }});
    fileInput.addEventListener("change", function() {{
      if (fileInput.files && fileInput.files.length > 0) {{
        addFiles(fileInput.files, "上传");
      }}
      fileInput.value = "";
    }});
    leftPanel.addEventListener("dragenter", onLeftPanelDragEnter);
    leftPanel.addEventListener("dragover", onLeftPanelDragOver);
    leftPanel.addEventListener("dragleave", onLeftPanelDragLeave);
    leftPanel.addEventListener("drop", onLeftPanelDrop);
    removeFileBtn.addEventListener("click", removeSelectedFile);
    clearFilesBtn.addEventListener("click", clearFiles);

    prevBtn.addEventListener("click", function() {{
      selectIndex(selectedIndex - 1);
    }});
    nextBtn.addEventListener("click", function() {{
      selectIndex(selectedIndex + 1);
    }});
    zoomSelect.addEventListener("change", function() {{
      applyZoomMode();
    }});

    attachParamChangeEvents();
    form.addEventListener("submit", onSubmit);
    cancelBtn.addEventListener("click", onCancelClick);
    setRunningState(false);
    renderThumbList();
    syncVisibility();
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

    def _parse_multipart_form(self) -> cgi.FieldStorage:
        content_length = _parse_int(self.headers.get("Content-Length"), 0)
        if content_length <= 0:
            raise ValueError("Empty request body")
        if content_length > MAX_REQUEST_BYTES:
            raise ValueError(f"Request exceeds maximum size ({MAX_REQUEST_BYTES} bytes)")

        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            raise TypeError("Content-Type must be multipart/form-data")

        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
            keep_blank_values=True,
        )

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

        if path == "/api/visibility":
            self._handle_visibility()
            return

        if path != "/api/process":
            self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "not_found", "Endpoint not found"))
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
            form = self._parse_multipart_form()
        except ValueError as exc:
            message = str(exc)
            if "maximum size" in message:
                self._send_json(
                    *_error_response(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "request_too_large",
                        message,
                    )
                )
            else:
                self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "bad_request", message))
            return
        except TypeError as exc:
            self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "bad_content_type", str(exc)))
            return
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
                result_paths=[None] * len(input_paths),
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

    def _handle_visibility(self) -> None:
        try:
            form = self._parse_multipart_form()
        except ValueError as exc:
            self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "bad_request", str(exc)))
            return
        except TypeError as exc:
            self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "bad_content_type", str(exc)))
            return
        except Exception as exc:
            self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "invalid_form", f"Cannot parse form data: {exc}"))
            return

        try:
            config_data = _build_config(form)
            payload = _build_visibility_payload(config_data)
            self._send_json(HTTPStatus.OK.value, _json_bytes(payload))
        except Exception as exc:
            self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "invalid_visibility_form", str(exc)))

    def _handle_get_job(self) -> None:
        # /api/jobs/<job_id>, /api/jobs/<job_id>/download, /api/jobs/<job_id>/results/<index>
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

        if len(parts) == 5 and parts[3] == "results":
            try:
                index = int(parts[4])
            except ValueError:
                self._send_json(*_error_response(HTTPStatus.BAD_REQUEST, "invalid_index", "Index must be integer"))
                return
            if index < 0 or index >= len(job.result_paths):
                self._send_json(*_error_response(HTTPStatus.NOT_FOUND, "result_not_found", "Result index out of range"))
                return
            result_path = job.result_paths[index]
            if not result_path or not result_path.exists():
                self._send_json(
                    *_error_response(
                        HTTPStatus.CONFLICT,
                        "result_not_ready",
                        "Result image is not available yet",
                    )
                )
                return
            payload = result_path.read_bytes()
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                _guess_image_content_type(result_path),
                {"Cache-Control": "no-store"},
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
    server, url, log_path = start_server_background(host, port)

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


def start_server_background(host: str, port: int) -> tuple[ThreadingHTTPServer, str, Path]:
    os.chdir(Path(__file__).resolve().parent)
    _ensure_cleanup_thread()
    log_path = setup_temp_logging(name_prefix="semi-utils-web", cleanup_on_exit=False)

    selected_port = _pick_available_port(host, port)
    if selected_port != port:
        print(f"Requested port {port} is occupied. Switched to {selected_port}.")

    server = ThreadingHTTPServer((host, selected_port), SemiWebHandler)
    real_port = int(server.server_address[1])
    url = f"http://{host}:{real_port}/"
    return server, url, log_path


def main() -> None:
    parser = argparse.ArgumentParser(description="semi-utils web GUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open browser")
    args = parser.parse_args()
    run_server(args.host, args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
