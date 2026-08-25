from __future__ import annotations

from datetime import date

import pytest

import unified_marketing.readers.snapchat_v2 as reader
from unified_marketing.readers.snapchat_v2 import (
    _projection_financial_status,
    _reconciliation_status,
)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length=None):
        return self.rows[:length]


class Collection:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, query, _projection):
        ids = set(query["sync_run_id"]["$in"])
        return Cursor([
            row
            for row in self.rows
            if row.get("sync_run_id") in ids
            and row.get("user_id") == query["user_id"]
            and row.get("ad_account_id") == query["ad_account_id"]
        ])


class Database(dict):
    pass


@pytest.mark.asyncio
async def test_range_financial_proof_ignores_newer_unrelated_partial_run():
    db = Database({
        "mezan_snapchat_sync_runs_v2": Collection([
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "closed-day-run",
                "financial_sync_status": "complete",
            },
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "new-open-day-run",
                "financial_sync_status": "partial",
            },
        ])
    })

    status = await _projection_financial_status(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        projections=[{"source_sync_run_ids": ["closed-day-run"]}],
    )

    assert status == "complete"


@pytest.mark.asyncio
async def test_range_financial_proof_fails_closed_when_a_source_run_is_not_complete():
    db = Database({
        "mezan_snapchat_sync_runs_v2": Collection([
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "run-a",
                "financial_sync_status": "complete",
            },
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "run-b",
                "financial_sync_status": "partial",
            },
        ])
    })

    status = await _projection_financial_status(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        projections=[{"source_sync_run_ids": ["run-a", "run-b"]}],
    )

    assert status == "partial"


def test_reconciliation_proof_requires_every_day_in_the_range():
    assert _reconciliation_status(
        [
            {"report_date": "2026-08-23", "reconciled": True},
            {"report_date": "2026-08-24", "reconciled": True},
        ],
        date_from=date(2026, 8, 23),
        date_to=date(2026, 8, 24),
    ) == "reconciled"

    assert _reconciliation_status(
        [{"report_date": "2026-08-23", "reconciled": True}],
        date_from=date(2026, 8, 23),
        date_to=date(2026, 8, 24),
    ) == "partial"


@pytest.mark.asyncio
async def test_dashboard_snapshot_uses_v2_projection_hours_and_cost_settings(
    monkeypatch,
):
    async def selected(*_args, **_kwargs):
        return {
            "ad_account_id": "snap-1",
            "display_name": "Snap Account",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        }

    async def projections(*_args, **_kwargs):
        return [{
            "report_date": "2026-08-24",
            "base_spend_native": 100.0,
            "purchase_value_native": 200.0,
            "impressions": 1000,
            "swipes": 100,
            "purchases": 4,
            "amount_complete": True,
            "data_state": "confirmed_data",
            "source_sync_run_ids": ["run-1"],
            "hours": [
                {
                    "sequence": 0,
                    "local_hour": "00:00",
                    "spend_native": 10.0,
                    "status": "confirmed",
                },
                {
                    "sequence": 23,
                    "local_hour": "23:00",
                    "spend_native": None,
                    "status": "future",
                },
            ],
        }]

    async def complete_financial(*_args, **_kwargs):
        return "complete"

    async def reconciled(*_args, **_kwargs):
        return [{"report_date": "2026-08-24", "reconciled": True}]

    async def cost(*_args, **_kwargs):
        return {
            "native_currency": "USD",
            "exchange_rate_to_sar": 3.7544,
            "cost_setting_configured": True,
            "bank_commission_pct": 2.3,
            "apply_bank_commission": True,
            "base_spend_sar": 375.44,
            "commission_sar": 8.64,
            "final_cost_sar": 384.08,
            "cost_coverage": {"complete": True},
        }

    monkeypatch.setattr(reader, "get_selected_account", selected)
    monkeypatch.setattr(reader, "list_daily_projections", projections)
    monkeypatch.setattr(reader, "_projection_financial_status", complete_financial)
    monkeypatch.setattr(reader, "list_reconciliation", reconciled)
    monkeypatch.setattr(reader, "calculate_cost_components", cost)

    result = await reader.load_snapchat_v2_dashboard_spend(
        object(),
        "owner-1",
        date_from=date(2026, 8, 24),
        date_to=date(2026, 8, 24),
        timezone_name="Asia/Riyadh",
    )

    assert result["total_sar"] == 375.44
    assert result["quality"]["amount_complete"] is True
    assert result["quality"]["reconciliation_status"] == "reconciled"
    assert result["rows"][0]["purchase_value_sar"] == 750.88
    assert result["hourly_sar"]["2026-08-24"][0]["spend_sar"] == 37.54
    assert result["hourly_sar"]["2026-08-24"][1]["spend_sar"] is None
    assert result["hourly_sar"]["2026-08-24"][1]["status"] == "future"
    assert result["bank_commissions"]["total_fee_sar"] == 8.64
    assert result["provider_write_reached"] is False
    assert result["accounting_write_reached"] is False
