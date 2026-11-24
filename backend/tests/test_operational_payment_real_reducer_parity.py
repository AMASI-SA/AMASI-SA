"""Direct first capture versus the REAL operational_balance_engine.reconcile.

Unlike synthetic snapshot-copy parity, this independently executes the
canonical reducer with the same saved source and applicable fee policies.
No database, remote Salla, CAS write, or production money is involved.
"""
import copy
import sys
import types
import unittest
from unittest.mock import patch

from operational_balance_engine import reconcile
from operational_payment_direct_provider import apply_first_provider_capture
from operational_payment_full_refresh_parity import compare_first_provider_capture
from test_operational_payment_direct_provider import state, normalized

AT = "2026-11-24T21:03:00+03:00"


def proof():
    raw = normalized()
    raw["order_created_at"] = "2026-11-24T20:55:00+03:00"
    return raw


def source_snapshot(payment):
    return {
        "orders": [{
            "id": "order1", "order_number": "10001",
            "created_at": payment["order_created_at"],
            "updated_at": "2026-11-24T21:02:00+03:00",
            "currency": "SAR", "status": "in_progress",
            "items": [], "payment": copy.deepcopy(payment),
        }],
        "fee_policies": copy.deepcopy(state()["engine"]["provider_fee_inputs"]["policies"]),
        "issues": [],
    }


def run_actual_reconcile(snapshot, source, clock):
    # The reducer's pure calculation checkpoint does not require a web server.
    runtime=types.ModuleType("operational_demand_refresh")
    runtime.calculation_checkpoint=lambda:None
    with patch.dict(sys.modules, {"operational_demand_refresh":runtime}):
        return reconcile(snapshot,source,clock)


class RealReducerParityTests(unittest.TestCase):
    def test_first_dated_capture_exactly_matches_full_reducer(self):
        before=state()
        payment=proof()
        direct=copy.deepcopy(before)
        outcome=apply_first_provider_capture(
            direct,order_id="order1",order_number="10001",
            normalized=payment,as_of=AT)
        self.assertEqual(outcome["status"],"posted")
        independent=run_actual_reconcile(
            copy.deepcopy(before),source_snapshot(payment),AT)
        receipt=compare_first_provider_capture(
            before,direct,independent,order_id="order1",
            provider="tabby",reference="native1")
        self.assertEqual(receipt["issues"],[],receipt)
        self.assertEqual(receipt["status"],"match")
        self.assertFalse(receipt["release_verified"])

    def test_direct_then_full_reducer_never_credits_same_capture_twice(self):
        before=state()
        payment=proof()
        direct=copy.deepcopy(before)
        apply_first_provider_capture(
            direct,order_id="order1",order_number="10001",
            normalized=payment,as_of=AT)
        repeated=run_actual_reconcile(
            copy.deepcopy(direct),source_snapshot(payment),
            "2026-11-24T21:04:00+03:00")
        self.assertEqual(repeated["engine"]["facts"]["capture:native1"],
                         direct["engine"]["facts"]["capture:native1"])
        self.assertEqual(repeated["engine"]["obligations"]["provider:order1"]["confirmed"],
                         "300.00")
        self.assertEqual(repeated.get("movements",[]),direct.get("movements",[]))


if __name__=="__main__":
    unittest.main()
