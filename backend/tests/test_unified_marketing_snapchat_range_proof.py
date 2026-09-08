from __future__ import annotations

from datetime import date

import pytest

import unified_marketing.readers.snapchat_v2 as reader
from unified_marketing.readers.snapchat_v2 import (
    _projection_financial_status,
    _reconciliation_status,
)
from snapchat_v2.routes import _daily_retry_dates


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length=None):
        return self.rows[:length]

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, length):
        self.rows = self.rows[:length]
        return self


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


class DailyCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, _query, _projection):
        return Cursor(self.rows)


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
async def test_entity_daily_series_uses_exact_v2_total_facts(monkeypatch):
    async def selected(*_args, **_kwargs):
        return {
            "ad_account_id": "snap-1",
            "display_name": "Snap Account",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        }

    async def cost(*_args, **_kwargs):
        return {"exchange_rate_to_sar": 3.75}

    monkeypatch.setattr(reader, "get_selected_account", selected)
    monkeypatch.setattr(reader, "calculate_cost_components", cost)
    db = Database({
        "mezan_snapchat_daily_total_facts_v2": DailyCollection([{
            "external_id": "campaign-1",
            "entity_type": "campaign",
            "report_date": "2026-08-24",
            "account_timezone": "America/Los_Angeles",
            "currency": "USD",
            "spend_native": 100.0,
            "impressions": 1000,
            "swipes": 50,
            "video_views": 500,
            "view_completion": 0.4,
            "view_content": 40,
            "add_to_cart": 10,
            "start_checkout": 8,
            "add_billing": 6,
            "purchases": 4,
            "purchase_value_native": 250.0,
            "coverage": {"status": "complete"},
        }])
    })

    result = await reader.load_snapchat_v2_entity_daily_series(
        db,
        "owner-1",
        entity_level="campaign",
        entity_ids=["campaign-1"],
        date_from=date(2026, 8, 24),
        date_to=date(2026, 8, 24),
        timezone_name="America/Los_Angeles",
    )

    assert result["contract_version"] == "unified-marketing-data-v1"
    assert result["entity_level"] == "campaign"
    assert result["source_fact_count"] == 1
    assert result["decision_eligibility"]["eligible"] is False
    row = result["rows"][0]
    assert row["delivery"]["spend_sar"]["amount"] == 375.0
    assert row["platform_outcomes"]["conversions"] == 4
    assert row["lineage"]["source_collection"] == (
        "mezan_snapchat_daily_total_facts_v2"
    )


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
            "source_fact_count": 1,
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
        return {"run-1": "complete"}

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
    monkeypatch.setattr(
        reader,
        "_projection_financial_run_statuses",
        complete_financial,
    )
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


