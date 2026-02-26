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
from PIL import Image

import web_gui_app
from web_gui_app import SemiWebHandler
from web_gui_app import reset_jobs_for_tests
from web_gui_app import set_max_concurrent_jobs_for_tests


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
        set_max_concurrent_jobs_for_tests(2)

    def _create_job(self, preview: bool = False, extra_fields: dict[str, str] | None = None):
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
        if extra_fields:
            fields.update(extra_fields)

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
            if last_job["status"] in {"done", "error", "cancelled"}:
                return last_job
            time.sleep(0.2)
        raise AssertionError(f"Timeout waiting job {job_id}, last state: {last_job}")

    def test_health(self):
        status, _, data = self.client.request("GET", "/health")
        assert status == 200
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is True

    def test_homepage_includes_drag_drop_support(self):
        status, _, data = self.client.request("GET", "/")
        assert status == 200
        html = data.decode("utf-8")
        assert 'id="leftPanel"' in html
        assert ".left-panel.drop-active" in html
        assert "leftPanel.addEventListener(\"drop\", onLeftPanelDrop);" in html
        assert "event.dataTransfer.files" in html

    def test_normal_process_flow(self):
        job_id = self._create_job(preview=False)
        job = self._wait_done(job_id)
        assert job["status"] == "done"
        assert job["mode"] == "normal"
        assert job["output_count"] >= 1
        assert len(job["results_available"]) == job["progress"]["total"]
        assert all(job["results_available"])

        status, headers, data = self.client.request("GET", f"/api/jobs/{job_id}/download")
        assert status == 200
        assert headers.get("Content-Type", "").startswith("application/zip")

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            assert "report.json" not in names
            assert all("/" not in name for name in names)
            assert any(Path(name).suffix.lower() in {".jpg", ".jpeg", ".png"} for name in names)

    def test_preview_process_flow(self):
        job_id = self._create_job(preview=True)
        job = self._wait_done(job_id)
        assert job["status"] == "done"
        assert job["mode"] == "preview"
        assert job["output_count"] >= 1
        assert len(job["results_available"]) == job["progress"]["total"]
        assert all(job["results_available"])

        status, _, data = self.client.request("GET", f"/api/jobs/{job_id}/download")
        assert status == 200
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            assert "report.json" not in names
            assert all("/" not in name for name in names)
            assert any(Path(name).suffix.lower() in {".jpg", ".jpeg", ".png"} for name in names)

    def test_get_single_result_image(self):
        job_id = self._create_job(preview=False)
        job = self._wait_done(job_id)
        assert job["status"] == "done"
        assert job["results_available"][0] is True

        status, headers, data = self.client.request("GET", f"/api/jobs/{job_id}/results/0")
        assert status == 200
        assert headers.get("Content-Type", "").startswith("image/")

        with Image.open(io.BytesIO(data)) as image:
            assert image.width > 0
            assert image.height > 0

    def test_results_available_updates_during_polling(self, monkeypatch):
        def fake_process_images(inputs, output_dir=None, on_progress=None, **kwargs):
            total = len(inputs)
            for index, raw_source in enumerate(inputs, start=1):
                source_path = Path(raw_source)
                target_path = Path(output_dir) / source_path.name
                Image.new("RGB", (50, 30), color=(index * 20, 10, 10)).save(target_path, format="JPEG")
                if on_progress:
                    on_progress(index, total, source_path, None)
                time.sleep(0.25)
            return []

        monkeypatch.setattr(web_gui_app, "process_images", fake_process_images)

        body, boundary = _multipart_body(
            {"layout": "watermark_right_logo", "quality": "90"},
            [
                ("files", "sample1.jpeg", self.sample_image, "image/jpeg"),
                ("files", "sample2.jpeg", self.sample_image, "image/jpeg"),
            ],
        )
        status, _, data = self.client.request(
            "POST",
            "/api/process",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert status == 202
        job_id = json.loads(data.decode("utf-8"))["job_id"]

        partial_seen = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            status, _, data = self.client.request("GET", f"/api/jobs/{job_id}")
            assert status == 200
            job = json.loads(data.decode("utf-8"))["job"]
            available = job["results_available"]
            if available == [True, False]:
                partial_seen = True
                break
            if job["status"] in {"done", "error", "cancelled"}:
                break
            time.sleep(0.05)
        assert partial_seen is True

        final_job = self._wait_done(job_id)
        assert final_job["results_available"] == [True, True]

    def test_visibility_endpoint_resets_hidden_values(self):
        fields = {
            "layout": "simple",
            "background_color": "#abcdef",
            "logo_enable": "on",
            "font": "./fonts/custom-font.ttf",
            "alternative_font": "./fonts/Roboto-Light.ttf",
            "element_left_top_name": "Model",
            "element_left_top_value": "dirty",
        }
        body, boundary = _multipart_body(fields, [])
        status, _, data = self.client.request(
            "POST",
            "/api/visibility",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert status == 200
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["visibility"]["layout.logo_enable"] is False
        assert payload["visibility"]["base.alternative_font"] is True
        assert payload["config"]["layout"]["logo_enable"] is False
        assert payload["config"]["layout"]["background_color"] == web_gui_app.DEFAULTS["layout"]["background_color"]

    def test_extended_config_fields_are_forwarded(self, monkeypatch):
        captured: dict[str, dict] = {}

        def fake_process_images(inputs, config_data=None, on_progress=None, **kwargs):
            captured["config_data"] = config_data
            if on_progress:
                on_progress(1, 1, Path(inputs[0]), None)
            return []

        monkeypatch.setattr(web_gui_app, "process_images", fake_process_images)

        job_id = self._create_job(
            preview=False,
            extra_fields={
                "font_size": "3",
                "bold_font_size": "2",
                "font": "./fonts/Roboto-Regular.ttf",
                "bold_font": "./fonts/Roboto-Bold.ttf",
                "alternative_font": "./fonts/Roboto-Light.ttf",
                "alternative_bold_font": "./fonts/Roboto-Medium.ttf",
                "element_left_top_name": "Custom",
                "element_left_top_value": "Semi Utils",
                "element_left_top_color": "#123456",
                "element_left_top_is_bold": "on",
                "element_left_bottom_color": "#654321",
                "element_right_bottom_is_bold": "on",
            },
        )
        job = self._wait_done(job_id)
        assert job["status"] == "done"

        config_data = captured["config_data"]
        assert config_data["base"]["font_size"] == 3
        assert config_data["base"]["bold_font_size"] == 2
        assert config_data["base"]["font"] == "./fonts/Roboto-Regular.ttf"
        assert config_data["base"]["bold_font"] == "./fonts/Roboto-Bold.ttf"
        assert config_data["base"]["alternative_font"] == web_gui_app.DEFAULTS["base"]["alternative_font"]
        assert config_data["base"]["alternative_bold_font"] == web_gui_app.DEFAULTS["base"]["alternative_bold_font"]
        assert config_data["layout"]["elements"]["left_top"]["name"] == "Custom"
        assert config_data["layout"]["elements"]["left_top"]["value"] == "Semi Utils"
        assert config_data["layout"]["elements"]["left_top"]["color"] == web_gui_app.DEFAULTS["layout"]["elements"]["left_top"]["color"]
        assert config_data["layout"]["elements"]["left_top"]["is_bold"] == web_gui_app.DEFAULTS["layout"]["elements"]["left_top"]["is_bold"]
        assert (
            config_data["layout"]["elements"]["left_bottom"]["color"]
            == web_gui_app.DEFAULTS["layout"]["elements"]["left_bottom"]["color"]
        )
        assert (
            config_data["layout"]["elements"]["right_bottom"]["is_bold"]
            == web_gui_app.DEFAULTS["layout"]["elements"]["right_bottom"]["is_bold"]
        )

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

    def test_cancel_running_job(self, monkeypatch):
        started = threading.Event()

        def fake_process_images(inputs, on_progress=None, **kwargs):
            started.set()
            time.sleep(0.6)
            if on_progress:
                on_progress(1, 1, Path(inputs[0]), None)
            return []

        monkeypatch.setattr(web_gui_app, "process_images", fake_process_images)

        job_id = self._create_job(preview=False)
        assert started.wait(timeout=2.0)

        status, _, data = self.client.request("POST", f"/api/jobs/{job_id}/cancel")
        assert status == 202
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["job"]["status"] in {"cancelling", "cancelled"}

        job = self._wait_done(job_id, timeout_sec=5.0)
        assert job["status"] == "cancelled"
        assert job["cancel_requested"] is True

    def test_concurrency_limit_puts_job_into_waiting(self, monkeypatch):
        set_max_concurrent_jobs_for_tests(1)
        release = threading.Event()
        started_count = 0
        started_lock = threading.Lock()

        def fake_process_images(inputs, on_progress=None, **kwargs):
            nonlocal started_count
            with started_lock:
                started_count += 1
            release.wait(timeout=5.0)
            if on_progress:
                on_progress(1, 1, Path(inputs[0]), None)
            return []

        monkeypatch.setattr(web_gui_app, "process_images", fake_process_images)

        first_job_id = self._create_job(preview=False)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            status, _, data = self.client.request("GET", f"/api/jobs/{first_job_id}")
            assert status == 200
            payload = json.loads(data.decode("utf-8"))
            if payload["job"]["status"] == "running":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("First job did not enter running state")

        second_job_id = self._create_job(preview=False)

        waiting_seen = False
        deadline = time.time() + 3.0
        while time.time() < deadline:
            status, _, data = self.client.request("GET", f"/api/jobs/{second_job_id}")
            assert status == 200
            payload = json.loads(data.decode("utf-8"))
            if payload["job"]["status"] == "waiting":
                waiting_seen = True
                break
            time.sleep(0.05)
        assert waiting_seen is True
        assert started_count == 1

        release.set()
        first_job = self._wait_done(first_job_id, timeout_sec=5.0)
        second_job = self._wait_done(second_job_id, timeout_sec=5.0)
        assert first_job["status"] == "done"
        assert second_job["status"] == "done"

    def test_download_before_done_returns_result_not_ready(self, monkeypatch):
        started = threading.Event()
        release = threading.Event()

        def fake_process_images(inputs, on_progress=None, **kwargs):
            started.set()
            release.wait(timeout=5.0)
            if on_progress:
                on_progress(1, 1, Path(inputs[0]), None)
            return []

        monkeypatch.setattr(web_gui_app, "process_images", fake_process_images)

        job_id = self._create_job(preview=False)
        assert started.wait(timeout=2.0)

        status, _, data = self.client.request("GET", f"/api/jobs/{job_id}/download")
        assert status == 409
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "result_not_ready"

        release.set()
        job = self._wait_done(job_id, timeout_sec=5.0)
        assert job["status"] == "done"

    def test_expired_done_job_is_cleaned_up(self, monkeypatch):
        job_id = self._create_job(preview=False)
        job = self._wait_done(job_id)
        assert job["status"] == "done"

        monkeypatch.setattr(web_gui_app, "JOB_TTL_SECONDS", 0)
        web_gui_app._cleanup_expired_jobs()

        status, _, data = self.client.request("GET", f"/api/jobs/{job_id}")
        assert status == 404
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "job_not_found"

    def test_cancel_waiting_job_keeps_consistent_result_state(self, monkeypatch):
        set_max_concurrent_jobs_for_tests(1)
        release = threading.Event()
        started_count = 0
        started_lock = threading.Lock()

        def fake_process_images(inputs, on_progress=None, **kwargs):
            nonlocal started_count
            with started_lock:
                started_count += 1
            release.wait(timeout=5.0)
            if on_progress:
                on_progress(1, 1, Path(inputs[0]), None)
            return []

        monkeypatch.setattr(web_gui_app, "process_images", fake_process_images)

        first_job_id = self._create_job(preview=False)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            status, _, data = self.client.request("GET", f"/api/jobs/{first_job_id}")
            assert status == 200
            payload = json.loads(data.decode("utf-8"))
            if payload["job"]["status"] == "running":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("First job did not enter running state")

        second_job_id = self._create_job(preview=False)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            status, _, data = self.client.request("GET", f"/api/jobs/{second_job_id}")
            assert status == 200
            payload = json.loads(data.decode("utf-8"))
            if payload["job"]["status"] == "waiting":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Second job did not enter waiting state")

        status, _, data = self.client.request("POST", f"/api/jobs/{second_job_id}/cancel")
        assert status == 202
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["job"]["status"] == "cancelled"

        waiting_job = self._wait_done(second_job_id, timeout_sec=5.0)
        assert waiting_job["status"] == "cancelled"
        assert waiting_job["cancel_requested"] is True
        assert started_count == 1

        status, _, data = self.client.request("GET", f"/api/jobs/{second_job_id}/download")
        assert status == 409
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "result_not_ready"

        release.set()
        first_job = self._wait_done(first_job_id, timeout_sec=5.0)
        assert first_job["status"] == "done"

    def test_cancel_done_job_returns_conflict(self):
        job_id = self._create_job(preview=False)
        job = self._wait_done(job_id)
        assert job["status"] == "done"

        status, _, data = self.client.request("POST", f"/api/jobs/{job_id}/cancel")
        assert status == 409
        payload = json.loads(data.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "cannot_cancel"
