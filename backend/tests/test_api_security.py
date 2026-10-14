import json
import tempfile
import time
import unittest
from concurrent.futures import Future
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import main


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_patch = patch.object(main, "DATA", self.root)
        self.data_patch.start()
        self.jobs_patch = patch.dict(main.JOBS, {}, clear=True)
        self.jobs_patch.start()
        self.access_patch = patch.object(main, "_fetch_apexive_product_access", side_effect=self.authorize)
        self.access_mock = self.access_patch.start()
        main._save_job("tenant-job", "tenant-a", {"_tenant_id": "tenant-a", "status": "queued", "progress": 0})
        self.client = TestClient(main.app)

    def tearDown(self):
        self.jobs_patch.stop()
        self.access_patch.stop()
        self.data_patch.stop()
        self.temp_dir.cleanup()

    def authorize(self, token):
        try:
            claims = jwt.decode(token, "test-only-secret-change-to-32-bytes", algorithms=["HS256"])
        except jwt.PyJWTError:
            return 401, b"{}"
        return 200, json.dumps({"has_access": True, "tenant_id": claims["tenant_id"]}).encode("utf8")

    def token(self, tenant):
        return jwt.encode({"tenant_id": tenant, "exp": int(time.time()) + 60}, "test-only-secret-change-to-32-bytes", algorithm="HS256")

    def test_api_requires_bearer_auth_when_verification_key_is_configured(self):
        response = self.client.get("/api/jobs/tenant-job")

        self.assertEqual(response.status_code, 401)

    def test_revoked_product_access_is_denied(self):
        self.access_mock.side_effect = None
        self.access_mock.return_value = (200, json.dumps({"has_access": False, "tenant_id": "tenant-a"}).encode("utf8"))
        response = self.client.get("/api/jobs/tenant-job", headers={"Authorization": f"Bearer {self.token('tenant-a')}"})
        self.assertEqual(response.status_code, 403)

    def test_authorization_service_outage_fails_closed(self):
        self.access_mock.side_effect = None
        self.access_mock.return_value = (503, b"{}")
        response = self.client.get("/api/jobs/tenant-job", headers={"Authorization": f"Bearer {self.token('tenant-a')}"})
        self.assertEqual(response.status_code, 503)

    def test_jobs_are_isolated_by_tenant(self):
        other_tenant = self.client.get(
            "/api/jobs/tenant-job",
            headers={"Authorization": f"Bearer {self.token('tenant-b')}"},
        )
        owner = self.client.get(
            "/api/jobs/tenant-job",
            headers={"Authorization": f"Bearer {self.token('tenant-a')}"},
        )

        self.assertEqual(other_tenant.status_code, 404)
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["status"], "queued")

    def test_invalid_pdf_upload_is_rejected_without_creating_a_job(self):
        response = self.client.post(
            "/api/analyze",
            headers={"Authorization": f"Bearer {self.token('tenant-a')}"},
            files={
                "file_a": ("not-a-pdf.pdf", b"not a pdf", "application/pdf"),
                "file_b": ("also-not-a-pdf.pdf", b"not a pdf", "application/pdf"),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("signature", response.json()["detail"].lower())
        self.assertFalse(any(path.is_dir() for path in self.root.iterdir()))

    def test_oversized_upload_is_rejected_and_cleaned_up(self):
        with patch.object(main, "MAX_FILE_BYTES", 4):
            response = self.client.post(
                "/api/analyze",
                headers={"Authorization": f"Bearer {self.token('tenant-a')}"},
                files={
                    "file_a": ("logo.png", b"oversized", "image/png"),
                    "file_b": ("logo-b.png", b"tiny", "image/png"),
                },
            )

        self.assertEqual(response.status_code, 413)
        self.assertFalse(any(path.is_dir() for path in self.root.iterdir()))

    def test_standalone_logo_job_completes_and_downloads_are_tenant_scoped(self):
        image = Image.new("RGB", (40, 30), "white")
        ImageDraw.Draw(image).ellipse((4, 4, 34, 26), fill="black")
        stream = BytesIO()
        image.save(stream, format="PNG")
        image_bytes = stream.getvalue()
        headers = {"Authorization": f"Bearer {self.token('tenant-a')}"}

        response = self.client.post(
            "/api/analyze",
            headers=headers,
            files={
                "file_a": ("brand-a.png", image_bytes, "image/png"),
                "file_b": ("brand-b.png", image_bytes, "image/png"),
            },
        )

        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 15
        state = {}
        while time.monotonic() < deadline:
            status_response = self.client.get(f"/api/jobs/{job_id}", headers=headers)
            state = status_response.json()
            if state["status"] in {"completed", "error"}:
                break
            time.sleep(0.05)

        self.assertEqual(state["status"], "completed", state.get("message"))
        self.assertEqual(state["source_a"]["file"], "brand-a.png")
        with main._job_lock:
            main.JOBS.pop(job_id, None)
        reloaded = self.client.get(f"/api/jobs/{job_id}", headers=headers)
        assets = self.client.get(f"/api/jobs/{job_id}/assets/A", headers=headers)
        report = self.client.get(
            f"/api/jobs/{job_id}/files/matches.csv",
            headers={"Authorization": f"Bearer {self.token('tenant-b')}"},
        )

        self.assertEqual(reloaded.status_code, 200)
        self.assertEqual(reloaded.json()["status"], "completed")
        self.assertEqual(assets.status_code, 200)
        self.assertEqual(assets.json()["items"][0]["source_reference"], "Standalone logo")
        self.assertEqual(report.status_code, 404)

    def test_failed_jobs_can_be_retried_and_queued_jobs_cancelled(self):
        main._save_job(
            "retry-job",
            "tenant-a",
            {
                "_tenant_id": "tenant-a",
                "status": "error",
                "progress": 100,
                "message": "previous failure",
                "attempts": 1,
                "source_a_path": "A/a.png",
                "source_b_path": "B/b.png",
            },
        )
        queued_future = Future()
        headers = {"Authorization": f"Bearer {self.token('tenant-a')}"}

        with patch.object(main._executor, "submit", return_value=queued_future):
            retry = self.client.post("/api/jobs/retry-job/retry", headers=headers)
            cancelled = self.client.delete("/api/jobs/retry-job", headers=headers)

        self.assertEqual(retry.status_code, 202)
        self.assertEqual(retry.json()["attempts"], 2)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertTrue(queued_future.cancelled())


if __name__ == "__main__":
    unittest.main()
