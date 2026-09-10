import unittest

from idor_workbench.views.api import _apply_plan_modifications


class PlanModificationTests(unittest.TestCase):
    def test_frontend_actor_is_not_valid_for_api_additions(self):
        plan = {
            "cases": [
                {"category": "allowed", "actor": "系统管理员", "method": "GET", "path": "/api/v1/users"},
                {"category": "anonymous", "actor": "未登录用户", "method": "GET", "path": "/api/v1/users"},
                {"category": "frontend", "actor": "未授权角色组合", "method": "PAGE", "path": "/users"},
            ]
        }

        modified = _apply_plan_modifications(plan, [{
            "action": "add",
            "actor": "未授权角色组合",
            "method": "GET",
            "path": "/api/v1/users",
            "category": "role_isolation",
        }])

        self.assertEqual(len(modified["ai_modifications"]["applied"]), 0)
        self.assertEqual(len(modified["ai_modifications"]["rejected"]), 1)
        self.assertEqual(len(modified["cases"]), 3)


if __name__ == "__main__":
    unittest.main()
