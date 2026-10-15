from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter

import snapchat_v2.routes as snapchat_routes
from snapchat_v2.headline import (
    resolve_open_day_headline_spend,
    resolve_report_headline_spend,
)

GENERATED_AT = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def _projection(
    spend: float,
    *,
    report_date: str = "2026-08-28",
    hour_status: str = "provisional_unavailable",
) -> dict:
    return {
        "report_date": report_date,
        "projection_timezone": "America/Los_Angeles",
        "account_timezone": "America/Los_Angeles",
        "action_report_time": "conversion",
        "base_spend_native": spend,
        # The current implementation may mark amount_complete even while the
        # open hour is unavailable.  Headline selection must not turn that into
        # a complete hourly-breakdown claim.
        "amount_complete": True,
        "hours": [
            {"local_hour": "10:00", "status": "confirmed_data", "spend_native": spend},
            {"local_hour": "11:00", "status": hour_status, "spend_native": None},
        ],
        "sync_run_id": "run-current",
        "source_sync_run_ids": ["run-current"],
        "generated_at": GENERATED_AT,
    }


def _reconciliation(
    total: float,
    *,
    granularity: str = "TOTAL",
    fallback_from: str | None = None,
) -> dict:
    coverage = {
        "status": "complete",
        "data_state": "confirmed_data" if total > 0 else "confirmed_zero",
        "provider_granularity": granularity,
    }
    if fallback_from:
        coverage["fallback_from"] = fallback_from
    return {
        "report_date": "2026-08-28",
        "account_timezone": "America/Los_Angeles",
        "action_report_time": "conversion",
        "provider_spend_native": total,
        "provider_riyadh_window_spend_native": total,
        "provider_coverage": coverage,
        "dashboard_provider_coverage": dict(coverage),
        "dashboard_timezone": "Asia/Riyadh",
        "sync_run_id": "run-current",
        "checked_at": GENERATED_AT + timedelta(seconds=5),
    }


def test_provider_total_is_headline_while_hourly_breakdown_remains_unchanged():
    projection = _projection(1983.68)
    original = deepcopy(projection)

    result = resolve_open_day_headline_spend(
        projection=projection,
        reconciliation=_reconciliation(2152.99),
        open_day=True,
    )

    assert result["headline_spend_native"] == 2152.99
    assert result["hourly_spend_native"] == 1983.68
    assert result["unallocated_spend_native"] == 169.31
    assert result["headline_spend_source"] == "provider_total"
    assert result["hourly_breakdown_status"] == "incomplete"
    assert result["hourly_breakdown_complete"] is False
    assert projection == original
    assert projection["hours"][1]["status"] == "provisional_unavailable"
    assert projection["hours"][1]["spend_native"] is None


def test_regression_provider_1698_09_does_not_fall_back_to_hourly_1393_10():
    result = resolve_open_day_headline_spend(
        projection=_projection(1393.10),
        reconciliation=_reconciliation(1698.09),
        open_day=True,
    )

    assert result["headline_spend_native"] == 1698.09
    assert result["hourly_spend_native"] == 1393.10
    assert result["unallocated_spend_native"] == 304.99


def test_provider_total_can_reconcile_down_without_mutating_hourly_facts():
    result = resolve_open_day_headline_spend(
        projection=_projection(925.0),
        reconciliation=_reconciliation(905.0),
        open_day=True,
    )

    assert result["headline_spend_native"] == 905.0
    assert result["hourly_spend_native"] == 925.0
    assert result["unallocated_spend_native"] == -20.0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row["provider_coverage"].update(status="incomplete"),
        lambda row: row.update(sync_run_id="run-stale"),
        lambda row: row.update(checked_at=GENERATED_AT - timedelta(seconds=1)),
        lambda row: row.update(report_date="2026-08-27"),
        lambda row: row.update(action_report_time="impression"),
        lambda row: row.update(account_timezone="Asia/Riyadh"),
    ],
)
def test_untrusted_or_stale_provider_evidence_never_overrides_hourly(mutate):
    reconciliation = _reconciliation(2152.99)
    mutate(reconciliation)

    result = resolve_open_day_headline_spend(
        projection=_projection(1983.68),
        reconciliation=reconciliation,
        open_day=True,
    )

    assert result["headline_spend_native"] == 1983.68
    assert result["headline_spend_source"] == "hourly_facts"
    assert result["unallocated_spend_native"] == 0.0


@pytest.mark.parametrize("run_ids", [[], ["run-current", "run-stale"]])
def test_provider_total_requires_exact_projection_run_proof(run_ids):
    projection = _projection(1983.68)
    projection["source_sync_run_ids"] = run_ids
    projection["sync_run_id"] = None

    result = resolve_open_day_headline_spend(
        projection=projection,
        reconciliation=_reconciliation(2152.99),
        open_day=True,
    )

    assert result["headline_spend_native"] == 1983.68
    assert result["headline_spend_source"] == "hourly_facts"


def test_projection_run_id_proves_zero_fact_projection_without_source_rows():
    projection = _projection(0.0)
    projection["source_sync_run_ids"] = []

    result = resolve_open_day_headline_spend(
        projection=projection,
        reconciliation=_reconciliation(2152.99),
        open_day=True,
    )

    assert result["headline_spend_native"] == 2152.99
    assert result["hourly_spend_native"] == 0.0
    assert result["unallocated_spend_native"] == 2152.99


