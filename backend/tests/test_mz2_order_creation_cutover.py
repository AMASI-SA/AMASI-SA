"""Order creation, never delivery/capture, owns the sale cutover boundary."""
from datetime import datetime, timezone
import unittest

from accounting_recognition_evidence import EvidenceError, qualify
from accounting_order_recognition import _salla_event

CUT = "2020-01-01T00:00:00Z"


def provider_evidence(created="2020-01-01T00:00:00Z"):
    order = {
        "id": "SYN-ORDER", "user_id": "owner", "order_number": "SYN-1",
        "payment_method": "tabby", "currency": "SAR", "total_amount": "115",
        "order_status": "completed", "delivered_at": "2020-01-02T12:00:00Z",
        "order_created_at": created,
    }
    payment = {
        "id": "SYN-PAY", "user_id": "owner", "order_reference_id": "SYN-1",
        "provider": "tabby", "provider_id": "SYN-CAPTURE", "currency": "SAR",
        "amount": "115", "captured_amount": "115", "status": "captured",
        "captured_at": "2020-01-02T10:00:00Z", "source": "synthetic-evidence",
    }
    return order, payment


def salla_evidence(created="2020-01-01 03:00:00"):
    return {
        "id": "SYN-EVIDENCE", "order_number": "SYN-1",
        "order_date_source_text": created,
        "delivery_source_text": "2020-01-02 15:00:00",
        "current_net_sar": "115", "refunded_sar": "0",
        "payment_reference": {"amount": "115", "reference": "SYN-CAPTURE"},
    }


class CreationFenceRegressionTests(unittest.TestCase):
    def test_provider_pre_cutover_creation_cannot_use_later_capture_or_delivery(self):
        order, payment = provider_evidence("2019-12-31T23:59:59Z")
        with self.assertRaisesRegex(EvidenceError, "^pre_cutover_order$"):
            qualify("owner", "tabby", order, payment, cutoff=CUT)

    def test_salla_pre_cutover_source_creation_cannot_use_later_delivery(self):
        with self.assertRaisesRegex(EvidenceError, "^pre_cutover_order$"):
            _salla_event("owner", salla_evidence("2020-01-01 02:59:59"), cutoff=CUT)

    def test_missing_and_invalid_creation_fail_with_stable_public_codes(self):
        from accounting_public_errors import public_accounting_error
        for value, code in (
            (None, "order_creation_timestamp_required"),
            ("", "order_creation_timestamp_required"),
            ("   ", "order_creation_timestamp_required"),
            ("not-a-date", "order_creation_timestamp_invalid"),
            ("2020-02-30T00:00:00Z", "order_creation_timestamp_invalid"),
            (True, "order_creation_timestamp_invalid"),
        ):
            for label, operation in (
                ("provider", lambda: qualify("owner", "tabby", *provider_evidence(value), cutoff=CUT)),
                ("salla", lambda: _salla_event("owner", salla_evidence(value), cutoff=CUT)),
            ):
                with self.subTest(value=value, path=label), self.assertRaises(EvidenceError) as error:
                    operation()
                self.assertEqual(public_accounting_error(error.exception), code)

    def test_boundary_and_post_cutover_keep_existing_recognition_dates(self):
        for value in (CUT, "2020-01-01T03:00:00+03:00", "2020-01-02T09:00:00Z"):
            with self.subTest(created=value):
                event = qualify("owner", "tabby", *provider_evidence(value), cutoff=CUT)
                self.assertEqual(event["recognized_at"], "2020-01-02T12:00:00+00:00")
        for value in ("2020-01-01 03:00", "2020-01-02 12:00", "2020-01-01T00:00:00Z"):
            with self.subTest(source=value):
                self.assertEqual(
                    _salla_event("owner", salla_evidence(value), cutoff=CUT)["recognized_at"],
                    "2020-01-02T12:00:00+00:00",
                )

    def test_all_provider_qualifiers_share_the_creation_fence(self):
        for provider in ("salla", "tamara", "tabby", "emkan"):
            order, payment = provider_evidence("2019-12-31T23:59:59Z")
            order["payment_method"] = payment["provider"] = provider
            with self.subTest(provider=provider), self.assertRaisesRegex(EvidenceError, "^pre_cutover_order$"):
                qualify("owner", provider, order, payment, cutoff=CUT)

    def test_creation_does_not_relax_capture_delivery_chronology(self):
        order, payment = provider_evidence("2020-01-03T00:00:00Z")
        with self.assertRaisesRegex(EvidenceError, "^event_date_conflict$"):
            qualify("owner", "tabby", order, payment, cutoff=CUT)


class CreationTimestampTests(unittest.TestCase):
    def test_explicit_timezone_required_except_documented_salla_source(self):
        from accounting_order_cutover import OrderCutoverError, require_order_created_on_or_after_cutover
        with self.assertRaisesRegex(OrderCutoverError, "^order_creation_timestamp_invalid$"):
            require_order_created_on_or_after_cutover("2020-01-01 03:00", CUT)
        self.assertEqual(
            require_order_created_on_or_after_cutover("2020-01-01 03:00", CUT, source_timezone=True),
            datetime(2020, 1, 1, tzinfo=timezone.utc),
        )

    def test_source_date_midnight_retains_riyadh_interpretation(self):
        from accounting_order_cutover import OrderCutoverError, require_order_created_on_or_after_cutover
        with self.assertRaisesRegex(OrderCutoverError, "^pre_cutover_order$"):
            require_order_created_on_or_after_cutover("2020-01-01", CUT, source_timezone=True)
        self.assertEqual(
            require_order_created_on_or_after_cutover(
                "2020-01-02", CUT, source_timezone=True),
            datetime(2020, 1, 1, 21, tzinfo=timezone.utc),
        )

    def test_bad_cutover_and_aware_datetime_inputs(self):
        from accounting_order_cutover import OrderCutoverError, require_order_created_on_or_after_cutover
        value = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(require_order_created_on_or_after_cutover(value, value), value)
        for cut in (None, "", "2020-01-01", "not-a-date"):
            with self.subTest(cut=cut), self.assertRaisesRegex(OrderCutoverError, "^recognition_cutoff_not_configured$"):
                require_order_created_on_or_after_cutover(value, cut)


if __name__ == "__main__":
    unittest.main()
