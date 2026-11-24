"""Actual canonical provider-history pooled settlement gate exercised in isolation.

The direct first-capture writer must invoke operational_provider_history's real
late_payment_blocks instead of merely checking an allocation to this order.
All tests are pure: no database, no transaction, no external provider call.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from operational_payment_direct_provider import (
    InitialCaptureHold, apply_first_provider_capture,
)
from test_operational_payment_direct_provider import state, normalized, STAMP


MODULE = Path(__file__).resolve().parents[1] / "operational_provider_history.py"


def actual_history():
    store=types.ModuleType("operational_balance_store")
    store.digest=lambda value:hashlib.sha256(json.dumps(
        value,sort_keys=True,ensure_ascii=False,separators=(",",":"),default=str
    ).encode()).hexdigest()
    runtime=types.ModuleType("operational_financial_activity_runtime")
    runtime.checked_rows=lambda rows:rows
    runtime.preparation_checkpoint=lambda:None
    with patch.dict(sys.modules,{
        "operational_balance_store":store,
        "operational_financial_activity_runtime":runtime,
    }):
        spec=importlib.util.spec_from_file_location(
            "operational_provider_history_test", MODULE)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def first(state_snapshot):
    return apply_first_provider_capture(
        state_snapshot,order_id="order1",order_number="10001",
        normalized=normalized(),as_of=STAMP,
    )


class CanonicalPooledSettlementTests(unittest.TestCase):
    def test_unallocated_provider_payout_blocks_new_capture(self):
        s=state()
        s["movements"]=[{
            "id":"payout-1","kind":"settlement",
            "party_type":"provider","party_id":"tabby","currency":"SAR",
            "direction":"incoming","amount":"250.00","actual_fee_amount":"0.00",
            "occurred_at":"2026-11-24T21:01:30+03:00",
            "allocations":[],
        }]
        before=copy.deepcopy(s)
        with patch.dict(sys.modules,{"operational_provider_history":actual_history()}):
            with self.assertRaisesRegex(InitialCaptureHold,"pooled_settlement"):
                first(s)
        self.assertEqual(s,before)

    def test_fully_allocated_unrelated_payout_does_not_invent_overlap(self):
        s=state()
        s["engine"]["obligations"]["provider:other-order"]={
            "id":"provider:other-order","kind":"provider","party_type":"provider",
            "party_id":"tabby","currency":"SAR","direction":"receivable",
            "confirmed":"100.00","expected":"0.00",
        }
        s["movements"]=[{
            "id":"payout-2","kind":"settlement",
            "party_type":"provider","party_id":"tabby","currency":"SAR",
            "direction":"incoming","amount":"100.00","actual_fee_amount":"0.00",
            "occurred_at":"2026-11-24T21:01:30+03:00",
            "allocations":[{"obligation_id":"provider:other-order","amount":"100.00"}],
        }]
        with patch.dict(sys.modules,{"operational_provider_history":actual_history()}):
            result=first(s)
        self.assertEqual(result["status"],"posted")
        self.assertEqual(s["engine"]["obligations"]["provider:order1"]["confirmed"],
                         "300.00")
        self.assertEqual(s["movements"][0]["amount"],"100.00")

if __name__=="__main__":
    unittest.main()
