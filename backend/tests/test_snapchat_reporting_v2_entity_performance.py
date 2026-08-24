from __future__ import annotations

from datetime import datetime, timezone

import pytest

from snapchat_v2.client import SnapchatClientError, SnapchatV2Client
from snapchat_v2.routes import _add_sar_spend, _entity_performance_report
from snapchat_v2.salla_outcomes import load_salla_campaign_outcomes
from snapchat_v2.sync_pipeline import SnapchatV2SyncPipeline
from snapchat_v2.token_store import SnapchatTokenStoreError
from unified_marketing.adapters.snapchat_v2 import build_snapchat_v2_unified_report


class FakeResponse:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload
        self.headers = {}
        self.text = ""

    def json(self):
        return self._payload


class FakeHTTPClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, *, headers, params):
        assert headers["Authorization"] == "Bearer safe-token"
        self.calls.append((url, params))
        return self.responses.pop(0)


class FakeHTTPFactory:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.clients: list[FakeHTTPClient] = []

    def __call__(self, **_kwargs):
        client = FakeHTTPClient(self.responses)
        self.clients.append(client)
        return client


class FakeTokenStore:
    async def get_access_token(self, _user_id, *, force_refresh=False):
        assert force_refresh is False
        return "safe-token"


class FailedTokenStore:
    async def get_access_token(self, _user_id, *, force_refresh=False):
        raise SnapchatTokenStoreError(
            "snapchat_needs_reauth",
            "Reconnect Snapchat.",
            needs_reauth=True,
        )


