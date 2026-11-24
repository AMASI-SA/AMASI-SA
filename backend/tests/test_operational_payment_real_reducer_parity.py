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

from pathlib import Path
import importlib.util

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
    # Use the actual pure reducer code while stubbing ONLY its web/DB runtime
    # module, which is not installed by this tiny policy-only CI job.
    runtime=types.ModuleType("operational_demand_refresh")
    runtime.calculation_checkpoint=lambda:None
    store=types.ModuleType("operational_balance_store")
    import hashlib, json
    store.digest=lambda value:hashlib.sha256(json.dumps(
        value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    with patch.dict(sys.modules,{
        "operational_demand_refresh":runtime,
        "operational_balance_store":store,
    }):
        spec=importlib.util.spec_from_file_location(
            "operational_balance_engine_isolated",
            Path(__file__).resolve().parents[1] / "operational_balance_engine.py",
        )
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.reconcile(snapshot,source,clock)


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
        # This version marker is emitted by the REAL reducer, not by the
        # direct CAS writer or a synthetic snapshot copy.
        self.assertEqual(
            independent["engine"]["order_versions"].get("order1"),
            "2026-11-24T21:02:00+03:00",
        )
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
