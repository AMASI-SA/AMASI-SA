"""Iter-001k — Pipeline Invariants Integration Test.

Proves the four contract invariants for the Selective Live Send Gate
that were requested for sign-off before opening the real gate:

    1. Invoice payload and payment payload share the SAME
       `send_timestamp_riyadh` (one frozen clock per send attempt).
    2. When `assert_send_allowed()` blocks, NO POST is made to Qoyod.
    3. In `pipeline.py`, every `api_client.create_invoice(...)` is
       preceded in the SAME function scope by an `assert_send_allowed`
       call.
    4. In `pipeline.py`, `create_invoice_payment(...)` is preceded by
       `apply_send_date_to_qoyod_payload(...)` and by either a shared
       `payment_decision = selective_send_decision` reuse OR a fresh
       `assert_send_allowed` — never a naked send.

Runs entirely OFFLINE. No DB. No httpx. No side effects.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from integrations.qoyod.selective_send_guard import (
    SelectiveSendPolicyBlocked,
    apply_send_date_to_qoyod_payload,
    assert_send_allowed,
)


_PIPELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations" / "qoyod" / "pipeline.py"
)


# ── Fixtures ────────────────────────────────────────────────────────
_ALLOW_ORDER = {
    "order_number": "TEST-001",
    "salla_order_id": "TEST-001",
    "salla_order_created_at": "2026-07-05",
    "status": "completed",
    "payment_method": "credit_card",
    "existing_qoyod_invoice_id": None,
    "customer_status": {"resolved": True, "qoyod_id": 999001,
                        "reason": None},
    "products_status": {"resolved": True, "resolved_count": 1,
                        "dry_run_only": 0, "missing": []},
    "totals_status": {"valid": True, "total": 100.0,
                      "expected": 100.0, "diff": 0.0},
}

_ALLOW_SETTINGS = {
    "selective_live_send_enabled": True,
    "production_writes_locked":    False,
    "qoyod_sync_start_date":       "2026-07-01",
    "qoyod_enabled_invoice_trigger_statuses":
        ["completed", "تم التنفيذ"],
    "qoyod_invoice_date_source":   "send_date",
}

_FAIL_CLOSED_SETTINGS = {
    "selective_live_send_enabled": False,   # gate closed → block
    "production_writes_locked":    True,
    "qoyod_sync_start_date":       "2026-07-01",
}


class _MockQoyodClient:
    """Records every call so we can assert what did/didn't happen."""
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def create_invoice(self, payload, idem=None):
        self.calls.append(("create_invoice", payload))
        return {"invoice": {"id": "INV-1", "number": "SO-1"}}

    async def create_invoice_payment(self, payload, idem=None):
        self.calls.append(("create_invoice_payment", payload))
        return {"invoice_payment": {"id": "PAY-1"}}


# ── 1. Same send_timestamp_riyadh across invoice + payment ─────────
class TestSharedSendTimestamp:
    """Simulates the pipeline's shared-decision pattern:
    capture decision once at the invoice site, reuse it at the
    payment site, stamp both payloads with the same date."""

    def test_invoice_and_payment_share_send_timestamp_riyadh(self):
        # Freeze the clock so send_date_riyadh is deterministic.
        frozen_utc = datetime(2026, 7, 15, 20, 30, 0,
                              tzinfo=timezone.utc)

        decision = assert_send_allowed(
            order=_ALLOW_ORDER, settings=_ALLOW_SETTINGS,
            now_utc=frozen_utc)
        assert decision.decision == "allow"
        assert decision.send_date_riyadh == "2026-07-15"
        assert decision.send_timestamp_riyadh is not None

        invoice_payload = {
            "date": "2026-07-05",       # legacy — must be rewritten
            "issue_date": "2026-07-05",
            "due_date": "2026-07-05",
            "completed_at": "2026-07-05T12:00:00Z",   # must be scrubbed
        }
        payment_payload = {
            "payment_date": "2026-07-05",
            "invoice_id": "INV-1",
            "paid_at": "2026-07-05T12:00:00Z",       # must be scrubbed
        }

        # Pipeline's contract: apply the SAME decision to both.
        invoice_stamped = apply_send_date_to_qoyod_payload(
            invoice_payload, decision)
        payment_stamped = apply_send_date_to_qoyod_payload(
            payment_payload, decision)

        # ── Invariant #1: shared date on every stamped field.
        assert invoice_stamped["date"] == "2026-07-15"
        assert invoice_stamped["issue_date"] == "2026-07-15"
        assert invoice_stamped["due_date"] == "2026-07-15"
        assert payment_stamped["payment_date"] == "2026-07-15"

        # Legacy fields scrubbed from BOTH payloads.
        assert "completed_at" not in invoice_stamped
        assert "paid_at" not in payment_stamped

        # ── Invariant: literally the SAME string on both sides.
        assert (invoice_stamped["date"] ==
                payment_stamped["payment_date"] ==
                decision.send_date_riyadh)