@pytest.mark.asyncio
async def test_open_riyadh_day_exposes_observed_spend_as_provisional_only(
    monkeypatch,
):
    report_day = date(2026, 8, 26)

    async def selected(*_args, **_kwargs):
        return {
            "ad_account_id": "snap-1",
            "display_name": "Snap Account",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        }

    async def projections(*_args, **_kwargs):
        return [{
            "report_date": report_day.isoformat(),
            "base_spend_native": 42.16,
            "purchase_value_native": 100.0,
            "impressions": 500,
            "swipes": 20,
            "purchases": 2,
            "amount_complete": True,
            "data_state": "confirmed_data",
            "source_fact_count": 8,
            "source_sync_run_ids": ["run-open-day"],
            "hours": [{
                "sequence": 0,
                "local_hour": "00:00",
                "spend_native": 42.16,
                "status": "confirmed_data",
            }, {
                "sequence": 23,
                "local_hour": "23:00",
                "spend_native": None,
                "status": "future",
            }],
        }]

    async def complete_financial(*_args, **_kwargs):
        return {"run-open-day": "complete"}

    async def unreconciled(*_args, **_kwargs):
        return []

    async def cost(*_args, **_kwargs):
        native = float(_kwargs["spend_native"])
        return {
            "native_currency": "USD",
            "exchange_rate_to_sar": 3.75,
            "cost_setting_configured": True,
            "bank_commission_pct": 2.0,
            "apply_bank_commission": True,
            "base_spend_sar": round(native * 3.75, 2),
            "commission_sar": round(native * 3.75 * 0.02, 2),
            "final_cost_sar": round(native * 3.75 * 1.02, 2),
            "cost_coverage": {"complete": True},
        }

    monkeypatch.setattr(reader, "get_selected_account", selected)
    monkeypatch.setattr(reader, "list_daily_projections", projections)
    monkeypatch.setattr(
        reader,
        "_projection_financial_run_statuses",
        complete_financial,
    )
    monkeypatch.setattr(reader, "list_reconciliation", unreconciled)
    monkeypatch.setattr(reader, "calculate_cost_components", cost)
    monkeypatch.setattr(
        reader,
        "_today_in_timezone",
        lambda _timezone_name: date(2026, 8, 26),
    )

    open_result = await reader.load_snapchat_v2_dashboard_spend(
        object(),
        "owner-1",
        date_from=report_day,
        date_to=report_day,
        timezone_name="Asia/Riyadh",
    )

    assert open_result["total_sar"] == 158.1
    assert open_result["rows"][0]["effective_spend_sar"] == 158.1
    assert open_result["daily_state"][report_day.isoformat()] == "provisional_data"
    assert open_result["quality"]["status"] == "provisional"
    assert open_result["quality"]["amount_available"] is True
    assert open_result["quality"]["amount_complete"] is False
    assert open_result["quality"]["provisional"] is True
    assert open_result["quality"]["closed_reconciliation_status"] == (
        "not_required_open_day"
    )
    assert open_result["quality"]["reason_codes"] == [
        "open_riyadh_day_reconciliation_pending",
        "current_coverage_conflict",
    ]
    assert open_result["bank_commissions"]["total_fee_sar"] == 3.16

    report_day = date(2026, 8, 25)
    closed_result = await reader.load_snapchat_v2_dashboard_spend(
        object(),
        "owner-1",
        date_from=report_day,
        date_to=report_day,
        timezone_name="Asia/Riyadh",
    )

    assert closed_result["total_sar"] is None
    assert closed_result["rows"] == []
    assert closed_result["quality"]["status"] == "incomplete"
    assert closed_result["quality"]["amount_available"] is False
    assert closed_result["quality"]["provisional"] is False


def _range_projection(day: date) -> dict:
    return {
        "report_date": day.isoformat(),
        "base_spend_native": 1.0,
        "purchase_value_native": 2.0,
        "impressions": 10,
        "swipes": 1,
        "purchases": 1,
        "amount_complete": True,
        "data_state": "confirmed_data",
        "source_fact_count": 24,
        "source_sync_run_ids": ["range-run"],
        "hours": [],
    }


async def _closed_range_result(
    monkeypatch,
    *,
    days_present: int,
    financial_run_status: str = "complete",
    first_projection_overrides: dict | None = None,
):
    start = date(2026, 7, 1)
    end = date(2026, 7, 30)
    projections = [
        _range_projection(start.fromordinal(start.toordinal() + offset))
        for offset in range(days_present)
    ]
    if projections and first_projection_overrides:
        projections[0].update(first_projection_overrides)

    async def selected(*_args, **_kwargs):
        return {
            "ad_account_id": "snap-1",
            "display_name": "Snap Account",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        }

    async def projection_rows(*_args, **_kwargs):
        return projections

    async def financial_runs(*_args, **_kwargs):
        return {"range-run": financial_run_status}

    async def reconciled(*_args, **_kwargs):
        return [
            {"report_date": start.fromordinal(start.toordinal() + offset).isoformat(), "reconciled": True}
            for offset in range(30)
        ]

    async def cost(*_args, **kwargs):
        native = float(kwargs["spend_native"])
        return {
            "native_currency": "USD",
            "exchange_rate_to_sar": 3.75,
            "cost_setting_configured": True,
            "bank_commission_pct": 0.0,
            "apply_bank_commission": False,
            "base_spend_sar": round(native * 3.75, 2),
            "commission_sar": 0.0,
            "final_cost_sar": round(native * 3.75, 2),
            "cost_coverage": {"complete": True},
        }

    monkeypatch.setattr(reader, "get_selected_account", selected)
    monkeypatch.setattr(reader, "list_daily_projections", projection_rows)
    monkeypatch.setattr(reader, "_projection_financial_run_statuses", financial_runs)
    monkeypatch.setattr(reader, "list_reconciliation", reconciled)
    monkeypatch.setattr(reader, "calculate_cost_components", cost)
    monkeypatch.setattr(reader, "_today_in_timezone", lambda _name: date(2026, 8, 2))
    return await reader.load_snapchat_v2_dashboard_spend(
        object(),
        "owner-1",
        date_from=start,
        date_to=end,
        timezone_name="Asia/Riyadh",
    )


