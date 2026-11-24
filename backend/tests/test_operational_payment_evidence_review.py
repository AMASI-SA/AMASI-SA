"""Unverified electronic payments remain review-only, never money."""
import copy
import unittest
from operational_payment_evidence_review import (
    build_review_record, queue_review_in_state, PENDING_KEY,
)


class PaymentReviewInboxTests(unittest.TestCase):
    def record(self, **kwargs):
        params = dict(owner="merchant-1", order_id="order-123",
                      source_ref="signed-event-hash", provider="tabby",
                      event_type="order.payment.updated",
                      reasons=["provider_capture_evidence_incomplete"])
        params.update(kwargs)
        return build_review_record(**params)

    def test_pending_does_not_touch_balances(self):
        state = {"status":"active","engine":{"facts":{}},"movements":[],
                 "openings":{"bank":123}}
        old = copy.deepcopy(state)
        result = queue_review_in_state(state, self.record(), "2026-11-24T20:00:00Z")
        self.assertEqual(result["status"], "pending_evidence")
        self.assertEqual(state["engine"], old["engine"])
        self.assertEqual(state["movements"], old["movements"])
        self.assertEqual(state["openings"], old["openings"])

    def test_replay_is_one_record(self):
        state = {"status":"active"}
        first = queue_review_in_state(state, self.record(), "2026-11-24T20:00:00Z")
        again = queue_review_in_state(state, self.record(), "2026-11-24T21:00:00Z")
        self.assertEqual(first["key"], again["key"])
        self.assertEqual(again["status"], "already_queued")
        self.assertEqual(len(state[PENDING_KEY]), 1)

    def test_repeated_review_retains_existing_decision(self):
        state = {"status":"active"}
        record = self.record()
        queue_review_in_state(state, record, "2026-11-24T20:00:00Z")
        state[PENDING_KEY][record["key"]]["status"] = "manually_closed"
        queue_review_in_state(state, record, "2026-11-24T21:00:00Z")
        self.assertEqual(state[PENDING_KEY][record["key"]]["status"], "manually_closed")

    def test_untrusted_reason_text_is_not_retained(self):
        data = self.record(reasons=["provider_payment_method_conflict",
                                    "BANK_ACCOUNT_123-secret"])
        self.assertEqual(data["reason_codes"], ["provider_payment_method_conflict"])
        self.assertNotIn("secret", str(data))

    def test_unrecognized_provider_fails_closed(self):
        with self.assertRaises(ValueError):
            self.record(provider="cash_on_delivery")

    def test_owner_scope_affects_stable_key(self):
        self.assertNotEqual(self.record()["key"],
                            self.record(owner="other-merchant")["key"])

    def test_inactive_state_fails_closed(self):
        with self.assertRaises(ValueError):
            queue_review_in_state({"status":"draft"}, self.record(), "now")


if __name__ == "__main__":
    unittest.main()
