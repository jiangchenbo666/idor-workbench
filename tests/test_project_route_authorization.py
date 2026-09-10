"""Regression tests for the project-artifact authorization boundary."""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest import TestCase

from fastapi.testclient import TestClient

from idor_workbench.views.api import _project_dir, _write_json, app


class ProjectRouteAuthorizationTests(TestCase):
    """A project id is not an authorization token for another logged-in user."""

    def setUp(self) -> None:
        self.owner = TestClient(app)
        self.outsider = TestClient(app)
        self.anonymous = TestClient(app)
        suffix = uuid.uuid4().hex[:10]
        self._register(self.owner, f"owner-{suffix}")
        self._register(self.outsider, f"other-{suffix}")
        saved = self.owner.post(
            "/api/projects",
            json={
                "project_name": f"authorization-{suffix}",
                "base_url": "http://127.0.0.1:8001",
                "goal": "route authorization regression",
                "roles": [],
                "endpoints": [],
                "source_files": [],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.project_id = saved.json()["project_id"]

    def tearDown(self) -> None:
        self.owner.delete(f"/api/projects/{self.project_id}")
        self.owner.close()
        self.outsider.close()
        self.anonymous.close()

    def _register(self, client: TestClient, username: str) -> None:
        response = client.post(
            "/api/auth/register",
            json={"username": username, "password": "test-pass", "confirm_password": "test-pass"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_project_artifacts_and_tasks_require_owner(self) -> None:
        """Read, mutate and execute routes all reject an unrelated account."""
        self.assertEqual(self.anonymous.get(f"/api/projects/{self.project_id}").status_code, 401)
        self.assertEqual(self.anonymous.post("/api/ai/test", json={}).status_code, 401)

        requests = [
            lambda: self.outsider.get(f"/api/projects/{self.project_id}"),
            lambda: self.outsider.get(f"/api/projects/{self.project_id}/runs"),
            lambda: self.outsider.get(f"/api/projects/{self.project_id}/findings"),
            lambda: self.outsider.post(
                f"/api/projects/{self.project_id}/findings/F-001/transition",
                json={"action": "review", "decision": "confirmed", "note": "steal"},
            ),
            lambda: self.outsider.post(f"/api/projects/{self.project_id}/run", json={}),
            lambda: self.outsider.post(f"/api/projects/{self.project_id}/run-frontend"),
            lambda: self.outsider.post(f"/api/projects/{self.project_id}/manual-review", json={"items": []}),
            lambda: self.outsider.post(f"/api/projects/{self.project_id}/report", json={}),
            lambda: self.outsider.get(f"/api/projects/{self.project_id}/download/project.json"),
            lambda: self.outsider.get(f"/api/projects/{self.project_id}/screenshots/not-real.png"),
            lambda: self.outsider.get(f"/api/projects/{self.project_id}/manual-recording/status"),
            lambda: self.outsider.post(f"/api/projects/{self.project_id}/manual-recording/stop"),
            lambda: self.outsider.post(
                "/api/import-curl",
                json={"project_id": self.project_id, "text": "curl /api/test"},
            ),
            lambda: self.outsider.post(
                "/api/ai/test",
                json={"project_id": self.project_id},
            ),
        ]
        for request in requests:
            with self.subTest(route=request):
                self.assertEqual(request().status_code, 404)

        queued = self.owner.post(f"/api/projects/{self.project_id}/run", json={})
        self.assertEqual(queued.status_code, 200, queued.text)
        task_id = queued.json()["task_id"]
        self.assertEqual(self.outsider.get(f"/api/tasks/{task_id}").status_code, 404)

    def test_history_note_uses_patch_and_is_owner_scoped(self) -> None:
        """Keep the browser PATCH contract and prevent cross-project note edits."""
        run_id = f"test-{uuid.uuid4().hex[:8]}"
        run_dir = _project_dir(self.project_id) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(run_dir / "meta.json", {"run_id": run_id, "time": "2026-01-01 00:00:00"})
        _write_json(run_dir / "run_results.json", {"results": [], "total": 0})

        saved = self.owner.patch(f"/api/projects/{self.project_id}/runs/{run_id}", json={"note": "fixed by e2e"})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["meta"]["note"], "fixed by e2e")
        rejected = self.outsider.patch(
            f"/api/projects/{self.project_id}/runs/{run_id}",
            json={"note": "steal"},
        )
        self.assertEqual(rejected.status_code, 404)

        # The file assertion proves the route writes the specific run selected
        # by the user, rather than a transient front-end-only value.
        self.assertIn("fixed by e2e", Path(run_dir / "meta.json").read_text(encoding="utf-8"))

    def test_owner_can_transition_finding_and_outsider_cannot(self) -> None:
        """Vue 人工复核接口只能变更当前用户项目中的指定风险卡片。"""
        findings_path = _project_dir(self.project_id) / "findings.json"
        _write_json(
            findings_path,
            [{
                "finding_id": "F-001",
                "status": "needs_human_review",
                "actor_role": "normal_user",
                "endpoint": "GET /api/admin/users",
                "expected_result": "普通用户不应访问管理员列表",
                "actual_status_code": 200,
                "actual_response_summary": "返回用户列表",
                "assertion_reason": "期望拒绝访问，但请求成功",
                "human_label": None,
                "human_note": None,
                "fix_version": None,
                "retest_run_id": None,
                "retest_note": None,
                "close_note": None,
                "retest_history": [],
            }],
        )

        listed = self.owner.get(f"/api/projects/{self.project_id}/findings")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["items"][0]["finding_id"], "F-001")

        rejected = self.outsider.post(
            f"/api/projects/{self.project_id}/findings/F-001/transition",
            json={"action": "review", "decision": "confirmed", "note": "attempt"},
        )
        self.assertEqual(rejected.status_code, 404)

        reviewed = self.owner.post(
            f"/api/projects/{self.project_id}/findings/F-001/transition",
            json={"action": "review", "decision": "confirmed", "note": "已确认越权漏洞"},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["item"]["status"], "confirmed")

        waiting_fix = self.owner.post(
            f"/api/projects/{self.project_id}/findings/F-001/transition",
            json={"action": "waiting_fix", "fix_version": "1.0.2"},
        )
        self.assertEqual(waiting_fix.status_code, 200, waiting_fix.text)
        self.assertEqual(waiting_fix.json()["item"]["status"], "waiting_fix")

        retested = self.owner.post(
            f"/api/projects/{self.project_id}/findings/F-001/transition",
            json={"action": "retest", "retest_run_id": "R-001", "passed": True, "note": "修复后拒绝访问"},
        )
        self.assertEqual(retested.status_code, 200, retested.text)
        self.assertEqual(retested.json()["item"]["status"], "retest_passed")
