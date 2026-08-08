from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from integrations_control_center import snapchat_adsquad_performance as module
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
)


def _matches(row, query):
    for key, condition in query.items():
        value = row.get(key)
        if isinstance(condition, dict):
            for operator, expected in condition.items():
                if operator == "$in" and value not in expected:
                    return False
                if operator == "$gte" and not (value is not None and value >= expected):
                    return False
                if operator == "$lte" and not (value is not None and value <= expected):
                    return False
        elif value != condition:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))


class FakeDB:
    def __init__(self, collections):
        self.collections = deepcopy(collections)

    def __getitem__(self, name):
        return FakeCollection(self.collections.setdefault(name, []))

    def __getattr__(self, name):
        return self[name]


def test_extract_adsquad_total_rows_preserves_parent_campaign():
    request_start = datetime(2026, 8, 4, tzinfo=timezone.utc)
    request_end = datetime(2026, 8, 5, tzinfo=timezone.utc)
    payload = {
        "total_stats": [
            {
                "sub_request_status": "SUCCESS",
                "total_stat": {
                    "start_time": "2026-08-04T00:00:00+00:00",
                    "end_time": "2026-08-05T00:00:00+00:00",
                    "breakdown_stats": {
                        "adsquad": [
                            {
                                "id": "squad-1",
                                "stats": {
                                    "spend": 5_000_000,
                                    "impressions": 1000,
                                    "swipes": 50,
                                    "conversion_purchases": 2,
                                    "conversion_purchases_value": 10_000_000,
                                },
                            }
                        ]
                    },
                },
            }
        ]
    }

    rows, errors, successful, breakdown_seen = (
        module.extract_adsquad_total_rows(
            payload,
            campaign_id="campaign-1",
            request_start=request_start,
            request_end=request_end,
        )
    )

    assert module.ADSQUAD_PROVIDER_GRANULARITY == "TOTAL"
    assert module.adsquad_source_mode("conversion").endswith(
        "ad_squad_active_campaign_account_day_bounded_v6"
    )
    assert successful == 1
    assert breakdown_seen is True
    assert errors == []
    assert rows == [
        {
            "campaign_id": "campaign-1",
            "ad_squad_id": "squad-1",
            "start_time": "2026-08-04T00:00:00+00:00",
            "end_time": "2026-08-05T00:00:00+00:00",
            "metrics": {
                "spend": 5_000_000,
                "impressions": 1000,
                "swipes": 50,
                "conversion_purchases": 2,
                "conversion_purchases_value": 10_000_000,
            },
        }
    ]


@pytest.mark.asyncio
async def test_campaign_entities_selects_active_rows_before_legacy_limit():
    historical = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "ad_account_id": "account-1",
            "entity_type": "campaign",
            "external_id": f"paused-{index:03d}",
            "display_name": f"Paused {index:03d}",
            "status": "PAUSED",
        }
        for index in range(module.MAX_CAMPAIGNS_PER_ACCOUNT + 20)
    ]
    current = {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "ad_account_id": "account-1",
        "entity_type": "campaign",
        "external_id": "campaign-current",
        "display_name": "Current active campaign",
        "status": "ACTIVE",
    }
    db = FakeDB({
        SNAPCHAT_ENTITY_COLLECTION: [*historical, current],
    })

    campaigns, limited = await module._campaign_entities(
        db,
        "owner-1",
        "account-1",
    )

    assert [row["external_id"] for row in campaigns] == ["campaign-current"]
    assert limited is False


@pytest.mark.asyncio
async def test_adsquad_window_fetch_is_parallel_and_bounded(monkeypatch):
    active = 0
    peak = 0

    async def fake_fetch(
        context,
        client,
        access_token,
        *,
        campaign_id,
        request_start,
        request_end,
        action_report_time,
    ):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ([{"campaign_id": campaign_id}], [], True)

    monkeypatch.setattr(module, "_fetch_campaign_adsquad_totals", fake_fetch)
    campaigns = [
        {"external_id": f"campaign-{index:02d}"}
        for index in range(module.ADSQUAD_FETCH_CONCURRENCY * 3)
    ]

    results = await module._fetch_adsquad_window(
        object(),
        object(),
        "token",
        campaigns=campaigns,
        request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert len(results) == (
        len(campaigns) * len(module.ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES)
    )
    assert 1 < peak <= module.ADSQUAD_FETCH_CONCURRENCY
    assert module.ADSQUAD_REFRESH_SOURCE_MODE.endswith(
        "ad_squad_active_bounded_total_v4"
    )


def test_day_buckets_follow_requested_timezone():
    rows = [
        {
            "campaign_id": "campaign-1",
            "ad_squad_id": "squad-1",
            "start_time": "2026-08-04T00:30:00+00:00",
            "end_time": "2026-08-04T01:30:00+00:00",
            "metrics": {"spend": 1_000_000},
        }
    ]

    riyadh = module._day_buckets(
        rows,
        timezone_name="Asia/Riyadh",
        start_date=date(2026, 8, 4),
        end_date=date(2026, 8, 4),
    )
    los_angeles = module._day_buckets(
        rows,
        timezone_name="America/Los_Angeles",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
    )

    assert ("campaign-1", "squad-1", "2026-08-04") in riyadh
    assert ("campaign-1", "squad-1", "2026-08-03") in los_angeles


@pytest.mark.asyncio
async def test_report_keeps_zero_spend_adsquad_from_entity_catalog(monkeypatch):
    account = {
        "ad_account_id": "account-1",
        "display_name": "سناب الرياض",
        "currency": "SAR",
        "timezone": "Asia/Riyadh",
    }

    async def selected_accounts(db, user_id):
        return [deepcopy(account)]

    async def cost_settings(db, user_id):
        return {"items": []}

    monkeypatch.setattr(module, "_load_selected_accounts", selected_accounts)
    import ads_manager.account_cost_settings as account_cost_settings

    monkeypatch.setattr(
        account_cost_settings,
        "list_account_cost_settings",
        cost_settings,
    )

    db = FakeDB({
        module.SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION: [],
        SNAPCHAT_ENTITY_COLLECTION: [
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "campaign",
                "external_id": "campaign-1",
                "display_name": "حملة المبيعات",
                "status": "ACTIVE",
            },
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "ad_squad",
                "external_id": "squad-1",
                "campaign_id": "campaign-1",
                "display_name": "مجموعة الرياض",
                "status": "PAUSED",
                "optimization_goal": "PURCHASE",
                "daily_budget_micro": 50_000_000,
            },
        ],
    })

    report = await module.build_account_timezone_adsquad_report(
        db,
        "owner-1",
        account_id="account-1",
        from_date="2026-08-04",
        to_date="2026-08-04",
        query=None,
        page=1,
        limit=25,
        now=lambda: datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
    )

    assert report["pagination"]["total"] == 1
    row = report["ad_squads"][0]
    assert row["ad_squad_name"] == "مجموعة الرياض"
    assert row["campaign_name"] == "حملة المبيعات"
    assert row["status"] == "PAUSED"
    assert row["spend_sar"] is None
    assert row["budget"]["daily_native"] == 50
    assert report["source"]["identity_coverage_pct"] == 100
    assert report["source_only"] is True
    assert report["provider_write_reached"] is False
    assert report["accounting_write_reached"] is False
