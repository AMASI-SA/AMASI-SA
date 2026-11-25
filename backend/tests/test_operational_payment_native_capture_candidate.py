"""Recover source-captured payment date only from a native bound transaction."""
import copy
import sys
import types
import unittest
from unittest.mock import patch

from operational_payment_native_capture_candidate import recovered_dated_native_capture
from test_operational_payment_direct_order_page import paid_source, source


NOW="2026-11-24T03:00:00+03:00"


def row(**kw):
    event = {
        "schema":"salla_transaction_evidence_v1",
        "provider_order_id":"580626783",
        "provider":"tamara",
        "transaction_id":"salla-txn-1",
        "reference":"t-transaction-01",
        "status":"paid",
        "paid_at":"2026-11-24T02:51:00+03:00",
        "currency":"SAR",
        "amount":"173.99",
    }
    event.update(kw)
    return {
        "user_id":"merchant1", "source":"salla_transactions",
        "order_number":"301556476", "provider_order_id":"580626783",
        "transaction_id":"salla-txn-1",
        "native_transaction_evidence":[event],
    }


def preview(rows=None,raw=None, owner="merchant1"):
    stub=types.ModuleType("operational_demand_refresh")
    stub.calculation_checkpoint=lambda:None
    with patch.dict(sys.modules, {"operational_demand_refresh":stub}):
        return recovered_dated_native_capture(
            source=source(),raw=raw or paid_source(),
            rows=rows if rows is not None else [row()],
            owner=owner,order_number="301556476",as_of=NOW)


class NativeTransactionRecoveryTests(unittest.TestCase):
    def test_full_dated_native_transaction_can_be_prepared_without_money(self):
        original=paid_source()
        before=copy.deepcopy(original)
        candidate=preview(raw=original)
        self.assertEqual(candidate["status"],"verified_native_capture")
        self.assertEqual(candidate["provider"],"tamara")
        self.assertEqual(candidate["original_captured_at"],"2026-11-23T23:51:00+00:00")
        self.assertEqual(candidate["amount"],"173.99")
        self.assertFalse(candidate["posting_permitted"])
        self.assertEqual(original,before)

    def test_transaction_without_original_capture_date_is_review_only(self):
        self.assertEqual(preview([row(paid_at=None)])["status"],"not_verified")

    def test_broken_order_binding_cannot_supply_capture_date(self):
        mismatch=row(provider_order_id="another-order")
        self.assertEqual(preview([mismatch])["status"],"not_verified")

    def test_transaction_reference_different_from_current_paid_page_refused(self):
        self.assertEqual(preview([row(reference="different-txn")])["status"],
                         "not_verified")

    def test_paid_amount_must_match_full_native_order(self):
        self.assertEqual(preview([row(amount="100.00")])["status"],"not_verified")

    def test_source_owner_mismatch_never_accepted(self):
        wrong=row()
        wrong["user_id"]="another-merchant"
        self.assertEqual(preview([wrong])["status"],"not_verified")
        self.assertEqual(preview(owner="another-merchant")["status"],"not_verified")

    def test_two_inconsistent_transactions_are_not_a_full_single_capture(self):
        other=row()
        other["native_transaction_evidence"][0]["transaction_id"]="other-id"
        other["transaction_id"]="other-id"
        other["native_transaction_evidence"][0]["reference"]="other-ref"
        self.assertEqual(preview([row(),other])["status"],"not_verified")

    def test_wrong_currency_or_unbound_source_blocked(self):
        self.assertEqual(preview([row(currency="USD")])["status"],"not_verified")
        unbound=row()
        unbound["native_transaction_evidence"][0].pop("provider_order_id")
        unbound["native_transaction_evidence"][0]["unbound_order"]=True
        self.assertEqual(preview([unbound])["status"],"not_verified")

    def test_missing_or_overfull_rows_refused(self):
        self.assertEqual(preview([])["reason"],"transaction_rows_incomplete")
        self.assertEqual(preview([row() for _ in range(33)])["status"],"not_verified")

    def test_conflicting_raw_payment_status_never_overruled_by_transaction(self):
        raw=paid_source()
        raw["payment"]={"status":"unpaid"}
        self.assertEqual(preview(raw=raw)["status"],"not_verified")

    def test_transaction_timestamp_cannot_precede_order_creation(self):
        self.assertEqual(preview([row(paid_at="2026-11-24T02:00:00+03:00")])["status"],
                         "not_verified")


if __name__=="__main__":
    unittest.main()
