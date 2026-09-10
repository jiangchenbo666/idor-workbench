import unittest

from idor_workbench.domains.idor.page_mapping import infer_page_url_from_api_path, page_url_for_endpoint


class PageMappingTests(unittest.TestCase):
    def test_infers_frontend_route_from_versioned_api_path(self):
        self.assertEqual(infer_page_url_from_api_path("/api/v1/system/roles/list"), "/system/roles")
        self.assertEqual(infer_page_url_from_api_path("/api/v2/security/users/123"), "/security/users")

    def test_manual_page_url_wins(self):
        page_url, inferred = page_url_for_endpoint({"path": "/api/v1/system/roles", "page_url": "/platform/roles"})

        self.assertEqual(page_url, "/platform/roles")
        self.assertFalse(inferred)

    def test_non_api_path_is_still_usable_as_route(self):
        self.assertEqual(infer_page_url_from_api_path("/system/roles"), "/system/roles")


if __name__ == "__main__":
    unittest.main()
