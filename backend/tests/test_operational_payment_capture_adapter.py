import unittest
from operational_payment_capture_adapter import CaptureEvidenceError, payment_events_from_normalized
from operational_payment_event_policy import decide, Decision


def source(captures, *, provider="tabby", currency="SAR", gross="450.00"):
    return dict(provider=provider, currency=currency, gross=gross, captures=captures)


def capture(ref="a", value="300.00", stamp="2026-11-24T12:30:00+03:00"):
    return dict(id=ref, amount=value, captured_at=stamp)


class CaptureAdapterTests(unittest.TestCase):
    def test_one_confirmed_payment(self):
        result = payment_events_from_normalized("order1", source([capture()]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].transaction_id, "a")
        self.assertEqual(decide(result[0]).action, Decision.POST)

    def test_two_installments_same_order(self):
        events = payment_events_from_normalized("order1",
            source([capture("a", "300.00"), capture("b", "150.00")]))
        self.assertEqual(len(events), 2)
        self.assertNotEqual(decide(events[0]).idempotency_key, decide(events[1]).idempotency_key)

    def test_diagnostics_disable_entire_batch(self):
        self.assertEqual(payment_events_from_normalized("order1", source([capture()]),
                         [{"code":"provider_payment_method_conflict"}]), [])

    def test_pending_observation_without_capture(self):
        self.assertEqual(payment_events_from_normalized("order1", source([])), [])

    def test_foreign_currency_requires_other_valuation_path(self):
        self.assertEqual(payment_events_from_normalized("order1",
                         source([capture()], currency="QAR")), [])

    def test_duplicate_reference_blocked(self):
        with self.assertRaises(CaptureEvidenceError):
            payment_events_from_normalized("order1",
                source([capture(), capture("a", "150.00")]))

    def test_overpaid_capture_blocked(self):
        with self.assertRaises(CaptureEvidenceError):
            payment_events_from_normalized("order1",
                source([capture("a", "301.00"), capture("b", "150.00")]))

    def test_no_missing_time_inference(self):
        with self.assertRaises(CaptureEvidenceError):
            payment_events_from_normalized("order1",
                source([capture(stamp=None)]))

    def test_invalid_order_identity(self):
        with self.assertRaises(CaptureEvidenceError):
            payment_events_from_normalized("", source([capture()]))

    def test_unknown_provider_cannot_be_posted(self):
        self.assertEqual(payment_events_from_normalized("order1",
                         source([capture()], provider="unknown")), [])


if __name__ == "__main__":
    unittest.main()
