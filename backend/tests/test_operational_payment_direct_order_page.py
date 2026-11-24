"""Salla cumulative paid page is a no-money proof until capture date exists."""
import copy
import unittest

from operational_payment_direct_order_page import FirstPageHold, apply_first_paid_order_page
from test_operational_payment_salla_order_page import sample, source


def state():
    return {
        "owner_id":"merchant1", "status":"active",
        "started_at":"2026-10-03T00:00:00+03:00",
        "movements":[],
        "openings":{"existing":{"amount":"500.00"}},
        "engine":{
            "as_of":"2026-11-24T02:30:00+03:00",
            "facts":{}, "obligations":{}, "provider_reports":{},
            "order_versions":{},
            "provider_fee_inputs":{"policies":[]},
        },
    }


def paid_source():
    raw=sample()
    raw["date"]={"date":"2026-11-24 02:49:25.000000","timezone":"Asia/Riyadh"}
    return raw


def attempt(s, raw=None):
    return apply_first_paid_order_page(
        s, raw=raw or paid_source(), source=source(),
        owner="merchant1", order_number="301556476",
        as_of="2026-11-24T03:00:00+03:00")


class FirstPaidPageNoMoneyTests(unittest.TestCase):
    def test_complete_tamara_paid_page_requires_original_gateway_date(self):
        s=state()
        before=copy.deepcopy(s)
        with self.assertRaisesRegex(FirstPageHold, "original_capture_date_required"):
            attempt(s)
        self.assertEqual(s,before)

    def test_unknown_business_date_never_uses_order_creation(self):
        s=state()
        original=paid_source()
        original["date"]={"date":"2026-11-24 02:49:25","timezone":"Asia/Riyadh"}
        with self.assertRaisesRegex(FirstPageHold, "original_capture_date_required"):
            attempt(s,original)
        self.assertEqual(s["engine"]["facts"],{})
        self.assertEqual(s["engine"]["obligations"],{})

    def test_repeated_original_order_page_is_no_money(self):
        s=state()
        for _ in range(3):
            with self.assertRaisesRegex(FirstPageHold,"original_capture_date_required"):
                attempt(s)
        self.assertEqual(s["movements"],[])
        self.assertFalse(s["engine"]["provider_reports"])

    def test_bank_transfer_is_never_a_provider_receivable(self):
        s=state()
        with self.assertRaisesRegex(FirstPageHold,"unverified"):
            attempt(s,sample("bank"))
        self.assertFalse(s["engine"]["facts"])

    def test_no_reference_stays_unverified(self):
        s=state()
        with self.assertRaisesRegex(FirstPageHold,"unverified"):
            attempt(s,sample(ref=None))

    def test_payment_pending_stays_unverified(self):
        s=state()
        raw=sample()
        raw["is_pending_payment"]=True
        with self.assertRaisesRegex(FirstPageHold,"unverified"):
            attempt(s,raw)

    def test_wrong_owner_rejected_before_financial_proof(self):
        s=state()
        s["owner_id"]="another-owner"
        with self.assertRaisesRegex(FirstPageHold,"owner_or_start"):
            attempt(s)

    def test_existing_settlement_not_mutated_by_undated_page(self):
        s=state()
        s["engine"]["facts"]["capture:old"]={
            "order_id":"different","provider":"tamara","amount":"300.00"}
        s["engine"]["obligations"]["provider:different"]={
            "confirmed":"300.00","party_id":"tamara"}
        s["movements"].append({"id":"bank-1","amount":"250.00"})
        original=copy.deepcopy(s)
        with self.assertRaisesRegex(FirstPageHold,"original_capture_date_required"):
            attempt(s)
        self.assertEqual(s,original)


if __name__=="__main__":
    unittest.main()