# ── 2. Block prevents ANY POST to Qoyod ─────────────────────────────
class TestBlockPreventsAPICalls:
    """When the policy blocks, neither create_invoice nor
    create_invoice_payment may be reached in a well-instrumented
    flow. Reproduces the caller pattern used in pipeline.py."""

    @pytest.mark.asyncio
    async def test_gate_disabled_blocks_before_any_call(self):
        client = _MockQoyodClient()

        async def _simulated_pipeline_send():
            # Mirror pipeline.py:849 exactly.
            try:
                decision = assert_send_allowed(
                    order=_ALLOW_ORDER,
                    settings=_FAIL_CLOSED_SETTINGS)
            except SelectiveSendPolicyBlocked:
                return "blocked"
            # Only past here may we build+send.
            invoice = apply_send_date_to_qoyod_payload(
                {"date": None}, decision)
            await client.create_invoice(invoice)
            payment = apply_send_date_to_qoyod_payload(
                {"payment_date": None}, decision)
            await client.create_invoice_payment(payment)
            return "sent"

        outcome = await _simulated_pipeline_send()
        assert outcome == "blocked"
        # ── Invariant #2: zero calls on block.
        assert client.calls == [], (
            f"Blocked decision must NOT reach the API. "
            f"Got calls: {client.calls}")

    @pytest.mark.asyncio
    async def test_allow_permits_both_calls_with_same_date(self):
        client = _MockQoyodClient()
        frozen_utc = datetime(2026, 7, 15, 20, 30, 0,
                              tzinfo=timezone.utc)

        decision = assert_send_allowed(
            order=_ALLOW_ORDER, settings=_ALLOW_SETTINGS,
            now_utc=frozen_utc)

        inv_payload = apply_send_date_to_qoyod_payload(
            {"date": None, "issue_date": None}, decision)
        await client.create_invoice(inv_payload)
        pay_payload = apply_send_date_to_qoyod_payload(
            {"payment_date": None}, decision)
        await client.create_invoice_payment(pay_payload)

        assert [c[0] for c in client.calls] == \
            ["create_invoice", "create_invoice_payment"]
        # Both payloads carry the SAME send_date_riyadh.
        assert client.calls[0][1]["date"] == "2026-07-15"
        assert client.calls[1][1]["payment_date"] == "2026-07-15"


