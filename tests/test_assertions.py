import json
import unittest

from idor_workbench.domains.idor.assertions import apply_ai_tuning, classify_response, new_profile, update_profile


class AssertionEngineTests(unittest.TestCase):
    def test_blocked_response_is_learned_and_passes(self):
        profile = update_profile(new_profile(), {"status_code": 200, "body": json.dumps({"success": True, "data": {"id": "42"}})}, False)
        profile = update_profile(profile, {"status_code": 403, "body": json.dumps({"success": False, "message": "Forbidden"})}, True)

        verdict = classify_response({"status_code": 403, "body": json.dumps({"success": False, "message": "Forbidden"})}, True, profile)

        self.assertTrue(verdict["passed"])
        self.assertIn("HTTP=403", verdict["evidence"])

    def test_successful_business_data_forbidden_to_other_role_fails(self):
        profile = update_profile(new_profile(), {"status_code": 200, "body": json.dumps({"success": True, "data": {"id": "42"}})}, False)

        verdict = classify_response({"status_code": 200, "body": json.dumps({"success": True, "data": {"id": "99"}})}, True, profile)

        self.assertFalse(verdict["passed"])

    def test_ai_tuning_rejects_unsafe_field_path(self):
        tuned = apply_ai_tuning(new_profile(), {"preferred_success_field": "success", "preferred_code_field": "bad-field"})

        self.assertEqual(tuned["success_field"], "success")
        self.assertIsNone(tuned["code_field"])


if __name__ == "__main__":
    unittest.main()