@pytest.mark.asyncio
async def test_dashboard_range_is_final_only_for_30_of_30_days(monkeypatch):
    result = await _closed_range_result(monkeypatch, days_present=30)

    assert result["total_sar"] == 112.5
    assert result["quality"]["amount_complete"] is True
    assert result["quality"]["requested_days"] == 30
    assert result["quality"]["complete_days"] == 30
    assert result["quality"]["missing_dates"] == []
    assert len(result["quality"]["daily_coverage"]) == 30


@pytest.mark.asyncio
async def test_dashboard_range_fails_closed_for_29_of_30_days(monkeypatch):
    result = await _closed_range_result(monkeypatch, days_present=29)

    assert result["total_sar"] is None
    assert result["rows"] == []
    assert result["quality"]["amount_complete"] is False
    assert result["quality"]["requested_days"] == 30
    assert result["quality"]["complete_days"] == 29
    assert result["quality"]["missing_dates"] == ["2026-07-30"]
    assert result["quality"]["provisional_subtotal_sar"] == 108.75
    missing = result["quality"]["daily_coverage"][-1]
    assert missing["date"] == "2026-07-30"
    assert missing["reason"] == "account_day_fact_missing"
    assert missing["usable"] is False


@pytest.mark.asyncio
async def test_ambiguous_run_proof_fails_closed_with_exact_reason(monkeypatch):
    result = await _closed_range_result(
        monkeypatch,
        days_present=30,
        financial_run_status="partial",
    )

    assert result["total_sar"] is None
    assert result["quality"]["amount_complete"] is False
    assert result["quality"]["complete_days"] == 0
    assert all(
        "run_proof_missing_or_ambiguous" in row["reasons"]
        for row in result["quality"]["daily_coverage"]
    )


@pytest.mark.asyncio
async def test_selected_account_without_daily_participation_fails_closed(monkeypatch):
    result = await _closed_range_result(
        monkeypatch,
        days_present=30,
        first_projection_overrides={
            "source_fact_count": 0,
            "coverage": {"status": "complete", "data_state": "confirmed_zero"},
        },
    )

    assert result["total_sar"] is None
    assert result["quality"]["amount_complete"] is False
    assert result["quality"]["complete_days"] == 29
    first = result["quality"]["daily_coverage"][0]
    assert first["participating_account_ids"] == []
    assert first["selected_account_ids"] == ["snap-1"]
    assert "selected_account_not_in_run" in first["reasons"]


@pytest.mark.asyncio
async def test_targeted_retry_selects_only_missing_or_unproven_days():
    start = date(2026, 7, 1)
    projections = [
        _range_projection(start.fromordinal(start.toordinal() + offset))
        for offset in range(3)
    ]
    reconciliations = [
        {"report_date": row["report_date"], "reconciled": True}
        for row in projections
    ]
    db = Database({
        "mezan_snapchat_sync_runs_v2": Collection([{
            "user_id": "owner-1",
            "ad_account_id": "snap-1",
            "sync_run_id": "range-run",
            "financial_sync_status": "complete",
        }])
    })

    assert await _daily_retry_dates(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        date_from=start,
        date_to=date(2026, 7, 3),
        projections=projections,
        reconciliations=reconciliations,
    ) == []

    missing = await _daily_retry_dates(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        date_from=start,
        date_to=date(2026, 7, 3),
        projections=projections[:1] + projections[2:],
        reconciliations=reconciliations,
    )
    assert missing == [date(2026, 7, 2)]

    recovered = await _daily_retry_dates(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        date_from=start,
        date_to=date(2026, 7, 3),
        projections=projections,
        reconciliations=reconciliations,
    )
    assert recovered == []

    db["mezan_snapchat_sync_runs_v2"].rows[0]["financial_sync_status"] = "partial"
    assert await _daily_retry_dates(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        date_from=start,
        date_to=date(2026, 7, 3),
        projections=projections,
        reconciliations=reconciliations,
    ) == [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
