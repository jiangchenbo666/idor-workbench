import unittest

from idor_workbench.domains.idor.recorder import (
    UNCAPTURED_SECTION,
    _clean_section_title,
    _merge_recorded_endpoints,
    _page_route,
    _request_key,
    _skip_recorded_request,
)


class RecorderRouteTests(unittest.TestCase):
    def test_hash_routes_are_kept_distinct(self):
        self.assertEqual(_page_route("https://example.test/#/system/users"), "/#/system/users")
        self.assertEqual(_page_route("https://example.test/#/system/roles"), "/#/system/roles")

    def test_request_key_normalizes_dynamic_ids(self):
        self.assertEqual(_request_key("get", "/api/users/123?tab=profile"), "GET /api/users/:id")

    def test_static_json_assets_are_not_recorded_as_api(self):
        self.assertTrue(_skip_recorded_request("https://example.test/static/i18n/zh-CN.json"))
        self.assertFalse(_skip_recorded_request("https://example.test/api/bootstrap"))

    def test_operation_labels_are_not_saved_as_navigation_section(self):
        self.assertEqual(_clean_section_title("刷新"), "")
        self.assertEqual(_clean_section_title(" 查询 "), "")
        self.assertEqual(_clean_section_title("成员管理"), "成员管理")

    def test_same_endpoint_keeps_all_navigation_sections(self):
        merged = _merge_recorded_endpoints([
            {"method": "GET", "path": "/api/v1/tasks", "source_section": "任务管理", "source_page": "/space/tasks"},
            {"method": "GET", "path": "/api/v1/tasks", "source_section": "数据统计", "source_page": "/space/overview"},
        ])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_sections"], ["任务管理", "数据统计"])

    def test_uncaptured_section_uses_one_backend_value(self):
        merged = _merge_recorded_endpoints([
            {"method": "GET", "path": "/api/v1/tasks", "source_page": "/space/tasks"},
        ])

        self.assertEqual(merged[0]["source_sections"], [UNCAPTURED_SECTION])


if __name__ == "__main__":
    unittest.main()
