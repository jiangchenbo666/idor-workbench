import unittest

from idor_workbench.domains.idor.scope import apply_goal_scope_to_project, role_scope_from_goal


def sample_project(goal="只进行系统管理员和安全管理员的交叉越权测试"):
    return {
        "project_name": "三权测试",
        "base_url": "http://example.test",
        "goal": goal,
        "auth": {"token_url": "/token", "token_path": "token"},
        "roles": [
            {"name": "系统管理员", "username": "sys", "password": "pw"},
            {"name": "安全管理员", "username": "safe", "password": "pw"},
            {"name": "安全审计员", "username": "audit", "password": "pw"},
            {"name": "普通用户", "username": "user", "password": "pw"},
        ],
        "endpoints": [
            {"method": "GET", "path": "/system", "allowed_roles": ["系统管理员"]},
            {"method": "GET", "path": "/security", "allowed_roles": ["安全管理员"]},
            {"method": "GET", "path": "/audit", "allowed_roles": ["安全审计员"]},
            {"method": "GET", "path": "/profile", "allowed_roles": ["普通用户"]},
        ],
    }


class GoalScopeTests(unittest.TestCase):
    def test_goal_limits_project_to_explicit_cross_roles(self):
        scoped = apply_goal_scope_to_project(sample_project())

        self.assertEqual([role["name"] for role in scoped["roles"]], ["系统管理员", "安全管理员"])
        self.assertEqual([endpoint["path"] for endpoint in scoped["endpoints"]], ["/system", "/security"])
        self.assertEqual(scoped["plan_scope"]["excluded_roles"], ["安全审计员", "普通用户"])

    def test_unscoped_goal_keeps_all_roles_and_endpoints(self):
        project = sample_project("根据用例检验前后端是否存在越权情况")
        scoped = apply_goal_scope_to_project(project)

        self.assertFalse(scoped["plan_scope"]["scoped"])
        self.assertEqual([role["name"] for role in scoped["roles"]], ["系统管理员", "安全管理员", "安全审计员", "普通用户"])
        self.assertEqual(len(scoped["endpoints"]), 4)

    def test_exclusion_phrase_removes_role(self):
        project = sample_project("测试三权越权，但排除安全审计员")
        scope = role_scope_from_goal(project)

        self.assertTrue(scope["scoped"])
        self.assertNotIn("安全审计员", scope["roles"])


if __name__ == "__main__":
    unittest.main()
