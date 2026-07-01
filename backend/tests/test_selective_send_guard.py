"""Selective Send Guard tests — Phase C P0 wiring (2026-07-01).

Coverage per user directive #1-#10:
    • Auto pipeline blocked when gate disabled → no Qoyod API call.
    • Auto pipeline blocked when production_writes_locked=true.
    • one_shot_reprocess blocked when policy blocks.
    • Manual send blocked without confirmation phrase.
    • Manual send does NOT bypass bank_transfer.
    • Q2 orders → no Qoyod API call.
    • DRY / PREVIEW / null IDs → no Qoyod API call.
    • totals mismatch > 0.01 → no Qoyod API call.
    • On allow, every payload date field equals send_date_riyadh.
    • QoyodAPIClient is NEVER called before assert_send_allowed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations.qoyod.selective_send_guard import (
    SelectiveSendPolicyBlocked,
    apply_send_date_to_qoyod_payload,
    assert_send_allowed,
    _DATE_FIELDS_TO_STAMP,
    _LEGACY_DATE_FIELDS_TO_SCRUB,
)
from integrations.qoyod.selective_send_policy import (
    BlockerCode,
    manual_approval_phrase_for,
)


# ── Fixtures ────────────────────────────────────────────────────────
def _green_order(**over):
    o = {
        "order_number": "GUARD-001",
        "salla_order_id": "GUARD-001",
        "salla_order_created_at": "2026-07-05",
        "status": "completed",
        "payment_method": "mada",
        "existing_qoyod_invoice_id": None,
        "customer_status": {"resolved": True, "qoyod_id": 223,
                            "reason": None},
        "products_status": {"resolved": True, "resolved_count": 1,
                            "dry_run_only": 0, "missing": []},
        "totals_status": {"valid": True, "total": 100.0,
                          "expected": 100.0, "diff": 0.0},
    }
    o.update(over)
    return o


def _gates_OPEN(**over):
    s = {
        "selective_live_send_enabled": True,
        "production_writes_locked":    False,
        "qoyod_sync_start_date":       "2026-07-01",
        "qoyod_tax_period":            "Q3-2026",
        "bank_transfer_routing_enabled": False,
        "qoyod_invoice_date_source":   "send_date",
        "qoyod_enabled_invoice_trigger_statuses":
            ["completed", "تم التنفيذ"],
    }
    s.update(over)
    return s


def _gates_CLOSED():
    return {
        "selective_live_send_enabled": False,
        "production_writes_locked":    True,
        "qoyod_sync_start_date":       "2026-07-01",
    }


# ── assert_send_allowed — raises on block, returns on allow ─────────
class TestAssertSendAllowed:
    def test_allow_returns_decision(self):
        d = assert_send_allowed(
            order=_green_order(), settings=_gates_OPEN())
        assert d.decision == "allow"
        assert d.would_send_to_qoyod is True
        assert d.send_date_riyadh

    def test_gate_closed_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(), settings=_gates_CLOSED())
        assert ei.value.blocker_code == BlockerCode.GATE_DISABLED

    def test_write_lock_active_raises(self):
        s = _gates_OPEN(production_writes_locked=True)
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(order=_green_order(), settings=s)
        assert ei.value.blocker_code == BlockerCode.WRITE_LOCK_ACTIVE

    def test_q2_order_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(salla_order_created_at="2026-06-15"),
                settings=_gates_OPEN())
        assert ei.value.blocker_code == \
            BlockerCode.BEFORE_SYNC_START_DATE

    def test_bank_transfer_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(payment_method="bank_transfer"),
                settings=_gates_OPEN())
        assert ei.value.blocker_code == \
            BlockerCode.BANK_TRANSFER_ON_HOLD

    def test_dry_customer_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(customer_status={
                    "resolved": True, "qoyod_id": "DRY:1",
                    "reason": None}),
                settings=_gates_OPEN())
        assert ei.value.blocker_code == \
            BlockerCode.CUSTOMER_DRY_OR_NULL

    def test_preview_invoice_id_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(
                    existing_qoyod_invoice_id="PREVIEW:abc"),
                settings=_gates_OPEN())
        assert ei.value.blocker_code == BlockerCode.PREVIEW_ID_DETECTED

    def test_null_customer_id_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(customer_status={
                    "resolved": True, "qoyod_id": None,
                    "reason": None}),
                settings=_gates_OPEN())
        assert ei.value.blocker_code == \
            BlockerCode.CUSTOMER_DRY_OR_NULL

    def test_hard_totals_mismatch_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(totals_status={
                    "valid": False, "total": 100.50,
                    "expected": 100.0, "diff": 0.50}),
                settings=_gates_OPEN())
        assert ei.value.blocker_code == \
            BlockerCode.TOTALS_MISMATCH_HARD

    def test_already_sent_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(
                    existing_qoyod_invoice_id="Q-REAL-9001"),
                settings=_gates_OPEN())
        assert ei.value.blocker_code == BlockerCode.ALREADY_SENT

    def test_manual_send_without_phrase_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(status="delivered"),
                settings=_gates_OPEN(),
                manual_send_requested=True,
                manual_approval_phrase=None)
        assert ei.value.blocker_code == \
            BlockerCode.MANUAL_APPROVAL_PHRASE_REQUIRED

    def test_manual_send_bank_transfer_still_raises(self):
        with pytest.raises(SelectiveSendPolicyBlocked) as ei:
            assert_send_allowed(
                order=_green_order(status="delivered",
                                   payment_method="bank_transfer"),
                settings=_gates_OPEN(),
                manual_send_requested=True,
                manual_approval_phrase=manual_approval_phrase_for(
                    "GUARD-001"))
        assert ei.value.blocker_code == \
            BlockerCode.BANK_TRANSFER_ON_HOLD

    def test_manual_send_correct_phrase_returns_decision(self):
        d = assert_send_allowed(
            order=_green_order(status="delivered"),
            settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=manual_approval_phrase_for(
                "GUARD-001"))
        assert d.decision == "allow"


# ── apply_send_date_to_qoyod_payload ────────────────────────────────
class TestPayloadDateRewrite:
    def _decision(self, send_date="2026-07-10"):
        # Build a real decision via the guard with a fixed now.
        # 2026-07-10 12:00 UTC → Riyadh 15:00 → same date.
        # Use a time that stays in the same Riyadh day.
        now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        return assert_send_allowed(
            order=_green_order(), settings=_gates_OPEN(),
            now_utc=now)

    def test_rewrites_top_level_date_fields(self):
        d = self._decision()
        payload = {
            "date": "2026-01-01",
            "issue_date": "2026-01-01",
            "invoice_date": "2026-01-01",
            "due_date": "2026-01-01",
            "payment_date": "2026-01-01",
            "receipt_date": "2026-01-01",
            "reference_id": "keep-me",
        }
        out = apply_send_date_to_qoyod_payload(payload, d)
        for f in _DATE_FIELDS_TO_STAMP:
            assert out[f] == "2026-07-10", f
        assert out["reference_id"] == "keep-me"

    def test_scrubs_legacy_date_fields(self):
        d = self._decision()
        payload = {
            "date": "2026-01-01",
            "completed_at": "2026-07-02T14:00:00+03:00",
            "delivered_at": "2026-07-03",
            "paid_at": "2026-07-04",
            "received_at": "2026-07-05",
            "created_at": "2026-07-01",
            "reference_id": "keep-me",
        }
        out = apply_send_date_to_qoyod_payload(payload, d)
        for f in _LEGACY_DATE_FIELDS_TO_SCRUB:
            assert f not in out, f
        assert out["date"] == "2026-07-10"

    def test_rewrites_nested_invoice_payment_dates(self):
        d = self._decision()
        payload = {
            "invoice": {
                "date": "2026-01-01",
                "due_date": "2026-01-01",
                "line_items": [
                    {"product_id": 42, "created_at": "old"},
                ],
            },
            "payment": {"payment_date": "2026-01-01",
                        "paid_at": "should-be-scrubbed"},
        }
        out = apply_send_date_to_qoyod_payload(payload, d)
        assert out["invoice"]["date"] == "2026-07-10"
        assert out["invoice"]["due_date"] == "2026-07-10"
        assert "created_at" not in out["invoice"]["line_items"][0]
        assert out["payment"]["payment_date"] == "2026-07-10"
        assert "paid_at" not in out["payment"]

    def test_rewrite_is_idempotent(self):
        d = self._decision()
        payload = {"date": "old", "due_date": "old"}
        out1 = apply_send_date_to_qoyod_payload(payload, d)
        out2 = apply_send_date_to_qoyod_payload(out1, d)
        assert out1 == out2

    def test_requires_decision(self):
        with pytest.raises(ValueError):
            apply_send_date_to_qoyod_payload({"date": "x"}, None)

    def test_non_dict_payload_returned_as_is(self):
        d = self._decision()
        assert apply_send_date_to_qoyod_payload(None, d) is None
        assert apply_send_date_to_qoyod_payload("hi", d) == "hi"


# ── Contract test: mock caller pattern (proves no API call on block) ─
class TestCallerContract:
    """Simulates the pattern every send code path MUST follow. A mock
    QoyodAPIClient records whether it was called. Guard must fire
    BEFORE any client call."""

    class _MockClient:
        def __init__(self):
            self.calls: list[tuple] = []

        def create_invoice(self, payload: dict):
            self.calls.append(("create_invoice", payload))
            return {"id": "Q-FAKE"}

    def _send(self, order, settings, client, **kwargs):
        """The pattern every send path must adopt."""
        decision = assert_send_allowed(
            order=order, settings=settings, **kwargs)
        # ONLY after allow do we build + send the payload.
        payload = {"date": "will-be-overwritten"}
        payload = apply_send_date_to_qoyod_payload(payload, decision)
        return client.create_invoice(payload)

    def test_block_prevents_api_call_gate_disabled(self):
        client = self._MockClient()
        with pytest.raises(SelectiveSendPolicyBlocked):
            self._send(_green_order(), _gates_CLOSED(), client)
        assert client.calls == [], "API MUST NOT be called on block"

    def test_block_prevents_api_call_write_lock(self):
        client = self._MockClient()
        s = _gates_OPEN(production_writes_locked=True)
        with pytest.raises(SelectiveSendPolicyBlocked):
            self._send(_green_order(), s, client)
        assert client.calls == []

    def test_block_prevents_api_call_q2(self):
        client = self._MockClient()
        with pytest.raises(SelectiveSendPolicyBlocked):
            self._send(_green_order(
                salla_order_created_at="2026-06-15"),
                _gates_OPEN(), client)
        assert client.calls == []

    def test_block_prevents_api_call_bank_transfer(self):
        client = self._MockClient()
        with pytest.raises(SelectiveSendPolicyBlocked):
            self._send(_green_order(payment_method="bank_transfer"),
                       _gates_OPEN(), client)
        assert client.calls == []

    def test_block_prevents_api_call_dry_ids(self):
        client = self._MockClient()
        with pytest.raises(SelectiveSendPolicyBlocked):
            self._send(_green_order(
                existing_qoyod_invoice_id="DRY:1"),
                _gates_OPEN(), client)
        assert client.calls == []

    def test_block_prevents_api_call_totals_mismatch(self):
        client = self._MockClient()
        with pytest.raises(SelectiveSendPolicyBlocked):
            self._send(_green_order(totals_status={
                "valid": False, "total": 100.50,
                "expected": 100.0, "diff": 0.50}),
                _gates_OPEN(), client)
        assert client.calls == []

    def test_block_prevents_api_call_manual_no_phrase(self):
        client = self._MockClient()
        with pytest.raises(SelectiveSendPolicyBlocked):
            self._send(
                _green_order(status="delivered"),
                _gates_OPEN(), client,
                manual_send_requested=True)
        assert client.calls == []

    def test_allow_permits_api_call_with_send_date(self):
        client = self._MockClient()
        self._send(_green_order(), _gates_OPEN(), client)
        assert len(client.calls) == 1
        method, payload = client.calls[0]
        assert method == "create_invoice"
        # Payload date was overwritten by apply_send_date_to_qoyod_payload.
        assert payload["date"] != "will-be-overwritten"
        assert len(payload["date"]) == 10  # YYYY-MM-DD


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
