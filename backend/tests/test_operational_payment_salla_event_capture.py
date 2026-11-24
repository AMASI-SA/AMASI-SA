"""Flagged Salla payment inbox capture is inert by default and retries safely."""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

from operational_payment_salla_event_capture import (
    FLAG, enabled, normalized_webhook_capture, stage_verified_payment_update,
)


def source_item():
    return {"_id": "event-sha-1", "user_id": "merchant-1",
            "event": {"event": "order.payment.updated", "data": {
                "id": "o1", "reference_id": "30001", "created_at": "2026-11-24T12:00:00+03:00",
                "amounts": {"total": {"amount": "300.00", "currency": "SAR"}}}}}


class SallaEventCaptureTests(unittest.TestCase):
    def test_feature_off_by_default(self):
        with patch.dict(os.environ, {FLAG: "0"}):
            self.assertFalse(enabled())
            self.assertTrue(asyncio.run(stage_verified_payment_update(None, source_item())))

    def test_wrong_event_never_captures(self):
        self.assertIsNone(normalized_webhook_capture({"event":"order.status.updated", "data":{}}))

    def test_initial_order_created_is_supported_as_payment_evidence_trigger(self):
        initial = source_item()["event"].copy()
        initial["event"] = "order.created"
        stub = types.ModuleType("operational_payment_evidence")
        calls = []
        def normalize_payment(raw, currency, total, created, as_of):
            calls.append((raw.get("id"), currency))
            return {"provider": "tabby", "currency": "SAR", "gross": "300.00",
                    "captures": [{"id": "tabby-native-001", "amount": "300.00",
                                  "captured_at": "2026-11-24T12:01:00+03:00"}]}, []
        stub.normalize_payment = normalize_payment
        with patch.dict(sys.modules, {"operational_payment_evidence":stub}):
            proof = normalized_webhook_capture(initial)
        self.assertEqual(proof[0], "o1")
        self.assertEqual(proof[1]["captures"][0]["id"], "tabby-native-001")
        self.assertEqual(calls, [("o1", "SAR")])

    def test_initial_order_with_status_only_is_not_a_paid_capture(self):
        initial = source_item()["event"].copy()
        initial["event"] = "order.created"
        stub = types.ModuleType("operational_payment_evidence")
        def normalize_payment(raw, currency, total, created, as_of):
            return {"provider": "tabby", "currency": "SAR",
                    "gross": "300.00", "captures": []}, [
                    {"code":"provider_capture_evidence_incomplete"}]
        stub.normalize_payment = normalize_payment
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch.dict(sys.modules, {"operational_payment_evidence":stub}):
                self.assertTrue(asyncio.run(stage_verified_payment_update(None, {
                    **source_item(), "event":initial})))

    def test_no_order_or_amount_never_captures(self):
        self.assertIsNone(normalized_webhook_capture({"event":"order.payment.updated", "data":{}}))

    def test_enabled_incomplete_proof_no_money(self):
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", {"provider":"tabby","captures":[]}, [])):
                self.assertTrue(asyncio.run(stage_verified_payment_update(None, source_item())))

    def test_enabled_staging_invoked_once_for_complete_capture(self):
        calls = []
        stub = types.ModuleType("operational_payment_evidence_inbox")
        async def stage(db, owner, oid, normalized, diagnostics, *, source_ref):
            calls.append((owner, oid, source_ref))
            return [{"status":"evidence_received"}]
        stub.stage_normalized_captures = stage
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", {"provider":"tabby","captures":[{"id":"t1"}]}, [])):
                with patch.dict(sys.modules, {"operational_payment_evidence_inbox":stub}):
                    self.assertTrue(asyncio.run(stage_verified_payment_update(None, source_item())))
        self.assertEqual(calls, [("merchant-1", "o1", "event-sha-1")])

    def test_failed_staging_retries(self):
        stub = types.ModuleType("operational_payment_evidence_inbox")
        async def stage(*args, **kwargs):
            raise RuntimeError("temporary database failure")
        stub.stage_normalized_captures = stage
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", {"captures":[{"id":"t1"}]}, [])):
                with patch.dict(sys.modules, {"operational_payment_evidence_inbox":stub}):
                    self.assertFalse(asyncio.run(stage_verified_payment_update(None, source_item())))


if __name__ == "__main__":
    unittest.main()
