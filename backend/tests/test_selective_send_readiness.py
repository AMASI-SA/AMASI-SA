"""Iter-001k+ — Read-only readiness diagnostic on the policy report.

Purpose
────────
The Selective Send policy report should show, for each order, the
FULL list of substantive blockers that would still refuse the send
even if the master gate and write lock were flipped open. This is
purely diagnostic — the effective `blocker_code` and the gate flags
are unchanged.

Contract
────────
- `readiness_blockers` is emitted on EVERY decision, even when the
  effective blocker_code is `gate_disabled`.
- `readiness_ready_if_gate_opened` is True iff `readiness_blockers`
  is empty.
- Zero Qoyod API calls (report is Read-Only).
- Zero DB writes (report is Read-Only).
- `gates_snapshot` is untouched by this diagnostic.
"""
from __future__ import annotations

import sys
from typing import Any, Optional
from unittest.mock import patch

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.selective_send_policy import (   # noqa: E402
    _compute_readiness_blockers,
    build_selective_send_policy_report,
    should_allow_selective_live_send,
)


_FAIL_CLOSED_SETTINGS = {
    "selective_live_send_enabled": False,
    "production_writes_locked":    True,
    "qoyod_sync_start_date":       "2026-07-01",
    "qoyod_tax_period":            "Q3-2026",
    "bank_transfer_routing_enabled": False,
    "qoyod_invoice_date_source":   "send_date",
    "qoyod_enabled_invoice_trigger_statuses":
        ["completed", "تم التنفيذ"],
}