def test_only_explicit_total_http_400_hour_fallback_is_trusted():
    trusted = resolve_open_day_headline_spend(
        projection=_projection(1983.68),
        reconciliation=_reconciliation(
            2152.99,
            granularity="HOUR",
            fallback_from="snapchat_provider_http_400",
        ),
        open_day=True,
    )
    unproven = resolve_open_day_headline_spend(
        projection=_projection(1983.68),
        reconciliation=_reconciliation(2152.99, granularity="HOUR"),
        open_day=True,
    )

    assert trusted["headline_spend_native"] == 2152.99
    assert trusted["headline_spend_source"] == "provider_total"
    assert unproven["headline_spend_native"] == 1983.68
    assert unproven["headline_spend_source"] == "hourly_facts"


def test_riyadh_projection_uses_only_the_riyadh_provider_window():
    projection = _projection(1983.68)
    projection["projection_timezone"] = "Asia/Riyadh"
    reconciliation = _reconciliation(2152.99)
    reconciliation["provider_spend_native"] = 9999.0

    result = resolve_open_day_headline_spend(
        projection=projection,
        reconciliation=reconciliation,
        open_day=True,
        provider_scope="dashboard",
    )

    assert result["headline_spend_native"] == 2152.99
    assert result["unallocated_spend_native"] == 169.31


def test_nonzero_gap_never_claims_a_complete_hourly_breakdown():
    projection = _projection(1983.68, hour_status="confirmed_data")

    result = resolve_open_day_headline_spend(
        projection=projection,
        reconciliation=_reconciliation(2152.99),
        open_day=True,
    )

    assert result["hourly_breakdown_status"] == "incomplete"
    assert result["hourly_breakdown_complete"] is False


def test_closed_days_keep_existing_hourly_behavior():
    result = resolve_open_day_headline_spend(
        projection=_projection(
            1983.68,
            report_date="2026-08-27",
            hour_status="confirmed_data",
        ),
        reconciliation={**_reconciliation(2152.99), "report_date": "2026-08-27"},
        open_day=False,
    )

    assert result["headline_spend_native"] == 1983.68
    assert result["headline_spend_source"] == "hourly_facts"
    assert result["unallocated_spend_native"] == 0.0


def test_range_keeps_closed_day_hourly_and_uses_total_for_open_day_only():
    closed = _projection(
        1000.0,
        report_date="2026-08-27",
        hour_status="confirmed_data",
    )
    open_day = _projection(1983.68)
    reconciliation = _reconciliation(2152.99)

    result = resolve_report_headline_spend(
        projections=[closed, open_day],
        reconciliations=[reconciliation],
        open_report_date="2026-08-28",
    )

    assert result["headline_spend_native"] == 3152.99
    assert result["hourly_spend_native"] == 2983.68
    assert result["unallocated_spend_native"] == 169.31
    assert result["headline_spend_source"] == "mixed"
    assert result["hourly_breakdown_complete"] is False


@pytest.mark.asyncio
async def test_report_route_publishes_provider_headline_and_keeps_hourly_endpoint(
    monkeypatch,
):
    projection = _projection(1983.68)
    reconciliation = _reconciliation(2152.99)
    account = {
        "ad_account_id": "account-1",
        "timezone": "America/Los_Angeles",
        "currency": "USD",
    }

    async def selected_account(_db, _user_id):
        return account

    async def daily_projections(_db, **_kwargs):
        return [deepcopy(projection)]

    async def reconciliations(_db, **_kwargs):
        return [deepcopy(reconciliation)]

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls(2026, 8, 28, 18, tzinfo=timezone.utc)
            return (
                current.astimezone(tz)
                if tz is not None
                else current.replace(tzinfo=None)
            )

    monkeypatch.setattr(snapchat_routes, "datetime", FrozenDateTime)
    monkeypatch.setattr(snapchat_routes, "get_selected_account", selected_account)
    monkeypatch.setattr(
        snapchat_routes,
        "list_daily_projections",
        daily_projections,
    )
    monkeypatch.setattr(
        snapchat_routes,
        "list_reconciliation",
        reconciliations,
    )

    router = APIRouter()
    snapchat_routes.attach_snapchat_v2_routes(
        router,
        object(),
        lambda: {"id": "u1"},
        lambda user: user,
    )
    report_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.name == "snapchat_v2_report_route"
    )
    hourly_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.name == "snapchat_v2_hourly_route"
    )

    report = await report_endpoint(
        date_from=datetime(2026, 8, 28).date(),
        date_to=datetime(2026, 8, 28).date(),
        timezone="account",
        action_report_time="conversion",
        user={"id": "u1"},
    )
    hourly = await hourly_endpoint(
        report_date=datetime(2026, 8, 28).date(),
        timezone="account",
        action_report_time="conversion",
        user={"id": "u1"},
    )

    assert report["base_spend_native"] == 1983.68
    assert report["headline_spend_native"] == 2152.99
    assert report["hourly_spend_native"] == 1983.68
    assert report["unallocated_spend_native"] == 169.31
    assert report["headline_spend_source"] == "provider_total"
    assert report["days"][0]["hours"] == projection["hours"]
    assert hourly["base_spend_native"] == 1983.68
    assert hourly["hours"] == projection["hours"]
