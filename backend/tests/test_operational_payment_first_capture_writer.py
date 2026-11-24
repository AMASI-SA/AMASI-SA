"""Simulated operational CAS first-payment posting with verified tenant origin."""
import asyncio
import copy
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from operational_payment_capture_adapter import payment_events_from_normalized
from operational_payment_evidence_inbox import event_record, queue_evidence_in_state
from operational_payment_first_capture_writer import (
    POST_FLAG, _recent_source_snapshot, _assert_staged_evidence,
    post_first_salla_capture,
)
from operational_payment_direct_provider import InitialCaptureHold
from test_operational_payment_direct_provider import normalized, state, STAMP


def staged():
    snapshot = state()
    native = normalized(captured_at="2026-11-24T18:01:00+00:00")
    event = payment_events_from_normalized("order1", native)[0]
    rec = event_record(event, owner="merchant1",
                       source="salla_native_transactions", source_ref="proof1")
    queue_evidence_in_state(snapshot, rec, STAMP)
    return snapshot, native


class WriterTests(unittest.TestCase):
    def test_flag_defaults_disabled(self):
        with patch.dict("os.environ", {POST_FLAG: "0"}):
            result = asyncio.run(post_first_salla_capture(
                None, "merchant1", "store1", "order1", "10001",
                normalized(), source_ref="proof1"))
        self.assertEqual(result["status"], "posting_disabled")

    def test_stale_source_rejected(self):
        snapshot, _ = staged()
        snapshot["engine"]["as_of"] = "2026-11-19T10:00:00+03:00"
        self.assertFalse(_recent_source_snapshot(snapshot, STAMP))

    def test_staged_native_reference_must_agree(self):
        snapshot, native = staged()
        self.assertIsNone(_assert_staged_evidence(snapshot, "merchant1", "proof1", "order1", native))
        with self.assertRaises(InitialCaptureHold):
            _assert_staged_evidence(snapshot, "merchant1", "another-proof", "order1", native)

    def test_owner_authorized_and_cas_post_once(self):
        snapshot, native = staged()
        tracked = {"commits":0,"audits":[]}
        store = types.ModuleType("operational_balance_store")
        async def mutate(db, owner, operation, **kwargs):
            candidate = copy.deepcopy(snapshot)
            result = await operation(candidate)
            guard = kwargs.get("before_commit")
            if guard:
                await guard(snapshot, candidate)
            snapshot.clear()
            snapshot.update(candidate)
            tracked["commits"] += 1
            return result
        def fail(code, message, status):
            raise InitialCaptureHold(code)
        store.mutate = mutate
        store.audit = lambda *args: tracked["audits"].append(args[3])
        store.fail = fail
        store.now = lambda: STAMP
        service = types.ModuleType("operational_balance_service")
        service.active_gate = AsyncMock()
        salla = types.ModuleType("salla_integration.webhook_order_sync")
        salla._resolve_user_id = AsyncMock(return_value="merchant1")
        package = types.ModuleType("salla_integration")
        package.__path__ = []
        with patch.dict("os.environ", {POST_FLAG:"1"}):
            with patch.dict(sys.modules, {
                "operational_balance_store":store, "operational_balance_service":service,
                "salla_integration":package, "salla_integration.webhook_order_sync":salla,
            }):
                result = asyncio.run(post_first_salla_capture(
                    None, "merchant1", "store1", "order1", "10001",
                    native, source_ref="proof1", clock=STAMP))
        self.assertEqual(result["status"],"posted")
        self.assertEqual(snapshot["engine"]["obligations"]["provider:order1"]["confirmed"],"300.00")
        self.assertEqual(snapshot["payment_event_evidence_inbox"]
                         [next(iter(snapshot["payment_event_evidence_inbox"]))]["status"],
                         "already_recognized")
        self.assertEqual(tracked["commits"],1)
        self.assertEqual(len(tracked["audits"]),1)
        self.assertEqual(salla._resolve_user_id.await_count,2)
        service.active_gate.assert_awaited_once()

    def test_wrong_merchant_never_persists(self):
        snapshot, native = staged()
        store = types.ModuleType("operational_balance_store")
        store.fail = lambda code,msg,status: (_ for _ in ()).throw(InitialCaptureHold(code))
        store.now = lambda: STAMP
        store.mutate = AsyncMock(side_effect=AssertionError("should never write"))
        service = types.ModuleType("operational_balance_service")
        service.active_gate = AsyncMock()
        salla = types.ModuleType("salla_integration.webhook_order_sync")
        salla._resolve_user_id = AsyncMock(return_value="another_owner")
        package = types.ModuleType("salla_integration")
        package.__path__ = []
        with patch.dict("os.environ", {POST_FLAG:"1"}):
            with patch.dict(sys.modules, {
                "operational_balance_store":store, "operational_balance_service":service,
                "salla_integration":package, "salla_integration.webhook_order_sync":salla,
            }):
                with self.assertRaisesRegex(InitialCaptureHold, "owner_changed"):
                    asyncio.run(post_first_salla_capture(
                        None, "merchant1", "store1", "order1", "10001",
                        native, source_ref="proof1", clock=STAMP))
        store.mutate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
