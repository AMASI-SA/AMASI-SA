from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from integrations_control_center import snapchat_account_hourly_chart as module


def _matches(row, query):
    for key, expected in query.items():
        value = row.get(key)
        if isinstance(expected, dict):
            for operator, operand in expected.items():
                if operator == "$in" and value not in operand:
                    return False
                if operator == "$gte" and not (value is not None and value >= operand):
                    return False
                if operator == "$lte" and not (value is not None and value <= operand):
                    return False
        elif value != expected:
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


@pytest.mark.asyncio
async def test_capture_wrapper_preserves_account_hour_fetch_result(monkeypatch):
    expected = module.hourly.AccountHourFetchResult(
        rows=[],
        errors=[],
        coverage={
            "status": "complete",
            "data_state": "confirmed_no_data",
            "expected_requests": 1,
            "completed_requests": 1,
        },
    )

    async def base_fetch(*args, **kwargs):
        return expected

    async def base_refresh(*args, **kwargs):
        return {}

    monkeypatch.setattr(module.hourly, "_fetch_account_hours", base_fetch)
    monkeypatch.setattr(
        module.hourly,
        "refresh_snapchat_account_hours",
        base_refresh,
    )
    module.install_snapchat_account_hourly_capture()

    actual = await module.hourly._fetch_account_hours(object())

    assert actual is expected
    assert isinstance(actual, module.hourly.AccountHourFetchResult)
    assert actual.coverage == expected.coverage
    rows, errors = actual
    assert rows == []
    assert errors == []


@pytest.mark.asyncio
async def test_capture_wrapper_rejects_legacy_tuple_without_making_coverage(
    monkeypatch,
):
    async def legacy_fetch(*args, **kwargs):
        return [], []

    async def base_refresh(*args, **kwargs):
        return {}

    monkeypatch.setattr(module.hourly, "_fetch_account_hours", legacy_fetch)
    monkeypatch.setattr(
        module.hourly,
        "refresh_snapchat_account_hours",
        base_refresh,
    )
    module.install_snapchat_account_hourly_capture()

    with pytest.raises(module.hourly.SnapchatNativeSyncError) as raised:
        await module.hourly._fetch_account_hours(object())

    assert raised.value.code == "snapchat_account_hour_result_contract_invalid"
    assert raised.value.retryable is True
    assert raised.value.result == {
        "contract_valid": False,
        "result_name": "hourly_capture_source",
    }
    assert "coverage" not in raised.value.result


def test_aggregate_campaign_rows_into_account_local_hours():
    rows = [
        {
            "campaign_id": "campaign-1",
            "start_time": "2026-08-04T00:10:00+03:00",
            "end_time": "2026-08-04T01:00:00+03:00",
            "metrics": {
                "spend": 1_000_000,
                "conversion_purchases": 1,
                "conversion_purchases_value": 5_000_000,
            },
        },
        {
            "campaign_id": "campaign-2",
            "start_time": "2026-08-04T00:20:00+03:00",
            "end_time": "2026-08-04T01:00:00+03:00",
            "metrics": {
                "spend": 2_000_000,
                "conversion_purchases": 2,
                "conversion_purchases_value": 8_000_000,
            },
        },
        {
            "campaign_id": "campaign-1",
            "start_time": "2026-08-04T01:05:00+03:00",
            "end_time": "2026-08-04T02:00:00+03:00",
            "metrics": {"spend": 4_000_000},
        },
    ]

    buckets = module.aggregate_account_rows_by_local_hour(
        rows,
        timezone_name="Asia/Riyadh",
        start_date=date(2026, 8, 4),
        end_date=date(2026, 8, 4),
    )

    assert len(buckets) == 2
    midnight = next(bucket for bucket in buckets.values() if bucket["hour_index"] == 0)
    finalized = module._finalize_bucket(midnight)
    assert finalized["spend"] == 3_000_000
    assert finalized["conversion_purchases"] == 3
    assert finalized["conversion_purchases_value"] == 13_000_000


