"""Flagged Salla payment inbox capture is inert by default and retries safely."""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch, AsyncMock

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
        review = types.ModuleType("operational_payment_evidence_review")
        review.stage_unverified_review = AsyncMock(return_value={"status":"pending_evidence"})
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", {"provider":"tabby","captures":[]}, [])):
                with patch.dict(sys.modules, {"operational_payment_evidence_review":review}):
                    self.assertTrue(asyncio.run(stage_verified_payment_update(None, source_item())))
        review.stage_unverified_review.assert_awaited_once()

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

    def test_paid_without_reference_enters_review_not_money(self):
        source = source_item()
        source["event"]["data"]["payment"] = {"status": "paid"}
        review = types.ModuleType("operational_payment_evidence_review")
        review.stage_unverified_review = AsyncMock(return_value={"status": "pending_evidence"})
        money = types.ModuleType("operational_payment_evidence_inbox")
        money.stage_normalized_captures = AsyncMock()
        with patch.dict(os.environ, {FLAG: "1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", {"provider":"tabby", "captures":[]},
                                     [{"code":"provider_capture_evidence_incomplete",
                                       "provider":"tabby"}])):
                with patch.dict(sys.modules, {
                    "operational_payment_evidence_review":review,
                    "operational_payment_evidence_inbox":money,
                }):
                    self.assertTrue(asyncio.run(stage_verified_payment_update(None, source)))
        review.stage_unverified_review.assert_awaited_once()
        money.stage_normalized_captures.assert_not_awaited()

    def test_unpaid_order_creation_does_not_create_review_noise(self):
        source = source_item()
        source["event"]["event"] = "order.created"
        review = types.ModuleType("operational_payment_evidence_review")
        review.stage_unverified_review = AsyncMock()
        with patch.dict(os.environ, {FLAG: "1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", {"provider":"tabby","captures":[]}, [])):
                with patch.dict(sys.modules, {"operational_payment_evidence_review":review}):
                    self.assertTrue(asyncio.run(stage_verified_payment_update(None, source)))
        review.stage_unverified_review.assert_not_awaited()

    def test_paid_order_unknown_provider_enters_review_only(self):
        source = source_item()
        source["event"]["data"]["payment"] = {"status":"paid"}
        review = types.ModuleType("operational_payment_evidence_review")
        review.stage_unverified_review = AsyncMock(return_value={"status":"pending_evidence"})
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", None,
                                     [{"code":"provider_payment_method_conflict"}])):
                with patch.dict(sys.modules, {"operational_payment_evidence_review":review}):
                    self.assertTrue(asyncio.run(stage_verified_payment_update(None, source)))
        args = review.stage_unverified_review.await_args.args
        self.assertEqual(args[4], "unknown")

    def test_review_staging_failure_keeps_retry(self):
        source = source_item()
        source["event"]["data"]["payment"] = {"status": "paid"}
        review = types.ModuleType("operational_payment_evidence_review")
        review.stage_unverified_review = AsyncMock(side_effect=RuntimeError("db unavailable"))
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1", {"provider":"tabby","captures":[]},
                                     [{"code":"provider_capture_evidence_incomplete",
                                       "provider":"tabby"}])):
                with patch.dict(sys.modules, {"operational_payment_evidence_review":review}):
                    self.assertFalse(asyncio.run(stage_verified_payment_update(None, source)))

    def test_historical_direct_post_goes_to_review_and_does_not_retry_forever(self):
        item = source_item()
        item["merchant_id"] = "store1"
        item["order_number"] = "30001"
        inbox = types.ModuleType("operational_payment_evidence_inbox")
        inbox.stage_normalized_captures = AsyncMock(return_value=[{"status":"evidence_received"}])
        direct = types.ModuleType("operational_payment_first_capture_writer")
        direct.enabled = lambda: True
        direct.post_first_salla_capture = AsyncMock(return_value={
            "status":"review_required", "reason_code":"historical_capture_requires_reconciliation"})
        review = types.ModuleType("operational_payment_evidence_review")
        review.stage_unverified_review = AsyncMock(return_value={"status":"pending_evidence"})
        verified = {"provider":"tabby", "currency":"SAR", "gross":"300.00",
                    "captures":[{"id":"t1", "amount":"300.00", "captured_at":"2026-11-24T12:01:00Z"}],
                    "refunds":[]}
        with patch.dict(os.environ, {FLAG:"1"}):
            with patch("operational_payment_salla_event_capture.normalized_webhook_capture",
                       return_value=("o1",verified,[])):
                with patch.dict(sys.modules, {
                    "operational_payment_evidence_inbox":inbox,
                    "operational_payment_first_capture_writer":direct,
                    "operational_payment_evidence_review":review,
                }):
                    self.assertTrue(asyncio.run(stage_verified_payment_update(None,item)))
        direct.post_first_salla_capture.assert_awaited_once()
        review.stage_unverified_review.assert_awaited_once()
        self.assertEqual(review.stage_unverified_review.await_args.args[-1],
                         ["historical_capture_requires_reconciliation"])

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
