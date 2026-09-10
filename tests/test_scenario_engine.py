import json
import tempfile
import unittest
from pathlib import Path

from idor_workbench.domains.idor.scenarios import execute_scenarios


class FakeEngine:
    def __init__(self):
        self.calls = []

    def get_token(self, username, password, login_type="WEB"):
        return f"token-{username}-{login_type}"

    def send_request(self, token, endpoint):
        self.calls.append({"token": token, "endpoint": endpoint})
        body = {"success": True, "data": {"id": "task-123", "records": [{"id": "row-1"}]}}
        return {
            "status_code": 200,
            "body": json.dumps(body),
            "error": None,
            "request": {
                "method": endpoint.get("method", "GET"),
                "path": endpoint.get("path", ""),
                "params": endpoint.get("params", {}),
                "body": endpoint.get("body"),
                "headers": endpoint.get("headers", {}),
            },
        }


def run_project(project):
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp)
        result = execute_scenarios(project, project_dir, FakeEngine())
        saved = json.loads((project_dir / "scenario_results.json").read_text(encoding="utf-8"))
        return result, saved


class ScenarioEngineTests(unittest.TestCase):
    def base_project(self, steps, allow_write=False):
        return {
            "roles": [{"name": "admin", "username": "admin", "password": "secret", "login_type": "WEB"}],
            "scenarios": [{
                "name": "task flow",
                "actor_role": "admin",
                "enabled": True,
                "allow_write": allow_write,
                "steps": steps,
            }],
        }

    def test_extracts_jsonpath_and_replaces_variables(self):
        steps = [
            {"name": "read id", "method": "GET", "path": "/api/tasks/latest", "role": "admin", "extract": [{"name": "task_id", "path": "$.data.id"}]},
            {"name": "use id", "method": "GET", "path": "/api/tasks/{{task_id}}", "role": "admin", "params": {"id": "{{task_id}}"}, "headers": {"X-Task": "{{task_id}}"}},
        ]
        result, saved = run_project(self.base_project(steps))

        self.assertEqual(result["variables"]["task_id"], "task-123")
        second = saved["scenarios"][0]["steps"][1]
        self.assertEqual(second["path"], "/api/tasks/task-123")
        self.assertEqual(second["request"]["params"]["id"], "task-123")
        self.assertEqual(second["request"]["headers"]["X-Task"], "task-123")

    def test_write_operations_are_skipped_by_default(self):
        result, _ = run_project(self.base_project([
            {"name": "create", "method": "POST", "path": "/api/tasks", "role": "admin", "body": {"name": "x"}},
        ]))

        step = result["scenarios"][0]["steps"][0]
        self.assertEqual(step["status"], "SKIPPED")
        self.assertIn("Write operation blocked", step["reason"])
        self.assertEqual(result["summary"]["skipped"], 1)

    def test_delete_requires_step_confirmation_even_when_writes_allowed(self):
        result, _ = run_project(self.base_project([
            {"name": "delete", "method": "DELETE", "path": "/api/tasks/task-123", "role": "admin"},
        ], allow_write=True))

        step = result["scenarios"][0]["steps"][0]
        self.assertEqual(step["status"], "SKIPPED")
        self.assertIn("DELETE requires", step["reason"])

    def test_confirmed_delete_can_execute_in_write_enabled_scenario(self):
        result, _ = run_project(self.base_project([
            {"name": "delete", "method": "DELETE", "path": "/api/tasks/task-123", "role": "admin", "confirm_delete": True},
        ], allow_write=True))

        step = result["scenarios"][0]["steps"][0]
        self.assertEqual(step["status"], "PASS")
        self.assertEqual(step["http"], 200)

    def test_empty_scenarios_are_still_persisted_for_downloads(self):
        result, saved = run_project({"roles": [], "scenarios": []})

        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(saved["summary"]["total"], 0)
        self.assertEqual(saved["scenarios"], [])


if __name__ == "__main__":
    unittest.main()
