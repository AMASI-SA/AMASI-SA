"""CAS-backed native Salla order-page first payment with current owner/source."""
import asyncio
import copy
import hashlib
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from operational_payment_first_order_page_writer import (
    PAGE_POST_FLAG, post_first_paid_page,
)
from test_operational_payment_direct_order_page import (
    state as sample_state, paid_source, source as salla_source,
)

STAMP="2026-11-24T03:00:00+03:00"


def fake_context(*, owner="merchant1", changing_source=False):
    snapshot=sample_state()
    doc={**salla_source(), "raw_by_source":{"salla_direct":paid_source()}}
    calls={"reads":0, "commits":0, "audits":0}
    class Orders:
        async def find_one(self, query, projection):
            calls["reads"]+=1
            row=copy.deepcopy(doc)
            if changing_source and calls["reads"]>=2:
                row["raw_by_source"]["salla_direct"]["payment_methods"][0]["amount"]="1.00"
            if query["user_id"]!=row["user_id"] or str(query["order_number"])!=row["order_number"]:
                return None
            return row
    db=types.SimpleNamespace(unified_orders=Orders())
    store=types.ModuleType("operational_balance_store")
    store.digest=lambda value: hashlib.sha256(json.dumps(value,sort_keys=True,
         ensure_ascii=False,separators=(",",":"),default=str).encode()).hexdigest()
    store.fail=lambda code,msg,status: (_ for _ in ()).throw(ValueError(code))
    store.now=lambda:STAMP
    store.audit=lambda *args,**kwargs:calls.__setitem__("audits",calls["audits"]+1)
    async def mutate(db, tenant, op, **kwargs):
        candidate=copy.deepcopy(snapshot)
        result=await op(candidate)
        changed=candidate!=snapshot
        if changed and kwargs.get("before_commit"):
            await kwargs["before_commit"](snapshot,candidate)
        if changed:
            snapshot.clear()
            snapshot.update(candidate)
            calls["commits"]+=1
        return result
    store.mutate=mutate
    service=types.ModuleType("operational_balance_service")
    service.active_gate=AsyncMock()
    salla=types.ModuleType("salla_integration.webhook_order_sync")
    salla._resolve_user_id=AsyncMock(return_value=owner)
    package=types.ModuleType("salla_integration")
    package.__path__=[]
    history=types.ModuleType("operational_provider_history")
    history.late_payment_blocks=lambda *args,**kwargs:False
    # The separately tested real refresh-health gate uses Motor/pymongo.
    # Here exercise CAS ordering and assert both health admission points.
    health=types.ModuleType("operational_payment_refresh_health_gate")
    health.require_current_refresh_health=AsyncMock(return_value=True)
    modules={
        "operational_balance_store":store,"operational_balance_service":service,
        "salla_integration":package,"salla_integration.webhook_order_sync":salla,
        "operational_provider_history":history,
        "operational_payment_refresh_health_gate":health,
    }
    return snapshot,db,calls,modules


class PaidPageCASWriterTests(unittest.TestCase):
    def test_off_by_default_no_reads_or_financial_writes(self):
        with patch.dict("os.environ", {PAGE_POST_FLAG:"0"}):
            result=asyncio.run(post_first_paid_page(None,"merchant1","store1",
                        "301556476",source_ref="event-1"))
        self.assertEqual(result["status"],"posting_disabled")

    def test_authoritative_current_paid_page_returns_review_without_money(self):
        snapshot,db,calls,modules=fake_context()
        before=copy.deepcopy(snapshot)
        with patch.dict("os.environ", {PAGE_POST_FLAG:"1"}):
            with patch.dict(sys.modules,modules):
                result=asyncio.run(post_first_paid_page(db,"merchant1","store1",
                    "301556476",source_ref="event-1",clock=STAMP))
        self.assertEqual(result["status"],"review_required")
        self.assertEqual(result["reason_code"],"paid_page_original_capture_date_required")
        self.assertEqual(snapshot,before)
        self.assertEqual(calls["commits"],0)
        self.assertEqual(calls["audits"],0)
        self.assertEqual(calls["reads"],1)

    def test_merchant_changed_blocks_before_db_write(self):
        snapshot,db,calls,modules=fake_context(owner="other-owner")
        with patch.dict("os.environ", {PAGE_POST_FLAG:"1"}):
            with patch.dict(sys.modules,modules):
                with self.assertRaisesRegex(ValueError,"merchant_owner_changed"):
                    asyncio.run(post_first_paid_page(db,"merchant1","store1",
                        "301556476",source_ref="event-1",clock=STAMP))
        self.assertEqual(calls["commits"],0)

    def test_source_version_change_cannot_post_without_original_capture_date(self):
        snapshot,db,calls,modules=fake_context(changing_source=True)
        before=copy.deepcopy(snapshot)
        with patch.dict("os.environ", {PAGE_POST_FLAG:"1"}):
            with patch.dict(sys.modules,modules):
                result=asyncio.run(post_first_paid_page(db,"merchant1","store1",
                    "301556476",source_ref="event-1",clock=STAMP))
        self.assertEqual(result["status"],"review_required")
        self.assertEqual(snapshot,before)
        self.assertEqual(calls["commits"],0)

    def test_stale_operational_source_may_queue_review_but_never_money(self):
        snapshot,db,calls,modules=fake_context()
        snapshot["engine"]["as_of"]="2026-11-20T10:00:00+03:00"
        before=copy.deepcopy(snapshot)
        with patch.dict("os.environ", {PAGE_POST_FLAG:"1"}):
            with patch.dict(sys.modules,modules):
                result=asyncio.run(post_first_paid_page(db,"merchant1","store1",
                    "301556476",source_ref="event-1",clock=STAMP))
        self.assertEqual(result["status"],"review_required")
        self.assertEqual(snapshot,before)
        self.assertEqual(calls["commits"],0)


if __name__=="__main__":
    unittest.main()