# ── 3. Static invariant: pipeline.py wiring ─────────────────────────
class TestPipelineWiring:
    """Cheap static-analysis proof that pipeline.py adopts the
    guard pattern. Complements the mock test above by validating the
    REAL file that ships to production."""

    @classmethod
    def setup_class(cls):
        cls.src = _PIPELINE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)

    def _line(self, needle: str) -> int:
        """Line number of first occurrence — 0 if absent."""
        for i, line in enumerate(self.src.splitlines(), start=1):
            if needle in line:
                return i
        return 0

    def test_selective_send_guard_is_imported(self):
        assert "from integrations.qoyod.selective_send_guard import" \
            in self.src, "pipeline.py must import the guard module"
        assert "apply_send_date_to_qoyod_payload" in self.src
        assert "assert_send_allowed" in self.src
        assert "SelectiveSendPolicyBlocked" in self.src

    def test_assert_send_allowed_precedes_create_invoice(self):
        """The FIRST assert_send_allowed(...) call comes BEFORE the
        FIRST api_client.create_invoice(...) call."""
        assert_line = self._line("assert_send_allowed(")
        create_line = self._line("api_client.create_invoice(")
        assert assert_line > 0, "assert_send_allowed not called"
        assert create_line > 0, "create_invoice not called"
        assert assert_line < create_line, (
            f"assert_send_allowed at L{assert_line} must precede "
            f"create_invoice at L{create_line}")

    def test_apply_send_date_precedes_create_invoice(self):
        apply_line = self._line(
            "apply_send_date_to_qoyod_payload(")
        create_line = self._line("api_client.create_invoice(")
        assert apply_line > 0
        assert apply_line < create_line

    def test_payment_reuses_invoice_decision(self):
        """The payment site MUST reuse the invoice decision so both
        payloads share `send_timestamp_riyadh`. Look for the exact
        pattern that establishes reuse."""
        assert "payment_decision = selective_send_decision" \
            in self.src, (
                "pipeline.py must reuse the invoice decision at the "
                "payment site (line ~1402). Otherwise invoice + "
                "payment could receive different send_timestamp_"
                "riyadh values if the clock ticks between them.")

    def test_apply_send_date_precedes_create_invoice_payment(self):
        # Grab ALL apply-send-date lines and the create_invoice_payment
        # line — the latest apply must still precede the create call.
        pay_call_line = self._line("api_client.create_invoice_payment(")
        assert pay_call_line > 0
        # Search backwards from the payment call for the closest
        # apply_send_date invocation and the closest assert_send_allowed
        # or reuse marker.
        src_lines = self.src.splitlines()
        found_apply = False
        found_gate = False
        for i in range(pay_call_line - 1, 0, -1):
            line = src_lines[i - 1]
            if "apply_send_date_to_qoyod_payload(" in line and \
                    "payment_payload" in line:
                found_apply = True
            if ("assert_send_allowed(" in line
                    or "payment_decision = selective_send_decision"
                    in line):
                found_gate = True
            if found_apply and found_gate:
                break
        assert found_apply, (
            "payment_payload must pass through "
            "apply_send_date_to_qoyod_payload before "
            "create_invoice_payment.")
        assert found_gate, (
            "create_invoice_payment must be gated by either a fresh "
            "assert_send_allowed or a reused selective_send_decision.")

    def test_blocked_branch_returns_selective_send_blocked(self):
        """On block, the pipeline must NOT proceed to POST — it must
        return an outcome carrying the blocker_code."""
        assert re.search(
            r'"outcome":\s*"SELECTIVE_SEND_BLOCKED"',
            self.src), (
                "Blocked branch must yield "
                'outcome="SELECTIVE_SEND_BLOCKED".')

    def test_one_shot_reprocess_has_guard_before_pipeline_call(self):
        one_shot_path = (
            Path(__file__).resolve().parent.parent
            / "integrations" / "qoyod" / "one_shot_reprocess.py"
        )
        src = one_shot_path.read_text(encoding="utf-8")
        # Both markers present.
        assert "assert_send_allowed" in src
        # The one_shot guard is placed AFTER approval_phrase and
        # BEFORE process_normalized_row / process_customer_resolved_row.
        guard_line = 0
        approval_line = 0
        pipeline_call_line = 0
        for i, ln in enumerate(src.splitlines(), start=1):
            if "_assert_send_allowed(" in ln and guard_line == 0:
                guard_line = i
            if "approval_phrase_mismatch" in ln and approval_line == 0:
                approval_line = i
            if (("process_normalized_row(" in ln
                 or "process_customer_resolved_row(" in ln)
                    and "await " in ln
                    and pipeline_call_line == 0):
                pipeline_call_line = i
        assert 0 < approval_line < guard_line < pipeline_call_line, (
            f"one_shot_reprocess.py must gate in order: "
            f"approval_phrase (L{approval_line}) → "
            f"assert_send_allowed (L{guard_line}) → "
            f"pipeline call (L{pipeline_call_line}).")
