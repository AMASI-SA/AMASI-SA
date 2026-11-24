"""First-order Salla page balance shares canonical projection, not captured_at."""
import copy
import sys
import types
import unittest
from unittest.mock import patch
from operational_payment_direct_order_page import FirstPageHold, apply_first_paid_order_page
from test_operational_payment_salla_order_page import sample, source, STAMP


def state():
    return {
        "owner_id":"merchant1","status":"active",
        "started_at":"2026-10-03T00:00:00+03:00",
        "movements":[], "openings":{"existing":{"amount":"500.00"}},
        "engine":{
            "as_of":"2026-11-24T02:30:00+03:00","facts":{},
            "obligations":{},"provider_reports":{},"order_versions":{},
            "provider_fee_inputs":{"policies":[{
                "provider":"tamara","currency":"SAR","status":"active",
                "effective_from":"2026-01-01","id":"tamara-2026",
                "percentage":"6.99","fixed_amount":"1.50",
                "vat_treatment":"exclusive","vat_rate":"15"
            }]},
        },
    }


def paid_source():
    raw=sample()
    raw["date"]={"date":"2026-11-24 02:49:25.000000","timezone":"Asia/Riyadh"}
    return raw


def post(s, raw=None, as_of="2026-11-24T03:00:00+03:00"):
    # The isolated policy workflow intentionally doesn't install FastAPI or
    # Motor. Stub only the provider-history import needed by observed_balance,
    # and run it with movements=[] so its settlement shortcut is never used.
    # Actual history/settlement integration still requires Preview UAT.
    history = types.ModuleType("operational_provider_history")
    history.late_payment_blocks = lambda *args, **kwargs: False
    with patch.dict(sys.modules, {"operational_provider_history":history}):
        return apply_first_paid_order_page(
            s, raw=raw or paid_source(), source=source(), owner="merchant1",
            order_number="301556476",as_of=as_of)


class DirectPaidPageTests(unittest.TestCase):
    def test_new_tamara_paid_page_is_one_provider_claim(self):
        s=state()
        previous=copy.deepcopy(s)
        result=post(s)
        self.assertEqual(result["status"],"posted")
        self.assertEqual(result["evidence"],"cumulative_salla_order_page")
        claim=s["engine"]["obligations"]["provider:580626783"]
        self.assertEqual(claim["confirmed"],"173.99")
        self.assertEqual(claim["party_id"],"tamara")
        self.assertEqual(claim["currency"],"SAR")
        page=s["engine"]["facts"]["provider_order_balance:580626783"]
        self.assertEqual(page["paid"],"173.99")
        self.assertIn("observed_at",page)
        self.assertNotIn("captured_at",page)
        self.assertEqual(s["movements"],previous["movements"])
        self.assertEqual(s["openings"],previous["openings"])

    def test_exact_replay_not_double_claim(self):
        s=state()
        post(s)
        before=copy.deepcopy(s)
        self.assertEqual(post(s)["status"],"already_posted")
        self.assertEqual(before,s)

    def test_exact_replay_after_refresh_advances(self):
        s=state()
        post(s)
        s["engine"]["as_of"]="2026-11-24T03:10:00+03:00"
        self.assertEqual(post(s,as_of="2026-11-24T03:20:00+03:00")["status"],
                         "already_posted")

    def test_new_historical_page_requires_reconciliation(self):
        s=state()
        s["engine"]["as_of"]="2026-11-24T03:10:00+03:00"
        with self.assertRaisesRegex(FirstPageHold,"historical"):
            post(s)
        self.assertFalse(s["engine"]["facts"])

    def test_bank_paid_amount_not_provider_claim(self):
        s=state()
        with self.assertRaisesRegex(FirstPageHold,"unverified"):
            post(s,raw=sample("bank"))
        self.assertFalse(s["engine"]["facts"])

    def test_same_native_reference_on_different_order_is_refused(self):
        s = state()
        s["engine"]["facts"]["provider_order_balance:other-order"] = {
            "order_id": "other-order", "provider": "tamara",
            "transaction_reference": "t-transaction-01", "paid": "173.99",
        }
        with self.assertRaisesRegex(FirstPageHold, "reference_reused"):
            post(s)
        self.assertNotIn("provider:580626783", s["engine"]["obligations"])

    def test_cross_order_capture_reference_reuse_is_refused(self):
        s = state()
        s["engine"]["facts"]["capture:t-transaction-01"] = {
            "order_id": "another-order", "provider": "tamara",
            "amount": "173.99",
        }
        with self.assertRaisesRegex(FirstPageHold, "reference_reused"):
            post(s)
        self.assertNotIn("provider:580626783", s["engine"]["obligations"])

    def test_cross_order_manual_collection_reference_reuse_is_refused(self):
        s = state()
        s["customer_collections"] = [{
            "status": "confirmed", "reference": "t-transaction-01",
            "order_id": "another-order", "destination_type": "provider",
            "destination_id": "tamara", "amount": "173.99",
        }]
        with self.assertRaisesRegex(FirstPageHold, "reference_reused"):
            post(s)
        self.assertNotIn("provider:580626783", s["engine"]["obligations"])

    def test_existing_provider_claim_refuses_second_credit(self):
        s=state()
        s["engine"]["obligations"]["provider:580626783"]={"confirmed":"173.99"}
        with self.assertRaisesRegex(FirstPageHold,"preexisting"):
            post(s)

    def test_missing_policy_never_fakes_net(self):
        s=state()
        s["engine"]["provider_fee_inputs"]["policies"]=[]
        with self.assertRaisesRegex(FirstPageHold,"policy_missing"):
            post(s)

    def test_source_snapshot_owner_changed_no_claim(self):
        s=state()
        s["owner_id"]="other-merchant"
        with self.assertRaisesRegex(FirstPageHold,"owner_or_start"):
            post(s)

    def test_created_after_cutover_required(self):
        s=state()
        raw=paid_source()
        raw["date"]={"date":"2026-10-02 23:00:00","timezone":"Asia/Riyadh"}
        with self.assertRaisesRegex(FirstPageHold,"period"):
            post(s,raw=raw)


if __name__ == "__main__":
    unittest.main()
