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


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (100, 9),
        (10, 9),
        (9, 9),
        (1, 1),
        (0, 1),
        (None, 9),
        ("invalid", 9),
    ],
)
def test_adsquad_page_limit_caps_legacy_clients_at_nine(requested, expected):
    assert module.normalize_adsquad_page_limit(requested) == expected


def test_extract_adsquad_total_rows_preserves_parent_campaign():
    request_start = datetime(2026, 8, 4, tzinfo=timezone.utc)
    request_end = datetime(2026, 8, 5, tzinfo=timezone.utc)
    payload = {
        "request_status": "SUCCESS",
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


def test_adsquad_success_without_total_stat_is_incomplete():
    rows, errors, successful, breakdown_seen = (
        module.extract_adsquad_total_rows(
            {
                "request_status": "SUCCESS",
                "total_stats": [
                    {"sub_request_status": "SUCCESS", "total_stat": None}
                ]
            },
            campaign_id="campaign-1",
            request_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
            request_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
    )

    assert rows == []
    assert successful == 0
    assert breakdown_seen is False
    assert errors[0]["code"] == "snapchat_adsquad_total_stat_missing"
    assert module._performance_data_state(
        rows,
        errors=errors,
        structure_seen=breakdown_seen,
    ) == module.DATA_STATE_UNKNOWN_INCOMPLETE


def test_adsquad_partial_subrequest_is_not_success():
    rows, errors, successful, breakdown_seen = (
        module.extract_adsquad_total_rows(
            {
                "request_status": "SUCCESS",
                "total_stats": [
                    {
                        "sub_request_status": "PARTIAL",
                        "total_stat": {
                            "breakdown_stats": {"adsquad": []}
                        },
                    }
                ]
            },
            campaign_id="campaign-1",
            request_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
            request_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
    )

    assert rows == []
    assert successful == 0
    assert breakdown_seen is False
    assert errors[0]["code"] == "snapchat_adsquad_subrequest_incomplete"


@pytest.mark.parametrize("request_status", [None, "PARTIAL"])
def test_adsquad_requires_explicit_success_status(request_status):
    payload = {
        "total_stats": [
            {
                "sub_request_status": "SUCCESS",
                "total_stat": {"breakdown_stats": {"adsquad": []}},
            }
        ]
    }
    if request_status is not None:
        payload["request_status"] = request_status

    with pytest.raises(module.SnapchatNativeSyncError) as raised:
        module.extract_adsquad_total_rows(
            payload,
            campaign_id="campaign-1",
            request_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
            request_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )

    assert raised.value.code == "snapchat_adsquad_request_incomplete"


def test_adsquad_missing_subrequest_status_is_incomplete():
    rows, errors, successful, breakdown_seen = module.extract_adsquad_total_rows(
        {
            "request_status": "SUCCESS",
            "total_stats": [
                {"total_stat": {"breakdown_stats": {"adsquad": []}}}
            ],
        },
        campaign_id="campaign-1",
        request_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )

    assert rows == []
    assert successful == 0
    assert breakdown_seen is False
    assert errors[0]["code"] == "snapchat_adsquad_subrequest_incomplete"


@pytest.mark.parametrize(
    ("start_time", "metrics", "expected_code"),
    [
        (
            "not-a-date",
            {"spend": 0},
            "snapchat_adsquad_provider_window_invalid",
        ),
        (
            "2026-08-03T00:00:00+00:00",
            {"spend": 0},
            "snapchat_adsquad_provider_window_invalid",
        ),
        (
            None,
            {"spend": 0, "conversion_purchases": "malformed"},
            "snapchat_adsquad_breakdown_row_invalid",
        ),
    ],
)
def test_adsquad_rejects_malformed_provider_evidence(
    start_time,
    metrics,
    expected_code,
):
    total_stat = {
        "breakdown_stats": {
            "adsquad": [{"id": "squad-1", "stats": metrics}]
        }
    }
    if start_time is not None:
        total_stat["start_time"] = start_time
    rows, errors, successful, breakdown_seen = module.extract_adsquad_total_rows(
        {
            "request_status": "SUCCESS",
            "total_stats": [
                {
                    "sub_request_status": "SUCCESS",
                    "total_stat": total_stat,
                }
            ],
        },
        campaign_id="campaign-1",
        request_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )

    assert rows == []
    assert successful == 0
    assert errors[0]["code"] == expected_code
    assert module._performance_data_state(
        rows,
        errors=errors,
        structure_seen=breakdown_seen,
    ) == module.DATA_STATE_UNKNOWN_INCOMPLETE


@pytest.mark.parametrize(
    ("breakdown_rows", "expected_state"),
    [
        ([], "confirmed_no_data"),
        (
            [
                {
                    "id": "squad-zero",
                    "stats": {"spend": 0, "impressions": 0, "swipes": 0},
                }
            ],
            "confirmed_zero",
        ),
    ],
)
def test_adsquad_distinguishes_confirmed_no_data_and_zero(
    breakdown_rows,
    expected_state,
):
    rows, errors, successful, breakdown_seen = (
        module.extract_adsquad_total_rows(
            {
                "request_status": "SUCCESS",
                "total_stats": [
                    {
                        "sub_request_status": "SUCCESS",
                        "total_stat": {
                            "breakdown_stats": {"adsquad": breakdown_rows}
                        },
                    }
                ]
            },
            campaign_id="campaign-1",
            request_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
            request_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
    )

    assert errors == []
    assert successful == 1
    assert breakdown_seen is True
    assert module._performance_data_state(
        rows,
        errors=errors,
        structure_seen=breakdown_seen,
    ) == expected_state


@pytest.mark.asyncio
async def test_adsquad_page_limit_marks_partial_pagination_incomplete(
    monkeypatch,
):
    class Context:
        async def get_json(self, client, url, *, headers, params):
            return {
                "request_status": "SUCCESS",
                "total_stats": [
                    {
                        "sub_request_status": "SUCCESS",
                        "total_stat": {
                            "breakdown_stats": {
                                "adsquad": [
                                    {
                                        "id": "squad-1",
                                        "stats": {"spend": 1_000_000},
                                    }
                                ]
                            }
                        },
                    }
                ],
                "paging": {
                    "next_link": (
                        "https://adsapi.snapchat.com/v1/campaigns/"
                        "campaign-1/stats?page=2"
                    )
                },
            }

    monkeypatch.setattr(module, "MAX_PAGES", 1)
    rows, errors, breakdown_seen = (
        await module._fetch_campaign_adsquad_totals(
            Context(),
            object(),
            "token",
            campaign_id="campaign-1",
            request_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
            request_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
    )

    assert len(rows) == 1
    assert breakdown_seen is True
    assert errors[-1]["code"] == "snapchat_adsquad_pagination_incomplete"
    assert module._performance_data_state(
        rows,
        errors=errors,
        structure_seen=breakdown_seen,
    ) == module.DATA_STATE_UNKNOWN_INCOMPLETE


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


@pytest.mark.asyncio
async def test_incomplete_adsquad_does_not_write_or_advance_last_success(
    monkeypatch,
):
    from integrations_control_center import (
        snapchat_platform_source_integrity as source_integrity,
    )

    class StateCollection:
        def __init__(self):
            self.updates = []

        async def update_one(self, query, update, *, upsert=False):
            self.updates.append(deepcopy(update))

    class Context:
        db = object()
        user_id = "owner-1"
        provider_calls = 2

        @staticmethod
        def now_iso():
            return "2026-08-08T12:00:00+00:00"

    state = StateCollection()
    writes = []

    async def recent_refresh(*args, **kwargs):
        return None

    async def campaigns(*args, **kwargs):
        return ([{"external_id": "campaign-1"}], False)

    async def fetch_window(*args, **kwargs):
        return [
            {
                "campaign_id": "campaign-1",
                "action_report_time": mode,
                "rows": [
                    {
                        "campaign_id": "campaign-1",
                        "ad_squad_id": "squad-1",
                        "start_time": "2026-08-08T00:00:00+00:00",
                        "end_time": "2026-08-09T00:00:00+00:00",
                        "metrics": {"spend": 0},
                    }
                ],
                "errors": [{"code": "nested_provider_error"}],
                "breakdown_seen": False,
                "data_state": module.DATA_STATE_UNKNOWN_INCOMPLETE,
            }
            for mode in module.ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
        ]

    async def capture_write(*args, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(module, "_recent_refresh", recent_refresh)
    monkeypatch.setattr(module, "_campaign_entities", campaigns)
    monkeypatch.setattr(module, "_fetch_adsquad_window", fetch_window)
    monkeypatch.setattr(module, "_upsert_projection", capture_write)
    monkeypatch.setattr(module, "_collection", lambda *args: state)
    monkeypatch.setattr(
        source_integrity,
        "account_local_dates_for_refresh",
        lambda *args, **kwargs: [date(2026, 8, 8)],
    )
    monkeypatch.setattr(
        source_integrity,
        "account_local_total_window",
        lambda *args, **kwargs: (
            datetime(2026, 8, 8, tzinfo=timezone.utc),
            datetime(2026, 8, 9, tzinfo=timezone.utc),
        ),
    )

    result = await module.refresh_snapchat_adsquad_performance(
        Context(),
        object(),
        "token",
        {"ad_account_id": "account-1", "timezone": "Asia/Riyadh"},
        start_date=date(2026, 8, 8),
        end_date=date(2026, 8, 8),
        now=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
    )

    state_set = state.updates[-1]["$set"]
    assert result["coverage"]["status"] == "incomplete"
    assert result["coverage"]["data_state"] == "unknown_incomplete"
    assert writes == []
    assert "last_success_at" not in state_set
    assert state_set["last_attempt_at"] == "2026-08-08T12:00:00+00:00"


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
    filtered = await module.build_account_timezone_adsquad_report(
        db,
        "owner-1",
        account_id="account-1",
        from_date="2026-08-04",
        to_date="2026-08-04",
        query=None,
        page=1,
        limit=9,
        campaign_id="campaign-other",
        now=lambda: datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
    )
    assert filtered["pagination"]["limit"] == 9
    assert filtered["pagination"]["total"] == 0
    assert filtered["campaign_id"] == "campaign-other"
