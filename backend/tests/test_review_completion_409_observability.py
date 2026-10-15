import unittest
from fastapi import HTTPException

import order_review_completion as completion


class ReviewCompletion409ObservabilityTests(unittest.TestCase):
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
