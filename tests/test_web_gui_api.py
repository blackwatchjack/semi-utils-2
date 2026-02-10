from __future__ import annotations

import http.client
import io
import json
import threading
import time
import uuid
import zipfile
from pathlib import Path

from http.server import ThreadingHTTPServer

from web_gui_app import SemiWebHandler
from web_gui_app import reset_jobs_for_tests


def _multipart_body(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----semiutils-{uuid.uuid4().hex}"
    body = bytearray()

    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    for field_name, filename, content, content_type in files:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


class _ApiClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=20)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        headers_map = {k: v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, headers_map, data


class TestWebGuiApi:
    @classmethod
    def setup_class(cls):
        reset_jobs_for_tests()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SemiWebHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _ApiClient("127.0.0.1", cls.port)
        cls.sample_image = (Path(__file__).resolve().parents[1] / "images" / "1.jpeg").read_bytes()

    @classmethod
    def teardown_class(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        reset_jobs_for_tests()

    def setup_method(self):
        reset_jobs_for_tests()

    def _create_job(self, preview: bool = False):
        fields = {
            "layout": "watermark_right_logo",
            "quality": "90",
        }
        if preview:
            fields.update(
                {
                    "preview": "on",
                    "preview_max_size": "900",
                    "preview_quality": "70",
                }
            )

        body, boundary = _multipart_body(
            fields,
            [("files", "sample.jpeg", self.sample_image, "image/jpeg")],
        )
        status, _, data = self.client.request(
            "POST",
            "/api/process",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert status == 202
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is True
        return payload["job_id"]

    def _wait_done(self, job_id: str, timeout_sec: float = 25.0):
        deadline = time.time() + timeout_sec
        last_job = None
        while time.time() < deadline:
            status, _, data = self.client.request("GET", f"/api/jobs/{job_id}")
            assert status == 200
            payload = json.loads(data.decode("utf-8"))
            assert payload["ok"] is True
            last_job = payload["job"]
            if last_job["status"] in {"done", "error"}:
                return last_job
            time.sleep(0.2)
        raise AssertionError(f"Timeout waiting job {job_id}, last state: {last_job}")

    def test_health(self):
        status, _, data = self.client.request("GET", "/health")
        assert status == 200
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is True

    def test_normal_process_flow(self):
        job_id = self._create_job(preview=False)
        job = self._wait_done(job_id)
        assert job["status"] == "done"
        assert job["mode"] == "normal"
        assert job["output_count"] >= 1

        status, headers, data = self.client.request("GET", f"/api/jobs/{job_id}/download")
        assert status == 200
        assert headers.get("Content-Type", "").startswith("application/zip")

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            assert "report.json" in names
            assert any(name.startswith("output/") for name in names)
            report = json.loads(zf.read("report.json").decode("utf-8"))
            assert report["mode"] == "normal"
            assert report["error_count"] == 0

    def test_preview_process_flow(self):
        job_id = self._create_job(preview=True)
        job = self._wait_done(job_id)
        assert job["status"] == "done"
        assert job["mode"] == "preview"
        assert job["output_count"] >= 1

        status, _, data = self.client.request("GET", f"/api/jobs/{job_id}/download")
        assert status == 200
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            assert "report.json" in names
            assert any(name.startswith("preview/") for name in names)
            report = json.loads(zf.read("report.json").decode("utf-8"))
            assert report["mode"] == "preview"
            assert report["error_count"] == 0

    def test_reject_invalid_extension(self):
        body, boundary = _multipart_body(
            {},
            [("files", "bad.txt", b"not-image", "text/plain")],
        )
        status, _, data = self.client.request(
            "POST",
            "/api/process",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert status == 400
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "invalid_input"

    def test_reject_empty_upload(self):
        body, boundary = _multipart_body({"layout": "watermark_right_logo"}, [])
        status, _, data = self.client.request(
            "POST",
            "/api/process",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert status == 400
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "invalid_input"
