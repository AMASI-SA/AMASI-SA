"""Iter-293.4-rev9 — invoice_issue_date_source = send_date (Asia/Riyadh).

Context (production order 269571122 — 2026-06-30 → 2026-07-01):
The order was completed by the customer on 2026-06-30 23:31 (UTC+3),
but manual per-order approval + one-shot-reprocess pushed the قيود
invoice out on 2026-07-01. Under the previous policy, the قيود
`issue_date` inherited Salla's `completed_at` (2026-06-30) — WRONG
for ZATCA: the issue date must reflect when the invoice was CREATED
in قيود.

Policy pinned by these tests:

    settings.invoice_date_source == "send_date"
        →  invoice_date = datetime.now() in Asia/Riyadh
        →  invoice_date_source recorded as "send_date"
        →  timezone label = "Asia/Riyadh"
        →  completed_at STILL surfaced separately as reference

    Legacy stored values "completed_at" / "trigger_status_date" /
    empty / None are auto-migrated to "send_date" on settings load.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.business_rules import (   # noqa: E402
    evaluate, QOYOD_ISSUE_DATE_TIMEZONE,
)
from integrations.qoyod.dto import SalesOrderDTO   # noqa: E402


try:
    from zoneinfo import ZoneInfo
    _RIYADH = ZoneInfo("Asia/Riyadh")
    _HAS_ZONEINFO = True
except Exception:   # pragma: no cover
    _RIYADH = timezone(timedelta(hours=3))
    _HAS_ZONEINFO = False


def _dto_269571122():
    """Mirror the production order — completed on 2026-06-30 23:31 (UTC+3)."""
    completed_at = datetime(2026, 6, 30, 23, 31, 26,
                            tzinfo=timezone(timedelta(hours=3)))
    return SalesOrderDTO(
        order_id="269571122",
        order_number="269571122",
        order_status="completed",
        order_status_native="completed",
        currency="SAR",
        subtotal=188.78,
        total_amount=213.78,
        tax_amount=27.78,
        shipping_amount=25.00,
        discount_amount=0.0,
        payment_method="cod",
        customer={"name": "T", "phone": "0500000000"},
        items=[{"sku": "AMS10002", "name": "ساعة", "quantity": 1,
                "unit_price": 179.00}],
        order_date=completed_at,
        completed_at=completed_at,
        paid_at=None,
    )


# ─────────────────────────────────────────────────────────────────────
class TestSendDatePolicy:

    def test_default_source_is_send_date(self):
        """Empty settings → source defaults to `send_date`."""
        decision = evaluate(_dto_269571122(), settings={})
        assert decision.eligible
        assert decision.invoice_date_source == "send_date"

    def test_send_date_returns_current_riyadh_datetime(self):
        """The resolved invoice_date must carry Asia/Riyadh tzinfo and
        be within the last few seconds (i.e. 'now')."""
        decision = evaluate(
            _dto_269571122(),
            settings={"invoice_date_source": "send_date",
                      "invoice_trigger_statuses": ["completed"]},
        )
        assert decision.invoice_date is not None
        # Must be recent — no more than 5 seconds ago.
        now_riyadh = datetime.now(_RIYADH)
        drift = abs((now_riyadh - decision.invoice_date).total_seconds())
        assert drift < 5, (
            f"send_date must be current 'now'; drift={drift:.1f}s")
        # Timezone MUST be Asia/Riyadh (not UTC, not Salla-side offset).
        tz_name = str(decision.invoice_date.tzinfo)
        assert "Riyadh" in tz_name or "+03:00" in tz_name, (
            f"expected Asia/Riyadh tzinfo; got {tz_name}")

    def test_send_date_ignores_stale_completed_at(self):
        """The critical bug — a same-day resend of an old order MUST
        stamp today's Riyadh date, NOT the yesterday completed_at."""
        dto = _dto_269571122()
        # DTO completed_at is 2026-06-30. We're running "now" (2026-07-01+).
        decision = evaluate(
            dto,
            settings={"invoice_date_source": "send_date",
                      "invoice_trigger_statuses": ["completed"]},
        )
        # The resolved invoice_date is TODAY (Riyadh), NOT 2026-06-30.
        assert decision.invoice_date is not None
        today_riyadh = datetime.now(_RIYADH).date()
        # completed_at.date() = 2026-06-30; if the bug were still there
        # this assertion would fail because invoice_date would equal
        # dto.completed_at.
        assert decision.invoice_date.date() == today_riyadh
        # And completed_at is still surfaced separately for reference.
        assert decision.completed_at is not None
        # NOTE: on the extremely rare case where the test runs on
        # 2026-06-30 in Riyadh, both dates would coincide. That's
        # fine — the assertion still succeeds because the SOURCE is
        # `send_date`, not `completed_at`, which is what the policy
        # cares about.

    def test_completed_at_source_still_available_explicitly(self):
        """When the operator EXPLICITLY sets `completed_at`, we honour
        it (no auto-migration at the business-rules layer). Only the
        settings loader silently upgrades stored `completed_at` →
        `send_date`; direct callers see the raw setting."""
        decision = evaluate(
            _dto_269571122(),
            settings={"invoice_date_source": "completed_at",
                      "invoice_trigger_statuses": ["completed"]},
        )
        assert decision.invoice_date_source == "completed_at"
        # And the resolved date IS the DTO's completed_at.
        assert decision.invoice_date is not None
        assert decision.invoice_date.date() == datetime(
            2026, 6, 30).date()

    def test_completed_at_diagnostic_always_present(self):
        """The `completed_at` diagnostic field on RulesDecision must
        be populated regardless of which source was chosen."""
        for src in ("send_date", "trigger_status_date", "completed_at",
                    "paid_at"):
            d = evaluate(
                _dto_269571122(),
                settings={"invoice_date_source": src,
                          "invoice_trigger_statuses": ["completed"]},
            )
            assert d.completed_at is not None, (
                f"completed_at diagnostic missing for source={src}")

    def test_timezone_label_only_for_send_date(self):
        """`invoice_issue_date_timezone` diagnostic is set only when
        the send_date path fires — for Salla-side timestamps it's
        None (they carry their own tzinfo in the DTO)."""
        d_send = evaluate(
            _dto_269571122(),
            settings={"invoice_date_source": "send_date"},
        )
        assert d_send.invoice_issue_date_timezone == "Asia/Riyadh"

        d_completed = evaluate(
            _dto_269571122(),
            settings={"invoice_date_source": "completed_at",
                      "invoice_trigger_statuses": ["completed"]},
        )
        assert d_completed.invoice_issue_date_timezone is None

    def test_to_log_dict_carries_diagnostics(self):
        d = evaluate(
            _dto_269571122(),
            settings={"invoice_date_source": "send_date"},
        )
        log = d.to_log_dict()
        assert log["invoice_issue_date_source"] == "send_date"
        assert log["invoice_issue_date_timezone"] == "Asia/Riyadh"
        assert log["completed_at"] is not None
        assert log["invoice_date"] is not None


# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestPipelineSettingsMigration:
    """The pipeline's `_load_settings` silently rewrites legacy values.
    This closes the door on stale docs surviving into production."""

    async def test_completed_at_stored_gets_upgraded(self):
        class _Coll:
            def __init__(self, doc):
                self.doc = doc
            async def find_one(self, q, projection=None):
                return dict(self.doc) if self.doc else None

        class _DB:
            def __init__(self, doc):
                self.qoyod_settings = _Coll(doc)

        from integrations.qoyod.pipeline import _load_settings
        db = _DB({"user_id": "main",
                  "invoice_trigger_statuses": ["completed"],
                  "invoice_date_source": "completed_at",
                  "trigger_once_only":   True,
                  "dry_run_mode":        False})
        settings = await _load_settings(db, "main")
        assert settings["invoice_date_source"] == "send_date"

    async def test_trigger_status_date_stored_gets_upgraded(self):
        class _Coll:
            def __init__(self, doc):
                self.doc = doc
            async def find_one(self, q, projection=None):
                return dict(self.doc) if self.doc else None

        class _DB:
            def __init__(self, doc):
                self.qoyod_settings = _Coll(doc)

        from integrations.qoyod.pipeline import _load_settings
        db = _DB({"user_id": "main",
                  "invoice_trigger_statuses": ["completed"],
                  "invoice_date_source": "trigger_status_date"})
        settings = await _load_settings(db, "main")
        assert settings["invoice_date_source"] == "send_date"

    async def test_missing_source_defaults_to_send_date(self):
        class _Coll:
            async def find_one(self, q, projection=None):
                return None    # empty DB — first read

        class _DB:
            def __init__(self):
                self.qoyod_settings = _Coll()

        from integrations.qoyod.pipeline import _load_settings
        settings = await _load_settings(_DB(), "main")
        assert settings["invoice_date_source"] == "send_date"

    async def test_explicit_paid_at_not_overwritten(self):
        """Only Salla-side event timestamps are migrated. `paid_at`
        is a distinct explicit choice — leave it alone."""
        class _Coll:
            def __init__(self, doc):
                self.doc = doc
            async def find_one(self, q, projection=None):
                return dict(self.doc)

        class _DB:
            def __init__(self, doc):
                self.qoyod_settings = _Coll(doc)

        from integrations.qoyod.pipeline import _load_settings
        db = _DB({"user_id": "main",
                  "invoice_trigger_statuses": ["completed"],
                  "invoice_date_source": "paid_at"})
        settings = await _load_settings(db, "main")
        assert settings["invoice_date_source"] == "paid_at"


# ─────────────────────────────────────────────────────────────────────
class TestTimezoneConstant:

    def test_riyadh_zoneinfo_name(self):
        assert QOYOD_ISSUE_DATE_TIMEZONE == "Asia/Riyadh"