def performance_payload(key: str, external_id: str) -> dict:
    return {
        "request_status": "SUCCESS",
        "timeseries_stats": [
            {
                "sub_request_status": "SUCCESS",
                "timeseries_stat": {
                    "granularity": "HOUR",
                    "breakdown_stats": {
                        key: [
                            {
                                "id": external_id,
                                "timeseries": [
                                    {
                                        "start_time": "2026-08-22T10:00:00+00:00",
                                        "end_time": "2026-08-22T11:00:00+00:00",
                                        "stats": {
                                            "spend": 1_250_000,
                                            "impressions": 100,
                                            "swipes": 5,
                                            "video_views": 20,
                                            "conversion_purchases": 1,
                                            "conversion_purchases_value": 5_000_000,
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        ],
        "paging": {},
    }


@pytest.mark.asyncio
async def test_fetches_ad_squad_hourly_facts_from_campaign_breakdown():
    factory = FakeHTTPFactory([FakeResponse(performance_payload("adsquad", "s1"))])
    client = SnapchatV2Client(
        object(),
        "u1",
        token_store=FakeTokenStore(),
        client_factory=factory,
    )

    result = await client.fetch_breakdown_hourly_facts(
        {"ad_account_id": "a1", "timezone": "UTC", "currency": "USD"},
        campaign_ids=["c1"],
        entity_type="ad_squad",
        start_utc=datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
        sync_run_id="run-1",
    )

    assert result["coverage"]["status"] == "complete"
    assert result["campaigns_requested"] == 1
    assert result["rows"] == [
        {
            "user_id": "u1",
            "provider": "snapchat_ads",
            "ad_account_id": "a1",
            "campaign_id": "c1",
            "ad_squad_id": "s1",
            "hour_start_utc": datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
            "hour_end_utc": datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
            "account_timezone": "UTC",
            "currency": "USD",
            "action_report_time": "conversion",
            "attribution_windows": {"swipe": "28_DAY", "view": "7_DAY"},
            "spend_native": 1.25,
            "impressions": 100,
            "swipes": 5,
            "video_views": 20,
            "purchases": 1,
            "purchase_value_native": 5.0,
            "sync_run_id": "run-1",
            "source": {
                "api": "snapchat_marketing_api",
                "granularity": "HOUR",
                "breakdown": "adsquad",
                "request_windows": [
                    {
                        "start_utc": datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
                        "end_utc": datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
                        "provider_start": datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
                        "provider_end": datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
                    }
                ],
            },
            "coverage": {
                "status": "complete",
                "data_state": "confirmed_data",
                "expected_requests": 1,
                "completed_requests": 1,
                "rows_received": 1,
            },
        }
    ]
    url, params = factory.clients[0].calls[0]
    assert url.endswith("/campaigns/c1/stats")
    assert params["breakdown"] == "adsquad"
    assert params["action_report_time"] == "conversion"
    assert params["swipe_up_attribution_window"] == "28_DAY"
    assert params["view_attribution_window"] == "7_DAY"


@pytest.mark.asyncio
async def test_ad_facts_retain_campaign_and_ad_squad_parent_identity():
    client = SnapchatV2Client(
        object(),
        "u1",
        token_store=FakeTokenStore(),
        client_factory=FakeHTTPFactory(
            [FakeResponse(performance_payload("ad", "ad1"))]
        ),
    )

    result = await client.fetch_breakdown_hourly_facts(
        {"ad_account_id": "a1", "timezone": "UTC", "currency": "USD"},
        campaign_ids=["c1"],
        entity_type="ad",
        start_utc=datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
        sync_run_id="run-1",
        ad_squad_by_ad_id={"ad1": "s1"},
    )

    assert result["rows"][0]["campaign_id"] == "c1"
    assert result["rows"][0]["ad_squad_id"] == "s1"
    assert result["rows"][0]["ad_id"] == "ad1"


@pytest.mark.asyncio
async def test_breakdown_token_failure_preserves_incomplete_coverage():
    client = SnapchatV2Client(
        object(),
        "u1",
        token_store=FailedTokenStore(),
        client_factory=FakeHTTPFactory([]),
    )

    with pytest.raises(SnapchatClientError) as raised:
        await client.fetch_breakdown_hourly_facts(
            {"ad_account_id": "a1", "timezone": "UTC", "currency": "USD"},
            campaign_ids=["c1"],
            entity_type="ad_squad",
            start_utc=datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
            end_utc=datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
            sync_run_id="run-1",
        )

    assert raised.value.needs_reauth is True
    assert raised.value.coverage["status"] == "incomplete"
    assert raised.value.coverage["rows_received"] == 0


@pytest.mark.asyncio
async def test_entity_report_joins_ad_to_v2_parent_identities(monkeypatch):
    async def fake_facts(*_args, **_kwargs):
        return [
            {
                "external_id": "ad1",
                "campaign_id": "c1",
                "ad_squad_id": "s1",
                "spend_native": 2.0,
                "impressions": 100,
                "swipes": 5,
                "video_views": 20,
                "purchases": 1,
                "purchase_value_native": 8.0,
            },
            {
                "external_id": "ad2",
                "campaign_id": "c2",
                "ad_squad_id": "s2",
                "spend_native": 99.0,
                "impressions": 1000,
                "swipes": 50,
                "video_views": 200,
                "purchases": 10,
                "purchase_value_native": 800.0,
            },
        ]

    async def fake_entities(*_args, entity_type, **_kwargs):
        return {
            "campaign": [
                {"external_id": "c1", "name": "Campaign", "active": True}
            ],
            "ad_squad": [
                {
                    "external_id": "s1",
                    "name": "Squad",
                    "campaign_id": "c1",
                    "active": True,
                },
                {
                    "external_id": "s2",
                    "name": "Other Squad",
                    "campaign_id": "c2",
                    "active": True,
                },
            ],
            "ad": [
                {
                    "external_id": "ad1",
                    "name": "Creative",
                    "ad_squad_id": "s1",
                    "active": True,
                },
                {
                    "external_id": "ad2",
                    "name": "Other Creative",
                    "ad_squad_id": "s2",
                    "active": True,
                },
            ],
        }[entity_type]

    async def complete(*_args, **_kwargs):
        return "complete"

    monkeypatch.setattr("snapchat_v2.routes.load_hourly_facts", fake_facts)
    monkeypatch.setattr("snapchat_v2.routes.list_entities", fake_entities)
    monkeypatch.setattr("snapchat_v2.routes._latest_level_status", complete)

    result = await _entity_performance_report(
        object(),
        user_id="u1",
        account={"ad_account_id": "a1", "timezone": "UTC", "currency": "USD"},
        date_from=datetime(2026, 8, 22, tzinfo=timezone.utc).date(),
        date_to=datetime(2026, 8, 22, tzinfo=timezone.utc).date(),
        timezone_name="UTC",
        action_report_time="conversion",
        entity_type="ad",
        campaign_id="c1",
        ad_squad_id="s1",
    )

    assert result["performance_sync_status"] == "complete"
    assert result["totals"]["spend_native"] == 2.0
    assert result["totals"]["roas"] == 4.0
    assert result["rows"][0]["ad_name"] == "Creative"
    assert result["rows"][0]["ad_squad_name"] == "Squad"
    assert result["rows"][0]["campaign_name"] == "Campaign"

    async def partial(*_args, **_kwargs):
        return "partial"

    monkeypatch.setattr("snapchat_v2.routes._latest_level_status", partial)
    partial_result = await _entity_performance_report(
        object(),
        user_id="u1",
        account={"ad_account_id": "a1", "timezone": "UTC", "currency": "USD"},
        date_from=datetime(2026, 8, 22, tzinfo=timezone.utc).date(),
        date_to=datetime(2026, 8, 22, tzinfo=timezone.utc).date(),
        timezone_name="UTC",
        action_report_time="conversion",
        entity_type="ad",
        campaign_id="c1",
        ad_squad_id="s1",
    )
    assert partial_result["rows"][0]["performance_sync_status"] == "partial"


@pytest.mark.asyncio
async def test_sar_conversion_keeps_identity_without_facts_unknown(monkeypatch):
    async def cost(*_args, **_kwargs):
        return {
            "exchange_rate_to_sar": 3.75,
            "cost_coverage": {"status": "complete"},
        }

    monkeypatch.setattr("snapchat_v2.routes.calculate_cost_components", cost)
    rows = [
        {"spend_native": 0, "source_fact_count": 0},
        {"spend_native": 2, "source_fact_count": 1},
    ]
    totals = {"spend_native": 2, "source_fact_count": 1}

    await _add_sar_spend(
        object(),
        user_id="u1",
        account={"currency": "USD"},
        rows=rows,
        totals=totals,
    )

    assert rows[0]["spend_sar"] is None
    assert rows[1]["spend_sar"] == 7.5
    assert totals["spend_sar"] == 7.5


@pytest.mark.asyncio
async def test_pipeline_publishes_child_shadow_facts_and_marks_level_complete(
    monkeypatch,
):
    calls: dict[str, object] = {}

    async def stage(*_args, **_kwargs):
        return None

    async def save(_db, rows, **_kwargs):
        calls["saved_rows"] = rows
        return {"rows_received": len(rows), "rows_saved": len(rows)}

    async def set_status(_db, _run_id, level, status, **kwargs):
        calls["level"] = level
        calls["status"] = status
        calls["coverage"] = kwargs.get("coverage")

    monkeypatch.setattr("snapchat_v2.sync_pipeline.update_sync_stage", stage)
    monkeypatch.setattr("snapchat_v2.sync_pipeline.upsert_hourly_facts", save)
    monkeypatch.setattr("snapchat_v2.sync_pipeline.set_level_status", set_status)

    class Client:
        async def fetch_breakdown_hourly_facts(self, _account, **kwargs):
            calls["campaign_ids"] = kwargs["campaign_ids"]
            return {
                "rows": [{"external_id": "s1"}],
                "coverage": {"status": "complete", "rows_received": 1},
            }

    result, error = await SnapchatV2SyncPipeline(object())._sync_breakdown_performance(
        Client(),
        user_id="u1",
        account={"ad_account_id": "a1"},
        sync_run_id="run-1",
        entity_type="ad_squad",
        campaign_rows=[
            {"campaign_id": "c1", "spend_native": 10},
            {"campaign_id": "c2", "spend_native": 0},
        ],
        start_utc=datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
        action_report_time="conversion",
    )

    assert error is None
    assert result["rows_saved"] == 1
    assert calls["campaign_ids"] == ["c1"]
    assert calls["level"] == "ad_squad"
    assert calls["status"] == "complete"


class OrderCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return self.rows[:length]


class OrderCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, *_args, **_kwargs):
        return OrderCursor(self.rows)


class SettingsCollection:
    async def find_one(self, *_args, **_kwargs):
        return {
            "report_included_statuses": ["completed"],
            "hide_inferred_date_orders": False,
        }


class OrderDB:
    def __init__(self, rows):
        self.unified_orders = OrderCollection(rows)
        self.settings = SettingsCollection()


@pytest.mark.asyncio
async def test_salla_outcomes_match_v2_campaigns_without_distributing_direct_orders(
):
    db = OrderDB(
        [
            {
                "order_number": "1001",
                "order_date": "2026-08-22",
                "created_at": "2026-08-22T12:00:00+00:00",
                "order_status": "completed",
                "total_amount": 250,
                "campaign_id": "c1",
                "source": "snapchat",
            },
            {
                "order_number": "1002",
                "order_date": "2026-08-22",
                "created_at": "2026-08-22T13:00:00+00:00",
                "order_status": "completed",
                "total_amount": 150,
                "source": "direct",
            },
            {
                "order_number": "1003",
                "order_date": "2026-08-22",
                "created_at": "2026-08-22T14:00:00+00:00",
                "order_status": "cancelled",
                "total_amount": 99,
                "campaign_id": "c1",
                "source": "snapchat",
            },
            {
                "order_number": "1004",
                "order_date": "2026-08-22",
                "created_at": "2026-08-22T15:00:00+00:00",
                "order_status": "completed",
                "total_amount": 500,
                "campaign_id": "c1",
                "source": "meta",
            },
        ]
    )

    result = await load_salla_campaign_outcomes(
        db,
        "u1",
        account_id="a1",
        date_from=datetime(2026, 8, 22, tzinfo=timezone.utc).date(),
        date_to=datetime(2026, 8, 22, tzinfo=timezone.utc).date(),
        timezone_name="UTC",
        identities=[
            {
                "account_id": "a1",
                "campaign_id": "c1",
                "campaign_name": "Campaign",
            }
        ],
        platform_purchases=3,
    )

    # Count every created Salla order matched to the campaign, while revenue
    # remains limited to financially included statuses.
    assert result["by_campaign"]["c1"] == {"orders": 2, "sales_sar": 250.0}
    assert result["summary"]["campaign_matched_orders"] == 2
    assert result["summary"]["campaign_matched_financial_orders"] == 1
    assert result["summary"]["non_campaign_orders"] == 2
    assert result["summary"]["platform_minus_confirmed_campaign_orders"] == 2
    assert len(result["orders"]) == 4


def test_snapchat_adapter_emits_provider_neutral_contract_and_blocks_decisions():
    report = build_snapchat_v2_unified_report(
        account_value={
            "ad_account_id": "a1",
            "display_name": "Amasi",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        },
        period_value={
            "date_from": "2026-08-22",
            "date_to": "2026-08-22",
            "timezone": "America/Los_Angeles",
            "action_report_time": "conversion",
        },
        entity_type="campaign",
        rows=[
            {
                "external_id": "c1",
                "name": "Campaign",
                "active": True,
                "spend_native": 10,
                "spend_sar": 37.5,
                "impressions": 100,
                "swipes": 5,
                "video_views": 25,
                "purchases": 2,
                "purchase_value_native": 40,
                "roas": 4,
                "source_fact_count": 24,
                "performance_sync_status": "complete",
                "salla_results": {"orders": 3, "sales_sar": 150, "roas": 4},
            }
        ],
        totals={
            "spend_native": 10,
            "spend_sar": 37.5,
            "impressions": 100,
            "swipes": 5,
            "video_views": 25,
            "purchases": 2,
            "purchase_value_native": 40,
            "roas": 4,
            "source_fact_count": 24,
            "salla_results": {"orders": 3, "sales_sar": 150, "roas": 4},
        },
        sync_status="complete",
        orders=[{"order_number": "1001", "campaign_id": "c1"}],
        order_summary={
            "coverage_status": "complete",
            "campaign_matched_financial_orders": 3,
        },
    )

    assert report["contract_version"] == "unified-marketing-data-v1"
    assert report["entity_level"] == "campaign"
    assert report["rows"][0]["delivery"]["clicks"] == 5
    assert report["rows"][0]["platform_outcomes"]["conversions"] == 2
    assert report["rows"][0]["commerce_outcomes"]["orders"] == 3
    assert report["rows"][0]["lineage"]["adapter"] == "snapchat_v2"
    assert report["orders"][0]["amount"] == {"amount": None, "currency": "SAR"}
    assert report["order_summary"]["matched_financial_orders"] == 3
    assert report["decision_eligibility"] == {
        "eligible": False,
        "reason": "shadow_sync_not_accepted",
    }


def test_snapchat_adapter_does_not_render_missing_ad_salla_attribution_as_zero():
    report = build_snapchat_v2_unified_report(
        account_value={
            "ad_account_id": "a1",
            "display_name": "Amasi",
            "currency": "USD",
            "timezone": "UTC",
        },
        period_value={
            "date_from": "2026-08-22",
            "date_to": "2026-08-22",
            "timezone": "UTC",
            "action_report_time": "conversion",
        },
        entity_type="ad",
        rows=[
            {
                "external_id": "ad1",
                "name": "Ad",
                "campaign_id": "c1",
                "ad_squad_id": "s1",
                "performance_sync_status": "complete",
            }
        ],
        totals={},
        sync_status="complete",
    )

    commerce = report["rows"][0]["commerce_outcomes"]
    assert commerce["status"] == "unavailable"
    assert commerce["orders"] is None
    assert commerce["revenue"]["amount"] is None
    assert report["rows"][0]["delivery"]["spend"]["amount"] is None
    assert report["rows"][0]["delivery"]["impressions"] is None
    assert report["rows"][0]["platform_outcomes"]["conversions"] is None


def test_snapchat_adapter_keeps_failed_salla_read_partial_instead_of_zero():
    report = build_snapchat_v2_unified_report(
        account_value={
            "ad_account_id": "a1",
            "display_name": "Amasi",
            "currency": "USD",
            "timezone": "UTC",
        },
        period_value={
            "date_from": "2026-08-22",
            "date_to": "2026-08-22",
            "timezone": "UTC",
            "action_report_time": "conversion",
        },
        entity_type="campaign",
        rows=[
            {
                "external_id": "c1",
                "name": "Campaign",
                "performance_sync_status": "complete",
                "salla_results": {
                    "status": "partial",
                    "orders": None,
                    "sales_sar": None,
                },
            }
        ],
        totals={
            "salla_results": {
                "status": "partial",
                "orders": None,
                "sales_sar": None,
            }
        },
        sync_status="complete",
    )

    assert report["rows"][0]["commerce_outcomes"]["status"] == "partial"
    assert report["rows"][0]["commerce_outcomes"]["orders"] is None
