"""Safe diagnostics for failed operational source refreshes."""
import unittest
from operational_source_diagnostics import source_validation_failure_detail


class SourceValidationFailureTests(unittest.TestCase):
    def test_known_salla_refresh_dependency(self):
        result = source_validation_failure_detail(ValueError("canonical_salla_refresh_required"))
        self.assertEqual(result["code"], "operational_source_setup_incomplete")
        self.assertEqual(result["cause"], "salla_snapshot_stale")
        self.assertIn("سلة", result["message"])

    def test_known_source_over_limit(self):
        result = source_validation_failure_detail(ValueError("operational_source_scope_too_large"))
        self.assertEqual(result["cause"], "source_capacity_exceeded")

    def test_known_payment_conflict(self):
        result = source_validation_failure_detail(ValueError("provider_payment_method_conflict"))
        self.assertEqual(result["cause"], "payment_method_conflict")

    def test_untrusted_exception_does_not_leak(self):
        secret = "bank-1234-order-2222"
        result = source_validation_failure_detail(ValueError(secret))
        self.assertEqual(result["cause"], "unknown")
        self.assertNotIn(secret, str(result))

    def test_multiple_args_and_subclasses_do_not_leak(self):
        class Strange(ValueError):
            pass
        self.assertEqual(source_validation_failure_detail(Strange("canonical_salla_refresh_required"))["cause"], "unknown")
        self.assertEqual(source_validation_failure_detail(ValueError("canonical_salla_refresh_required", "private"))["cause"], "unknown")


if __name__ == "__main__":
    unittest.main()
