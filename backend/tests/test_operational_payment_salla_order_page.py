"""Observed Salla online paid orders are not dated gateway captures."""
import copy
import unittest
from operational_payment_salla_order_page import native_paid_order_page

STAMP = "2026-11-24T02:54:44+03:00"


def sample(method="tamara_installment", *, ref="t-transaction-01"):
    return {
        "id": 580626783, "reference_id": 301556476,
        "status": {"name":"بإنتظار المراجعة", "slug":"under_review"},
        "payment_method": method,
        "amounts": {"total": {"amount":173.99, "currency":"SAR"}},
        "payment_methods": [{"payment_method":method,
                             "transaction_reference":ref, "amount":"173.99"}],
        "payment_actions": {"remaining_action":{
            "has_remaining_amount":False, "paid_amount":{"amount":173.99,"currency":"SAR"},
            "remaining_amount":None}, "refund_action":{}},
        "updated_at": STAMP,
    }


def source():
    return {"user_id":"merchant1", "salla_order_id":"580626783",
            "order_id":"580626783", "order_number":"301556476",
            "g47_salla_snapshot":{"source_updated_at":STAMP,"component_pending":False}}


class OrderPageEvidenceTests(unittest.TestCase):
    def test_tamara_full_paid_page_is_observation_not_capture(self):
        raw = sample()
        original = copy.deepcopy(raw)
        result = native_paid_order_page(raw, owner="merchant1", order_number="301556476",
                                        source=source())
        self.assertIsNotNone(result)
        self.assertEqual(result["paid"],"173.99")
        self.assertEqual(result["provider"],"tamara")
        self.assertEqual(result["transaction_reference"],"t-transaction-01")
        self.assertEqual(result["date_basis"],"source_observation_only")
        self.assertNotIn("captured_at",result)
        self.assertEqual(raw,original)

    def test_mada_online_page_is_salla_provider(self):
        raw=sample("mada")
        self.assertEqual(native_paid_order_page(raw, owner="merchant1",
                         order_number="301556476",source=source())["provider"],"salla")

    def test_bank_transfer_with_paid_amount_is_not_online_capture(self):
        raw=sample("bank",ref="")
        self.assertIsNone(native_paid_order_page(raw, owner="merchant1",
                           order_number="301556476",source=source()))

    def test_bank_even_if_reference_present_must_not_be_recognized(self):
        raw=sample("bank",ref="bank-claim")
        self.assertIsNone(native_paid_order_page(raw, owner="merchant1",
                           order_number="301556476",source=source()))

    def test_no_native_reference_kept_pending(self):
        self.assertIsNone(native_paid_order_page(sample(ref=None), owner="merchant1",
                           order_number="301556476",source=source()))

    def test_only_full_native_paid_balance_eligible(self):
        raw=sample()
        raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"]=150
        self.assertIsNone(native_paid_order_page(raw, owner="merchant1",
                          order_number="301556476",source=source()))

    def test_unverified_salla_source_version_rejected(self):
        stale=source()
        stale["g47_salla_snapshot"]["source_updated_at"]="2026-11-24T01:00:00+03:00"
        self.assertIsNone(native_paid_order_page(sample(), owner="merchant1",
                          order_number="301556476",source=stale))

    def test_other_owner_rejected(self):
        self.assertIsNone(native_paid_order_page(sample(),owner="other-merchant",
                          order_number="301556476",source=source()))

    def test_payment_pending_blocked(self):
        raw=sample()
        raw["is_pending_payment"]=True
        self.assertIsNone(native_paid_order_page(raw,owner="merchant1",
                          order_number="301556476",source=source()))

    def test_not_actual_awaiting_review_blocked(self):
        raw=sample()
        raw["status"]["name"]="تم المراجعة"
        self.assertIsNone(native_paid_order_page(raw,owner="merchant1",
                          order_number="301556476",source=source()))

    def test_split_payment_must_use_distinct_transaction_path(self):
        raw=sample()
        raw["payment_methods"].append(copy.deepcopy(raw["payment_methods"][0]))
        self.assertIsNone(native_paid_order_page(raw,owner="merchant1",
                          order_number="301556476",source=source()))


if __name__ == "__main__":
    unittest.main()
