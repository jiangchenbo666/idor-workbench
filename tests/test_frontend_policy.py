import unittest

from idor_workbench.domains.idor.frontend_probe import _apply_goal_access_policy, _page_targets, _probe_assignments


class FrontendPolicyTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            "goal": "三个管理员可进入用户前台页面，具有普通员工所有功能；普通员工无权访问管理员权限，管理员权限间要隔离。",
            "roles": [
                {"name": "系统管理员"},
                {"name": "安全管理员"},
                {"name": "安全审计员"},
                {"name": "普通员工A"},
                {"name": "普通员工B"},
            ],
        }

    def test_admin_route_keeps_recorded_admin_owner_and_collapses_employees(self):
        target = {"page_url": "/space/members", "allowed_roles": ["安全管理员", "普通员工A"]}
        adjusted = _apply_goal_access_policy(self.project, [target])[0]
        self.assertEqual(adjusted["allowed_roles"], ["安全管理员"])
        self.assertEqual(
            _probe_assignments(self.project, adjusted),
            [
                ("系统管理员", ["系统管理员"]),
                ("安全管理员", ["安全管理员"]),
                ("安全审计员", ["安全审计员"]),
                ("普通员工A", ["普通员工A", "普通员工B"]),
            ],
        )

    def test_shared_product_route_uses_one_representative_check(self):
        target = {"page_url": "/agent", "allowed_roles": ["普通员工A"]}
        adjusted = _apply_goal_access_policy(self.project, [target])[0]
        self.assertEqual(len(adjusted["allowed_roles"]), 5)
        self.assertEqual(
            _probe_assignments(self.project, adjusted),
            [("普通员工A", ["系统管理员", "安全管理员", "安全审计员", "普通员工A", "普通员工B"])],
        )

    def test_role_scoped_page_route_is_a_frontend_target_without_an_api(self):
        project = {**self.project, "endpoints": [], "page_routes": [{"page_url": "/#/system/users", "allowed_roles": ["安全管理员"]}]}
        targets = _page_targets(project)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["page_url"], "/#/system/users")
        self.assertEqual(targets[0]["allowed_roles"], ["安全管理员"])


if __name__ == "__main__":
    unittest.main()
