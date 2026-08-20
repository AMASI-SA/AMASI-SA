from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from integrations_control_center import snapchat_ad_performance as module
from integrations_control_center import snapchat_native_entities_sync as native_sync
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
    def __init__(self, rows, *, name, find_calls):
        self.rows = rows
        self.name = name
        self.find_calls = find_calls

    def find(self, query, projection=None):
        self.find_calls.append({
            "collection": self.name,
            "query": deepcopy(query),
            "projection": deepcopy(projection),
        })
        return FakeCursor(row for row in self.rows if _matches(row, query))


class FakeDB:
    def __init__(self, collections):
        self.collections = deepcopy(collections)
        self.find_calls = []

    def __getitem__(self, name):
        return FakeCollection(
            self.collections.setdefault(name, []),
            name=name,
            find_calls=self.find_calls,
        )

    def __getattr__(self, name):
        return self[name]


def test_extract_ad_hour_rows_preserves_campaign_identity():
    payload = {
        "request_status": "SUCCESS",
        "timeseries_stats": [
            {
                "sub_request_status": "SUCCESS",
                "timeseries_stat": {
                    "breakdown_stats": {
                        "ad": [
                            {
                                "id": "ad-1",
                                "timeseries": [
                                    {
                                        "start_time": "2026-08-04T00:00:00+03:00",
                                        "end_time": "2026-08-04T01:00:00+03:00",
                                        "stats": {
                                            "spend": 5_000_000,
                                            "impressions": 1000,
                                            "swipes": 50,
                                            "conversion_purchases": 2,
                                            "conversion_purchases_value": 10_000_000,
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        ]
    }

    rows, errors, successful = module.extract_ad_hour_rows(
        payload,
        campaign_id="campaign-1",
    )

    assert successful == 1
    assert errors == []
    assert rows[0]["campaign_id"] == "campaign-1"
    assert rows[0]["ad_id"] == "ad-1"
    assert rows[0]["metrics"]["spend"] == 5_000_000


def test_ad_success_without_timeseries_stat_is_incomplete():
    rows, errors, successful = module.extract_ad_hour_rows(
        {
            "request_status": "SUCCESS",
            "timeseries_stats": [
                {"sub_request_status": "SUCCESS", "timeseries_stat": None}
            ]
        },
        campaign_id="campaign-1",
    )

    assert rows == []
    assert successful == 0
    assert errors[0]["code"] == "snapchat_ad_timeseries_stat_missing"


def test_extract_ad_total_rows_preserves_all_attributed_purchases():
    payload = {
        "request_status": "SUCCESS",
        "total_stats": [
            {
                "sub_request_status": "SUCCESS",
                "total_stat": {
                    "start_time": "2026-08-08T00:00:00+00:00",
                    "end_time": "2026-08-08T12:00:00+00:00",
                    "breakdown_stats": {
                        "ad": [
                            {
                                "id": "ad-1",
                                "stats": {
                                    "spend": 12_000_000,
                                    "conversion_purchases": 15,
                                    "conversion_purchases_value": 50_000_000,
                                },
                            }
                        ]
                    },
                }
            }
        ]
    }

    rows, errors, successful, breakdown_seen = module.extract_ad_total_rows(
        payload,
        campaign_id="campaign-1",
        request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
    )

    assert successful == 1
    assert errors == []
    assert breakdown_seen is True
    assert rows[0]["ad_id"] == "ad-1"
    assert rows[0]["metrics"]["conversion_purchases"] == 15


@pytest.mark.parametrize("request_status", [None, "PARTIAL"])
def test_ad_total_requires_explicit_success_status(request_status):
    payload = {
        "total_stats": [
            {
                "sub_request_status": "SUCCESS",
                "total_stat": {"breakdown_stats": {"ad": []}},
            }
        ]
    }
    if request_status is not None:
        payload["request_status"] = request_status

    with pytest.raises(module.SnapchatNativeSyncError) as raised:
        module.extract_ad_total_rows(
            payload,
            campaign_id="campaign-1",
            request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
            request_end=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )

    assert raised.value.code == "snapchat_ad_request_incomplete"


def test_ad_total_missing_subrequest_status_is_incomplete():
    rows, errors, successful, breakdown_seen = module.extract_ad_total_rows(
        {
            "request_status": "SUCCESS",
            "total_stats": [
                {"total_stat": {"breakdown_stats": {"ad": []}}}
            ],
        },
        campaign_id="campaign-1",
        request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert rows == []
    assert successful == 0
    assert breakdown_seen is False
    assert errors[0]["code"] == "snapchat_ad_subrequest_incomplete"


@pytest.mark.parametrize(
    ("start_time", "metrics", "expected_code"),
    [
        (
            "not-a-date",
            {"spend": 0},
            "snapchat_ad_provider_window_invalid",
        ),
        (
            "2026-08-07T00:00:00+00:00",
            {"spend": 0},
            "snapchat_ad_provider_window_invalid",
        ),
        (
            None,
            {"spend": 0, "conversion_purchases": "malformed"},
            "snapchat_ad_breakdown_row_invalid",
        ),
    ],
)
def test_ad_total_rejects_malformed_provider_evidence(
    start_time,
    metrics,
    expected_code,
):
    total_stat = {
        "breakdown_stats": {"ad": [{"id": "ad-1", "stats": metrics}]}
    }
    if start_time is not None:
        total_stat["start_time"] = start_time
    rows, errors, successful, breakdown_seen = module.extract_ad_total_rows(
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
        request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 9, tzinfo=timezone.utc),
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
                    "id": "ad-zero",
                    "stats": {"spend": 0, "impressions": 0, "swipes": 0},
                }
            ],
            "confirmed_zero",
        ),
    ],
)
def test_ad_distinguishes_confirmed_no_data_and_zero(
    breakdown_rows,
    expected_state,
):
    rows, errors, successful, breakdown_seen = module.extract_ad_total_rows(
        {
            "request_status": "SUCCESS",
            "total_stats": [
                {
                    "sub_request_status": "SUCCESS",
                    "total_stat": {
                        "breakdown_stats": {"ad": breakdown_rows}
                    },
                }
            ]
        },
        campaign_id="campaign-1",
        request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 9, tzinfo=timezone.utc),
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
async def test_total_request_keeps_conversion_only_ad_rows():
    class Context:
        provider_calls = 0

        async def get_json(self, client, url, *, headers, params):
            self.params = deepcopy(params)
            return {
                "request_status": "SUCCESS",
                "total_stats": [
                    {
                        "sub_request_status": "SUCCESS",
                        "total_stat": {
                            "breakdown_stats": {
                                "ad": [
                                    {
                                        "id": "ad-conversion-only",
                                        "stats": {"conversion_purchases": 3},
                                    }
                                ]
                            }
                        }
                    }
                ]
            }

    context = Context()
    rows, errors, breakdown_seen = await module._fetch_campaign_ad_totals(
        context,
        None,
        "token",
        campaign_id="campaign-1",
        request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
    )

    assert errors == []
    assert breakdown_seen is True
    assert rows[0]["metrics"]["conversion_purchases"] == 3
    assert context.params["granularity"] == "TOTAL"
    assert context.params["omit_empty"] == "false"


@pytest.mark.asyncio
async def test_ad_page_limit_marks_partial_pagination_incomplete(monkeypatch):
    class Context:
        async def get_json(self, client, url, *, headers, params):
            return {
                "request_status": "SUCCESS",
                "total_stats": [
                    {
                        "sub_request_status": "SUCCESS",
                        "total_stat": {
                            "breakdown_stats": {
                                "ad": [
                                    {
                                        "id": "ad-1",
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
    rows, errors, breakdown_seen = await module._fetch_campaign_ad_totals(
        Context(),
        object(),
        "token",
        campaign_id="campaign-1",
        request_start=datetime(2026, 8, 8, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert len(rows) == 1
    assert breakdown_seen is True
    assert errors[-1]["code"] == "snapchat_ad_pagination_incomplete"
    assert module._performance_data_state(
        rows,
        errors=errors,
        structure_seen=breakdown_seen,
    ) == module.DATA_STATE_UNKNOWN_INCOMPLETE


def test_ad_delivery_separates_switch_review_and_parent_blockers():
    paused = module._delivery_for_ad({"status": "PAUSED"}, None)
    assert paused["delivery_reason_code"] == "AD_CONFIGURED_PAUSED"

    deleted = module._delivery_for_ad(
        {"status": "ACTIVE", "deleted": True},
        None,
    )
    assert deleted["delivery_state"] == "NOT_DELIVERING"
    assert deleted["delivery_reason_code"] == "AD_DELETED"
    assert deleted["deliverable"] is False

    rejected = module._delivery_for_ad(
        {"status": "ACTIVE", "review_status": "REJECTED"},
        None,
    )
    assert rejected["delivery_status"] == "لا تسليم — الإعلان مرفوض"

    inherited = module._delivery_for_ad(
        {"status": "ACTIVE", "review_status": "APPROVED"},
        {
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "لا تسليم — الحساب موقوف بسبب الدفع",
            "delivery_reason_code": "ACCOUNT_PAYMENT_BLOCKED",
        },
    )
    assert inherited["configured_status"] == "ACTIVE"
    assert inherited["delivery_inherited_from_ad_squad"] is True
    assert inherited["delivery_reason_code"] == "ACCOUNT_PAYMENT_BLOCKED"

    paused_parent = module._delivery_for_ad(
        {"status": "ACTIVE", "review_status": "APPROVED"},
        {"status": "PAUSED"},
    )
    assert paused_parent["configured_status"] == "ACTIVE"
    assert paused_parent["delivery_state"] == "NOT_DELIVERING"
    assert (
        paused_parent["delivery_status"]
        == "غير نشط — المجموعة الإعلانية متوقفة"
    )
    assert (
        paused_parent["delivery_reason_code"]
        == "PARENT_AD_SQUAD_CONFIGURED_PAUSED"
    )
    assert paused_parent["delivery_inherited_from_ad_squad"] is True


    unknown = module._delivery_for_ad({}, None)
    assert unknown["delivery_state"] == "UNKNOWN"
    assert unknown["delivery_reason_code"] == "AD_IDENTITY_NOT_SYNCED"

    learning = module._delivery_for_ad(
        {
            "status": "ACTIVE",
            "review_status": "APPROVED",
            "delivery_status": ["VALID", "LEARNING_PHASE"],
        },
        {"delivery_state": "DELIVERING"},
    )
    assert learning["delivery_state"] == "DELIVERING"
    assert learning["delivery_reason_code"] == "LEARNING_PHASE"


@pytest.mark.asyncio
async def test_report_includes_zero_spend_ad_and_parent_names(monkeypatch):
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
                "status": "ACTIVE",
            },
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "ad",
                "external_id": "ad-1",
                "campaign_id": "campaign-1",
                "creative_id": "creative-1",
                "display_name": "فيديو المنتج الأول",
                "status": "ACTIVE",
                "review_status": "APPROVED",
                "provider_snapshot": {"ad_squad_id": "squad-1"},
            },
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "creative",
                "external_id": "creative-1",
                "display_name": "إبداع المنتج",
                "provider_snapshot": {"type": "SNAP_AD"},
            },
        ],
    })

    report = await module.build_account_timezone_ad_report(
        db,
        "owner-1",
        account_id="account-1",
        from_date="2026-08-04",
        to_date="2026-08-04",
        query=None,
        page=1,
        limit=100,
        active_campaigns_only=True,
        now=lambda: datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
    )

    entity_find = next(
        call for call in db.find_calls
        if call["collection"] == SNAPCHAT_ENTITY_COLLECTION
    )
    assert entity_find["projection"] == module.AD_REPORT_ENTITY_PROJECTION
    assert report["source"]["entity_projection_bounded"] is True
    assert report["source"]["parent_catalog_reused"] is True
    assert report["pagination"]["total"] == 1
    ad = report["ads"][0]
    assert ad["ad_name"] == "فيديو المنتج الأول"
    assert ad["ad_squad_name"] == "مجموعة الرياض"
    assert ad["campaign_name"] == "حملة المبيعات"
    assert ad["creative_name"] == "إبداع المنتج"
    assert ad["creative_type"] == "SNAP_AD"
    assert ad["status"] == "ACTIVE"
    assert ad["delivery_state"] == "DELIVERING"
    assert ad["spend_sar"] is None
    assert report["source"]["salla_results_supported"] is False
    assert report["policy"]["mutations_allowed"] is False
    assert report["provider_write_reached"] is False
    filtered_squad = await module.build_account_timezone_ad_report(
        db,
        "owner-1",
        account_id="account-1",
        from_date="2026-08-04",
        to_date="2026-08-04",
        query=None,
        page=1,
        limit=9,
        campaign_id="campaign-1",
        ad_squad_id="squad-1",
        active_campaigns_only=True,
        now=lambda: datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
    )
    assert filtered_squad["pagination"]["total"] == 1
    assert filtered_squad["ads"][0]["ad_squad_id"] == "squad-1"
    filtered = await module.build_account_timezone_ad_report(
        db,
        "owner-1",
        account_id="account-1",
        from_date="2026-08-04",
        to_date="2026-08-04",
        query=None,
        page=1,
        limit=9,
        campaign_id="campaign-other",
        active_campaigns_only=True,
        now=lambda: datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
    )
    assert filtered["pagination"]["limit"] == 9
    assert filtered["pagination"]["total"] == 0
    assert filtered["campaign_id"] == "campaign-other"

@pytest.mark.asyncio
async def test_refresh_hydrates_exact_ad_identity_before_recent_skip(
    monkeypatch,
):
    events = []

    class Context:
        db = object()
        user_id = "owner-1"
        provider_calls = 0

    context = Context()

    async def fake_identity_sync(
        sync_context,
        client,
        access_token,
        account,
    ):
        assert sync_context is context
        assert access_token == "token"
        assert account["ad_account_id"] == "account-1"
        events.append("identity")
        sync_context.provider_calls += 2
        return 3, {"ad": 3}, []

    async def fake_recent_refresh(
        db,
        user_id,
        account_id,
        *,
        now,
    ):
        assert db is context.db
        assert user_id == "owner-1"
        assert account_id == "account-1"
        events.append("recent")
        return {
            "coverage": {
                "status": "complete",
                "data_state": module.DATA_STATE_CONFIRMED_DATA,
                "expected_requests": 2,
                "completed_requests": 2,
            }
        }

    monkeypatch.setattr(
        module,
        "sync_snapchat_ad_entities",
        fake_identity_sync,
    )
    monkeypatch.setattr(module, "_recent_refresh", fake_recent_refresh)

    result = await module.refresh_snapchat_ad_performance(
        context,
        object(),
        "token",
        {"ad_account_id": "account-1", "timezone": "America/Los_Angeles"},
        start_date=date(2026, 8, 9),
        end_date=date(2026, 8, 9),
        now=datetime(2026, 8, 9, 12, tzinfo=timezone.utc),
    )

    assert events == ["identity", "recent"]
    assert result["skip_reason"] == "fresh_within_15_minutes"
    assert result["provider_calls"] == 2
    assert result["identity_rows_saved"] == 3
    assert result["identity_counts"] == {"ad": 3}
    assert result["rows_saved"] == 0


@pytest.mark.asyncio
async def test_incomplete_ad_does_not_write_or_advance_last_success(
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

    async def identity_sync(*args, **kwargs):
        return 1, {"ad": 1}, []

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
                        "ad_id": "ad-1",
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

    monkeypatch.setattr(module, "sync_snapchat_ad_entities", identity_sync)
    monkeypatch.setattr(module, "_recent_refresh", recent_refresh)
    monkeypatch.setattr(module, "_campaign_entities", campaigns)
    monkeypatch.setattr(module, "_fetch_ad_window", fetch_window)
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

    result = await module.refresh_snapchat_ad_performance(
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


@pytest.mark.asyncio
async def test_ad_identity_sync_reads_deleted_ads_without_sort():
    class MarkerCollection:
        async def find_one(self, query, projection=None):
            return None

        async def update_one(self, query, update):
            return None

    class MarkerDB:
        def __getitem__(self, name):
            return MarkerCollection()

    class Context:
        db = MarkerDB()
        user_id = "owner-1"

        @staticmethod
        def now_iso():
            return "2026-08-12T12:00:00+00:00"

        async def get_json(self, client, url, *, headers, params):
            self.url = url
            self.params = deepcopy(params)
            return {"ads": [], "paging": {}}

    context = Context()
    saved, counts, errors = await native_sync.sync_snapchat_ad_entities(
        context,
        object(),
        "token",
        {"ad_account_id": "account-1"},
    )

    assert context.url.endswith("/adaccounts/account-1/ads")
    assert context.params["read_deleted_entities"] == "true"
    assert "sort" not in context.params
    assert saved == 0
    assert counts == {"ad": 0}
    assert errors == []
