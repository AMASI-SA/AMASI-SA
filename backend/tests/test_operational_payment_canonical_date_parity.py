"""Parity checks for dated initial capture and authoritative recognition_day.

The canonical provider reducer requires the original capture timestamp. These
tests compare the direct source claim's business date against the SAME date
policy used by operational_balance_engine.reconcile, not today's order status.
"""
import copy
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch

from operational_provider_dates import recognition_day, UNPROVEN
from operational_payment_direct_provider import apply_first_provider_capture
from operational_payment_direct_order_page import apply_first_paid_order_page, FirstPageHold
from test_operational_payment_direct_provider import state, normalized, STAMP
from test_operational_payment_direct_order_page import paid_source, source


class CanonicalDateParityTests(unittest.TestCase):
    def _canonical_day(self, payment, as_of=STAMP):
        stub=types.ModuleType("operational_demand_refresh")
        stub.calculation_checkpoint=lambda:None
        with patch.dict(sys.modules, {"operational_demand_refresh":stub}):
            return recognition_day(
                None, payment["captures"], currency="SAR",
                confirmed=Decimal(payment["gross"]),
                captured=Decimal(payment["gross"]),
                created_at=payment["order_created_at"], as_of=as_of,
            )

    def test_direct_dated_capture_matches_canonical_booking_day(self):
        s=state()
        payment=normalized()
        outcome=apply_first_provider_capture(
            s, order_id="o-1",order_number="10001",
            normalized=payment,as_of=STAMP)
        self.assertEqual(outcome["status"],"posted")
        reference=payment["captures"][0]["id"]
        self.assertEqual(
            s["engine"]["facts"]["capture:"+reference]["amount"],"300.00")
        day=self._canonical_day(payment)
        self.assertEqual(
            s["engine"]["obligations"]["provider:o-1"]["business_date"],
            day.isoformat())
        self.assertEqual(
            s["engine"]["obligations"]["provider:o-1"]["confirmed"],"300.00")
        self.assertEqual(
            s["engine"]["provider_reports"]["provider:o-1"]["confirmed_receivable"],
            "300.00")

    def test_pre_created_capture_rejected_by_both_paths(self):
        s=state()
        payment=normalized()
        payment["order_created_at"]="2026-11-24T21:05:00+03:00"
        prior=copy.deepcopy(s)
        with self.assertRaises(ValueError):
            self._canonical_day(payment)
        with self.assertRaises(ValueError):
            apply_first_provider_capture(s,order_id="o-1",
                order_number="10001",normalized=payment,as_of=STAMP)
        self.assertEqual(s,prior)

    def test_source_page_cannot_supply_canonical_original_capture_day(self):
        no_native={"provider":"tamara","currency":"SAR","gross":"173.99",
                   "captures":[],"order_created_at":"2026-11-24T02:49:25+03:00"}
        with self.assertRaisesRegex(ValueError,UNPROVEN):
            self._canonical_day(no_native)
        s=state()
        s["owner_id"]="merchant1"
        prior=copy.deepcopy(s)
        with self.assertRaisesRegex(FirstPageHold,"original_capture_date_required"):
            apply_first_paid_order_page(s,raw=paid_source(),source=source(),
                owner="merchant1",order_number="301556476",
                as_of="2026-11-24T03:00:00+03:00")
        self.assertEqual(s,prior)


if __name__=="__main__":
    unittest.main()