def _base_order(**overrides: Any) -> dict:
    order = {
        "order_number": "N-1",
        "salla_order_id": "SO-1",
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
    order.update(overrides)
    return order


# ── Pure inspector ──────────────────────────────────────────────────
class TestComputeReadinessBlockers:

    def test_clean_order_has_no_readiness_blockers(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(), settings=_FAIL_CLOSED_SETTINGS)
        assert blockers == []

    def test_dry_invoice_id_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(existing_qoyod_invoice_id="DRY:INV-1"),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "dry_invoice_id" in blockers

    def test_preview_id_on_invoice_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                existing_qoyod_invoice_id="PREVIEW:INV-1"),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "preview_id_detected" in blockers

    def test_existing_real_invoice_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(existing_qoyod_invoice_id=1234567),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "existing_real_qoyod_invoice_id" in blockers

    def test_dry_customer_id_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                customer_status={"resolved": True,
                                 "qoyod_id": "DRY:CUST-1",
                                 "reason": None}),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "dry_customer_id" in blockers

    def test_null_contact_id_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                customer_status={"resolved": False,
                                 "qoyod_id": None,
                                 "reason": "unmapped"}),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "null_contact_id" in blockers

    def test_null_product_id_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                products_status={"resolved": False,
                                 "resolved_count": 0,
                                 "dry_run_only": 0,
                                 "missing": ["AMS11961"]}),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "null_product_id" in blockers

    def test_dry_product_id_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                products_status={"resolved": True,
                                 "resolved_count": 1,
                                 "dry_run_only": 1,
                                 "missing": []}),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "dry_product_id" in blockers

    def test_totals_mismatch_gt_001_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                totals_status={"valid": False, "total": 100.05,
                               "expected": 100.0, "diff": 0.05}),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "totals_mismatch_gt_0_01" in blockers

    def test_totals_within_001_is_not_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                totals_status={"valid": True, "total": 100.005,
                               "expected": 100.0, "diff": 0.005}),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "totals_mismatch_gt_0_01" not in blockers

    def test_bank_transfer_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(payment_method="bank_transfer"),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "bank_transfer_on_hold_iter_294" in blockers

    def test_q2_cutoff_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(salla_order_created_at="2026-06-30"),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "q2_cutoff" in blockers

    def test_missing_order_created_at_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(salla_order_created_at=None),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "missing_order_created_at" in blockers

    def test_trigger_status_disabled_is_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(status="delivered"),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "invoice_trigger_status_not_enabled" in blockers

    def test_gate_disabled_is_NEVER_in_readiness_blockers(self):
        """`gate_disabled` is the operator-toggled blocker — it must
        NOT appear in the readiness list (it's the whole point of
        the diagnostic — what remains BESIDES the gate)."""
        blockers = _compute_readiness_blockers(
            order=_base_order(),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "gate_disabled" not in blockers
        assert "write_lock_active" not in blockers

    def test_multiple_blockers_are_all_flagged(self):
        blockers = _compute_readiness_blockers(
            order=_base_order(
                payment_method="bank_transfer",
                existing_qoyod_invoice_id="DRY:INV-2",
                totals_status={"valid": False, "total": 105.0,
                               "expected": 100.0, "diff": 5.0}),
            settings=_FAIL_CLOSED_SETTINGS)
        assert "bank_transfer_on_hold_iter_294" in blockers
        assert "dry_invoice_id" in blockers
        assert "totals_mismatch_gt_0_01" in blockers
        # Insertion order is stable & deduped.
        assert len(blockers) == len(set(blockers))


# ── Report-level integration ────────────────────────────────────────
class _FakeSettingsColl:
    def __init__(self, settings):
        self._s = dict(settings, user_id="main")

    async def find_one(self, q, projection=None):
        # Match by user_id only.
        if q.get("user_id") == self._s["user_id"]:
            return dict(self._s)
        return None


class _FakeDB:
    def __init__(self, settings):
        self.qoyod_settings = _FakeSettingsColl(settings)


@pytest.mark.asyncio
class TestReportEmitsReadiness:
    """`build_selective_send_policy_report` decorates every decision
    row with `readiness_blockers` and `readiness_ready_if_gate_opened`
    regardless of the effective blocker_code."""

    async def _run_report(self, *, items: list[dict],
                          settings: dict = _FAIL_CLOSED_SETTINGS
                          ) -> dict:
        db = _FakeDB(settings)
        with patch(
            "integrations.qoyod.selective_send_policy."
            "build_eligible_orders_report"
        ) as mock_build:
            async def _fake(*a, **kw):
                return {
                    "items": items,
                    "total_scanned": len(items),
                    "total_classified": len(items),
                    "excluded_status_count": 0,
                    "excluded_before_sync_start_date_count": 0,
                    "excluded_missing_order_created_at_count": 0,
                    "source_mode": "test",
                }
            mock_build.side_effect = _fake
            return await build_selective_send_policy_report(
                db, user_id="main", since_days=90, limit=10)

    async def test_gate_disabled_row_still_shows_readiness_blockers(self):
        """Even when effective blocker_code=gate_disabled, the
        readiness_blockers list must NOT be hidden."""
        dirty = _base_order(
            payment_method="bank_transfer",
            existing_qoyod_invoice_id="DRY:INV-1")
        report = await self._run_report(items=[dirty])
        assert report["counts"] == {"allow": 0, "block": 1}
        d = report["decisions"][0]
        # Effective blocker is still gate_disabled (unchanged).
        assert d["blocker_code"] == "gate_disabled"
        # But readiness surfaces the substantive blockers underneath.
        assert "bank_transfer_on_hold_iter_294" \
            in d["readiness_blockers"]
        assert "dry_invoice_id" in d["readiness_blockers"]
        assert d["readiness_ready_if_gate_opened"] is False

    async def test_ready_row_is_marked_ready_if_gate_opened(self):
        clean = _base_order()
        report = await self._run_report(items=[clean])
        d = report["decisions"][0]
        assert d["blocker_code"] == "gate_disabled"  # still gated
        assert d["readiness_blockers"] == []
        assert d["readiness_ready_if_gate_opened"] is True

    async def test_gates_snapshot_is_unchanged(self):
        report = await self._run_report(items=[_base_order()])
        gates = report["gates_snapshot"]
        # Gates remain closed. The readiness diagnostic did NOT flip
        # anything.
        assert gates["selective_live_send_enabled"] is False
        assert gates["production_writes_locked"] is True

    async def test_readiness_rollup_counts_present(self):
        items = [
            _base_order(payment_method="bank_transfer"),
            _base_order(existing_qoyod_invoice_id="DRY:INV-9"),
            _base_order(),   # clean
        ]
        report = await self._run_report(items=items)
        assert report["readiness_ready_if_gate_opened_count"] == 1
        codes = report["readiness_blocker_code_counts"]
        assert codes.get("bank_transfer_on_hold_iter_294") == 1
        assert codes.get("dry_invoice_id") == 1

    async def test_no_qoyod_api_call_from_report(self):
        """Report must never touch the Qoyod HTTP client."""
        with patch(
            "integrations.qoyod.api_client.QoyodAPIClient"
        ) as mock_client_cls:
            report = await self._run_report(items=[_base_order()])
            assert mock_client_cls.call_count == 0
        assert report["total_decisions"] == 1

    async def test_effective_blocker_code_is_unchanged_by_diagnostic(
            self):
        """Ensure the readiness inspector never overwrites the
        effective decision fields."""
        dirty = _base_order(payment_method="bank_transfer")
        report = await self._run_report(items=[dirty])
        d = report["decisions"][0]
        # Direct policy result — same code the pipeline would surface.
        direct = should_allow_selective_live_send(
            order=dirty, settings=_FAIL_CLOSED_SETTINGS)
        assert d["blocker_code"] == direct.blocker_code
        assert d["decision"] == direct.decision