@pytest.mark.asyncio
async def test_build_hourly_series_returns_full_24_hour_axis():
    db = FakeDB({
        module.SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION: [
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "date": "2026-08-04",
                "date_timezone": "Asia/Riyadh",
                "action_report_time": "conversion",
                "source_mode": module.account_local_hourly_source_mode("conversion"),
                "hour_index": 0,
                "metrics": {
                    "spend": 10_000_000,
                    "conversion_purchases": 2,
                    "conversion_purchases_value": 30_000_000,
                },
                "spend_sar": 10.0,
                "spend_native": 10.0,
                "purchase_value_sar": 30.0,
                "purchase_value_native": 30.0,
                "purchases": 2,
                "updated_at": "2026-08-04T00:05:00+00:00",
            },
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "date": "2026-08-04",
                "date_timezone": "Asia/Riyadh",
                "action_report_time": "conversion",
                "source_mode": module.account_local_hourly_source_mode("conversion"),
                "hour_index": 1,
                "metrics": {
                    "spend": 5_000_000,
                    "conversion_purchases": 1,
                    "conversion_purchases_value": 10_000_000,
                },
                "spend_sar": 5.0,
                "spend_native": 5.0,
                "purchase_value_sar": 10.0,
                "purchase_value_native": 10.0,
                "purchases": 1,
                "updated_at": "2026-08-04T01:05:00+00:00",
            },
        ],
    })

    series, source = await module.build_hourly_chart_series(
        db,
        "owner-1",
        account_id="account-1",
        date_string="2026-08-04",
        timezone_name="Asia/Riyadh",
        result_source="platform",
        now=datetime(2026, 8, 4, 2, 30, tzinfo=timezone.utc),
    )

    assert len(series) == 24
    assert series[0] == {
        "date": "2026-08-04",
        "hour_index": 0,
        "hour": "00:00",
        "orders": 2,
        "sales_sar": 30.0,
        "spend_sar": 10.0,
        "roas": 3.0,
        "cpa_sar": 5.0,
        "observed": True,
        "is_future": False,
        "result_source": "platform",
    }
    assert series[1]["orders"] == 1
    assert series[2]["spend_sar"] == 0.0
    assert series[23]["is_future"] is True
    assert source["hourly_available"] is True
    assert source["stored_granularity"] == "ACCOUNT_LOCAL_HOUR"
    assert source["accounting_eligible"] is False


@pytest.mark.asyncio
async def test_salla_hourly_series_uses_exact_campaign_attribution(monkeypatch):
    db = FakeDB({
        module.SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION: [
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "date": "2026-08-04",
                "date_timezone": "Asia/Riyadh",
                "action_report_time": "conversion",
                "source_mode": module.account_local_hourly_source_mode("conversion"),
                "hour_index": 9,
                "metrics": {"spend": 20_000_000},
                "spend_sar": 20.0,
                "spend_native": 20.0,
                "purchase_value_sar": 0.0,
                "purchase_value_native": 0.0,
                "purchases": 0,
                "updated_at": "2026-08-04T06:05:00+00:00",
            },
        ],
        module.SNAPCHAT_ENTITY_COLLECTION: [
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "campaign",
                "external_id": "campaign-1",
                "display_name": "حملة اليوم الوطني",
            },
        ],
    })

    async def filtered_orders(*args, **kwargs):
        return [
            {
                "created_at": "2026-08-04T09:25:00+03:00",
                "total_amount": 100,
                "campaign_id": "campaign-1",
            },
            {
                "created_at": "2026-08-04T10:15:00+03:00",
                "total_amount": 50,
                "campaign_id": "campaign-1",
            },
        ]

    import dashboard_v2_routes

    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", filtered_orders)
    series, source = await module.build_hourly_chart_series(
        db,
        "owner-1",
        account_id="account-1",
        date_string="2026-08-04",
        timezone_name="Asia/Riyadh",
        result_source="salla",
        now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
    )

    assert series[9]["orders"] == 1
    assert series[9]["sales_sar"] == 100.0
    assert series[9]["spend_sar"] == 20.0
    assert series[9]["roas"] == 5.0
    assert series[10]["orders"] == 1
    assert series[10]["sales_sar"] == 50.0
    assert source["salla_hourly_attribution"]["matched_hourly_orders"] == 2
