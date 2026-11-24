"""First-capture canonical provider posting: no double counts or historical replay."""
import copy
import unittest
from operational_payment_direct_provider import (
    InitialCaptureHold, apply_first_provider_capture,
)

STAMP = "2026-11-24T21:02:00+03:00"
CAPTURE = "2026-11-24T21:01:00+03:00"


def state():
    return {
        "status": "active", "started_at": "2026-10-03T00:00:00+03:00",
        "openings": {"existing-balance": {"amount": "500.00"}},
        "movements": [],
        "engine": {
            "as_of": "2026-11-24T20:50:00+03:00",
            "facts": {}, "obligations": {}, "provider_reports": {},
            "order_versions": {},
            "provider_fee_inputs": {"policies": [{
                "provider": "tabby", "currency": "SAR", "status": "active",
                "event_type": "capture", "effective_from": "2026-01-01",
                "id": "tabby-fee-2026", "percentage": "6.99",
                "fixed_amount": "1.50", "vat_treatment": "exclusive",
                "vat_rate": "15"
            }]}
        },
    }


def normalized(*, ref="native1", amount="300.00", gross="300.00",
               captured_at=CAPTURE, provider="tabby"):
    return {"provider": provider, "currency": "SAR", "gross": gross,
            "order_created_at": "2026-11-24T20:55:00+03:00",
            "captures": [{"id": ref, "amount": amount, "captured_at": captured_at}],
            "refunds": []}


def post(snapshot, payment=None, order="order1", as_of=STAMP):
    return apply_first_provider_capture(snapshot, order_id=order,
                order_number="10001", normalized=payment or normalized(), as_of=as_of)


class DirectProviderTests(unittest.TestCase):
    def test_new_first_capture_posts_to_existing_canonical_structure(self):
        s = state()
        before = copy.deepcopy(s)
        result = post(s)
        self.assertEqual(result["status"], "posted")
        self.assertEqual(s["engine"]["facts"]["capture:native1"],
                         {"order_id": "order1", "provider": "tabby", "amount": "300.00"})
        claim = s["engine"]["obligations"]["provider:order1"]
        self.assertEqual(claim["confirmed"], "300.00")
        self.assertEqual(claim["party_id"], "tabby")
        self.assertEqual(claim["kind"], "provider")
        self.assertEqual(claim["currency"], "SAR")
        self.assertEqual(claim["business_date"], "2026-11-24")
        self.assertIn("provider:order1", s["engine"]["provider_reports"])
        self.assertEqual(before["movements"], s["movements"])
        self.assertEqual(before["openings"], s["openings"])

    def test_replay_does_not_count_second_time(self):
        s = state()
        post(s)
        previous = copy.deepcopy(s)
        self.assertEqual(post(s)["status"], "already_posted")
        self.assertEqual(s, previous)

    def test_exact_replay_still_matches_after_source_refresh_advances(self):
        s = state()
        post(s)
        s["engine"]["as_of"] = "2026-11-24T22:30:00+03:00"
        self.assertEqual(post(s, as_of="2026-11-24T22:31:00+03:00")["status"],
                         "already_posted")
        self.assertEqual(s["engine"]["obligations"]["provider:order1"]["confirmed"],
                         "300.00")

    def test_reused_payment_reference_on_different_order_is_blocked(self):
        s = state()
        post(s)
        with self.assertRaises(InitialCaptureHold):
            post(s, order="order2")
        self.assertNotIn("provider:order2", s["engine"]["obligations"])

    def test_historical_capture_prior_to_last_refresh_denied(self):
        s = state()
        previous = normalized(captured_at="2026-11-24T20:49:00+03:00")
        previous["order_created_at"] = "2026-11-24T20:40:00+03:00"
        with self.assertRaisesRegex(InitialCaptureHold, "historical"):
            post(s, previous)
        self.assertEqual(s["engine"]["facts"], {})

    def test_capture_prior_to_cutover_denied(self):
        s = state()
        with self.assertRaisesRegex(InitialCaptureHold, "outside_operational"):
            post(s, normalized(captured_at="2026-10-02T23:00:00+03:00"))

    def test_no_source_refresh_checkpoint_no_post(self):
        s = state()
        s["engine"].pop("as_of")
        with self.assertRaisesRegex(InitialCaptureHold, "prior_source_snapshot"):
            post(s)

    def test_refund_or_partial_payment_not_in_first_capture_path(self):
        s = state()
        with self.assertRaisesRegex(InitialCaptureHold, "partial"):
            post(s, normalized(amount="150.00"))
        with self.assertRaisesRegex(InitialCaptureHold, "refund"):
            post(s, {**normalized(), "refunds": [{"id": "refund1", "amount":"10.00"}]})

    def test_provider_claim_already_exists_requires_reconciliation(self):
        s = state()
        s["engine"]["obligations"]["provider:order1"] = {"confirmed":"300.00"}
        with self.assertRaisesRegex(InitialCaptureHold, "preexisting_order"):
            post(s)

    def test_missing_fee_policy_is_blocker_not_fake_net(self):
        s = state()
        s["engine"]["provider_fee_inputs"]["policies"] = []
        with self.assertRaisesRegex(InitialCaptureHold, "fee_policy_missing"):
            post(s)
        self.assertFalse(s["engine"]["facts"])

    def test_malformed_currency_or_provider_rejected(self):
        s = state()
        with self.assertRaisesRegex(InitialCaptureHold, "currency"):
            post(s, {**normalized(), "currency":"QAR"})
        with self.assertRaisesRegex(InitialCaptureHold, "currency"):
            post(s, normalized(provider="unknown"))

    def test_source_capture_before_original_order_is_not_recognized(self):
        s = state()
        payment = normalized()
        payment["order_created_at"] = "2026-11-24T21:05:00+03:00"
        with self.assertRaisesRegex(InitialCaptureHold,"outside_operational"):
            post(s,payment)
        self.assertEqual(s["engine"]["facts"],{})

    def test_pre_cutover_order_with_recent_capture_is_not_initial_order(self):
        s = state()
        payment = normalized()
        payment["order_created_at"] = "2026-10-02T23:40:00+03:00"
        with self.assertRaisesRegex(InitialCaptureHold,"outside_operational"):
            post(s,payment)
        self.assertFalse(s["engine"]["obligations"])

    def test_missing_original_order_timestamp_is_not_inferred_from_webhook(self):
        s = state()
        payment = normalized()
        payment.pop("order_created_at")
        with self.assertRaisesRegex(InitialCaptureHold,"capture_clock_invalid"):
            post(s,payment)
        self.assertEqual(s["engine"]["facts"],{})

    def test_future_capture_blocked(self):
        s = state()
        with self.assertRaisesRegex(InitialCaptureHold, "outside_operational"):
            post(s, normalized(captured_at="2026-11-24T22:00:00+03:00"))

    def test_already_scanned_order_rejected(self):
        s = state()
        s["engine"]["order_versions"]["order1"] = "2026-11-24T20:55:00+03:00"
        with self.assertRaisesRegex(InitialCaptureHold, "already_scanned"):
            post(s)

    def test_no_additional_bank_movement(self):
        s = state()
        s["movements"] = [{"id":"bank-1","amount":"400.00","direction":"incoming"}]
        before = copy.deepcopy(s["movements"])
        post(s)
        self.assertEqual(s["movements"], before)


if __name__ == "__main__":
    unittest.main()
