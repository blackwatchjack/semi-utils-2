from __future__ import annotations

import web_gui_app


def test_load_runtime_limits_uses_defaults_when_env_missing():
    limits = web_gui_app._load_runtime_limits({})
    assert limits["max_files"] == web_gui_app.DEFAULT_MAX_FILES
    assert limits["max_request_bytes"] == web_gui_app.DEFAULT_MAX_REQUEST_BYTES
    assert limits["max_file_bytes"] == web_gui_app.DEFAULT_MAX_FILE_BYTES
    assert limits["job_ttl_seconds"] == web_gui_app.DEFAULT_JOB_TTL_SECONDS
    assert limits["max_concurrent_jobs"] == web_gui_app.DEFAULT_MAX_CONCURRENT_JOBS


def test_load_runtime_limits_accepts_valid_env_values():
    limits = web_gui_app._load_runtime_limits(
        {
            "SEMI_WEB_MAX_FILES": "123",
            "SEMI_WEB_MAX_REQUEST_BYTES": "456",
            "SEMI_WEB_MAX_FILE_BYTES": "78",
            "SEMI_WEB_JOB_TTL_SECONDS": "999",
            "SEMI_WEB_MAX_CONCURRENT_JOBS": "7",
        }
    )
    assert limits["max_files"] == 123
    assert limits["max_request_bytes"] == 456
    assert limits["max_file_bytes"] == 78
    assert limits["job_ttl_seconds"] == 999
    assert limits["max_concurrent_jobs"] == 7


def test_load_runtime_limits_clamps_and_falls_back():
    limits = web_gui_app._load_runtime_limits(
        {
            "SEMI_WEB_MAX_FILES": "0",
            "SEMI_WEB_MAX_REQUEST_BYTES": "invalid",
            "SEMI_WEB_MAX_FILE_BYTES": "-2",
            "SEMI_WEB_JOB_TTL_SECONDS": "0",
            "SEMI_WEB_MAX_CONCURRENT_JOBS": "999",
        }
    )
    assert limits["max_files"] == 1
    assert limits["max_request_bytes"] == web_gui_app.DEFAULT_MAX_REQUEST_BYTES
    assert limits["max_file_bytes"] == 1
    assert limits["job_ttl_seconds"] == 1
    assert limits["max_concurrent_jobs"] == 16
