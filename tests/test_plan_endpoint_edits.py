import unittest
from typing import Any

from idor_workbench.views.api import _make_plan


class PlanEndpointEditTests(unittest.TestCase):
    def test_allowed_roles_edit_changes_generated_cases(self):
        project: dict[str, Any] = {
            "project_name": "权限编辑",
            "base_url": "http://example.test",
            "goal": "测试管理员权限隔离",
            "roles": [{"name": "系统管理员"}, {"name": "安全管理员"}],
            "endpoints": [{"method": "GET", "path": "/api/v1/tasks", "allowed_roles": ["系统管理员"], "source_section": "任务管理"}],
        }

        first = _make_plan(project)
        first_cases = {row["actor"]: row for row in first["cases"] if row["method"] == "GET"}
        self.assertEqual(first_cases["系统管理员"]["category"], "allowed")
        self.assertEqual(first_cases["安全管理员"]["category"], "role_isolation")
        self.assertEqual(first_cases["系统管理员"]["source_section"], "任务管理")

        project["endpoints"][0]["allowed_roles"] = ["安全管理员"]
        changed = _make_plan(project)
        changed_cases = {row["actor"]: row for row in changed["cases"] if row["method"] == "GET"}
        self.assertEqual(changed_cases["系统管理员"]["category"], "role_isolation")
        self.assertEqual(changed_cases["安全管理员"]["category"], "allowed")


if __name__ == "__main__":
    unittest.main()
