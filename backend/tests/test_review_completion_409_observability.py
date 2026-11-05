import unittest
from fastapi import HTTPException

import order_review_completion as completion


class ReviewCompletion409ObservabilityTests(unittest.TestCase):
    def test_nested_snapshot_diagnostic_survives_without_raw_source(self):
        exc = HTTPException(409, detail={"code": "unknown_source_change", "source_diagnostic": {
            "approved_source_hash": "a" * 64, "current_source_hash": "a" * 64,
            "differing_fields": ["/unknown-path-sha256/" + "b" * 64],
            "classification": "unknown", "customer": {"mobile": "private"},
            "raw": {"payment": "private"}}})
        result = completion._review_409_log_detail(exc, stage="finalize", user_id="owner", order_number="test", revision=0)
        self.assertEqual(result["source_diagnostic"]["approved_source_hash"], "a" * 64)
        self.assertEqual(len(result["source_diagnostic"]["differing_fields"]), 1)
        self.assertNotIn("private", str(result))
        self.assertNotIn("raw", result["source_diagnostic"])

    def test_allowlisted_fields_only(self):
        exc = HTTPException(409, detail={
            "code": "review_completion_source_changed",
            "operation_id": "review_example",
            "differing_fields": ["/source/shipping/company"],
            "customer": {"mobile": "redacted"},
            "extra": "redacted",
        })
        detail = completion._review_409_log_detail(
            exc, stage="claim", user_id="owner", order_number="292495318", revision=0
        )
        self.assertEqual(detail["code"], "review_completion_source_changed")
        self.assertEqual(detail["stage"], "claim")
        self.assertNotIn("customer", detail)
        self.assertNotIn("extra", detail)

    def test_non_409_returns_none(self):
        exc = HTTPException(502, detail={"code": "provider_error"})
        self.assertIsNone(completion._review_409_log_detail(
            exc, stage="sync_salla", user_id="owner", order_number="292495318", revision=0
        ))

    def test_stage_identity_revision(self):
        exc = HTTPException(409, detail={"code": "review_revision_conflict"})
        detail = completion._review_409_log_detail(
            exc, stage="finalize", user_id="owner", order_number="292486136", revision=3
        )
        self.assertEqual(detail["stage"], "finalize")
        self.assertEqual(detail["order_number"], "292486136")
        self.assertEqual(detail["revision"], 3)


if __name__ == "__main__":
    unittest.main()
