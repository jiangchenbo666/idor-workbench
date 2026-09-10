import unittest

from idor_workbench.views.api import _apply_plan_modifications


class AiPlanModificationTests(unittest.TestCase):
    def test_add_with_a_different_category_cannot_duplicate_an_existing_actor_endpoint_case(self):
        plan = {
            "cases": [{
                "actor": "安全管理员",
                "method": "GET",
                "path": "/api/v1/digitalStaff/simpleList?orgId=1",
                "category": "role_isolation",
            }],
        }
        modifications = [{
            "action": "add",
            "actor": "安全管理员",
            "method": "GET",
            "path": "/api/v1/digitalStaff/simpleList?orgId=1",
            "category": "coverage_gap",
            "reason": "AI duplicate proposal",
        }]
        result = _apply_plan_modifications(plan, modifications)
        self.assertEqual(len(result["cases"]), 1)
        self.assertEqual(len(result["ai_modifications"]["applied"]), 0)
        self.assertEqual(len(result["ai_modifications"]["rejected"]), 1)


if __name__ == "__main__":
    unittest.main()
