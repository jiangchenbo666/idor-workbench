"""Uploads are bounded capabilities owned by exactly one user."""
from __future__ import annotations

import uuid
from io import BytesIO
from unittest import TestCase

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from idor_workbench.views.api import _store_upload_file, app


class UploadIsolationTests(TestCase):
    """Identical bytes must never create a cross-account shared file."""

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.owner = TestClient(app)
        self.other = TestClient(app)
        self.owner_name = f"upload-owner-{suffix}"
        self.other_name = f"upload-other-{suffix}"
        self._register(self.owner, self.owner_name)
        self._register(self.other, self.other_name)
        self.project_ids: list[tuple[TestClient, str]] = []

    def tearDown(self) -> None:
        for client, project_id in self.project_ids:
            client.delete(f"/api/projects/{project_id}")
        self.owner.close()
        self.other.close()

    def _register(self, client: TestClient, username: str) -> None:
        response = client.post(
            "/api/auth/register",
            json={"username": username, "password": "test-pass", "confirm_password": "test-pass"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _save_with_source(self, client: TestClient, name: str, source: dict[str, object]) -> str:
        response = client.post(
            "/api/projects",
            json={
                "project_name": name,
                "base_url": "https://test.invalid",
                "roles": [],
                "endpoints": [],
                "source_files": [source],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        project_id = str(response.json()["project_id"])
        self.project_ids.append((client, project_id))
        return project_id

    def test_same_content_gets_independent_source_and_storage(self) -> None:
        content = b"same product requirement for two isolated accounts"
        first = self.owner.post("/api/import/prd", files={"file": ("same.md", content, "text/markdown")})
        second = self.other.post("/api/import/prd", files={"file": ("same.md", content, "text/markdown")})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

        first_source = first.json()["source_file"]
        second_source = second.json()["source_file"]
        self.assertNotEqual(first_source["source_id"], second_source["source_id"])
        self.assertNotEqual(first_source["stored_path"], second_source["stored_path"])
        self.assertEqual(first_source["content_sha256"], second_source["content_sha256"])
        self.assertNotEqual(first_source["owner_user_id"], second_source["owner_user_id"])

        rejected = self.owner.post(
            "/api/projects",
            json={
                "project_name": f"stolen-source-{uuid.uuid4().hex[:8]}",
                "base_url": "https://test.invalid",
                "roles": [],
                "endpoints": [],
                "source_files": [second_source],
            },
        )
        self.assertEqual(rejected.status_code, 404, rejected.text)

        self._save_with_source(self.owner, f"owner-source-{uuid.uuid4().hex[:8]}", first_source)
        self._save_with_source(self.other, f"other-source-{uuid.uuid4().hex[:8]}", second_source)

    def test_oversized_upload_is_rejected_before_persistence(self) -> None:
        upload = UploadFile(filename="too-large.md", file=BytesIO(b"12345"))
        with self.assertRaises(HTTPException) as raised:
            _store_upload_file("prd", upload, f"test-user-{uuid.uuid4().hex[:8]}", max_bytes=4)
        self.assertEqual(raised.exception.status_code, 413)

    def test_separator_only_filename_uses_server_fallback(self) -> None:
        upload = UploadFile(filename="////", file=BytesIO(b"safe"))
        target, _source = _store_upload_file(
            "prd",
            upload,
            f"test-user-{uuid.uuid4().hex[:8]}",
            max_bytes=16,
        )
        try:
            self.assertTrue(target.name.endswith("-prd-upload"), target)
            self.assertEqual(target.read_bytes(), b"safe")
        finally:
            target.unlink(missing_ok=True)
