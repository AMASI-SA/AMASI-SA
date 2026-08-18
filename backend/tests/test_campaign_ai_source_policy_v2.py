import asyncio
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import campaign_ai_monitor as monitor


def _account(account_id="snap-1", timezone_name="America/Los_Angeles"):
    return {
        "ad_account_id": account_id,
        "display_name": "Snap account",
        "timezone": timezone_name,
    }


def test_snapchat_ai_range_is_anchored_to_each_account_local_today(monkeypatch):
    monkeypatch.setattr(
        monitor._policy,
        "account_local_today",
        lambda _tz, now=None: date(2026, 8, 17),
    )
    start, end = monitor._account_range(
        _account(), date(2026, 8, 16), date(2026, 8, 18)
    )
    assert (start.isoformat(), end.isoformat()) == ("2026-08-15", "2026-08-17")


def test_snapchat_campaign_uses_conversion_platform_results_and_separate_salla_profit(monkeypatch):
    async def accounts(_db, _user_id):
        return [_account(timezone_name="Asia/Riyadh")]

    calls = []

    async def campaign_report(**kwargs):
        calls.append((kwargs["result_source"], kwargs["action_report_time"]))
        common = {
            "campaign_id": "c-1",
            "campaign_name": "الحملة",
            "account_name": "Snap account",
            "status": "ACTIVE",
            "spend_sar": 100.0,
            "impressions": 1000,
            "swipes": 50,
            "observed_days": 3,
            "data_complete": True,
            "budget": {"daily_native": 40.0},
        }
        if kwargs["result_source"] == monitor.RESULT_SOURCE_PLATFORM:
            campaign = {**common, "orders": 5, "sales_sar": 500.0}
        else:
            campaign = {
                **common,
                "orders": 4,
                "sales_sar": 450.0,
                "salla_results": {"orders": 4, "sales_sar": 450.0},
            }
        return {
            "campaigns": [campaign],
            "totals": {"observed_days": 3},
            "account_timezone": "Asia/Riyadh",
            "ai_readiness": {
                "report_ready": True,
                "campaign_details_ready": True,
            },
        }

    async def profitability(*_args, **_kwargs):
        return {
            "by_campaign": {
                ("snap-1", "c-1"): {
                    "orders": 4,
                    "sales_sar": 450.0,
                    "product_cost_sar": 200.0,
                    "known_product_cost_sar": 200.0,
                    "ad_spend_sar": 100.0,
                    "gross_profit_before_ads_sar": 250.0,
                    "contribution_profit_sar": 150.0,
                    "profit_margin_pct": 33.33,
                    "cost_status": "complete",
                    "product_count": 1,
                    "products": [{
                        "name": "منتج",
                        "sales_sar": 450.0,
                        "product_cost_sar": 200.0,
                    }],
                }
            },
            "coverage": {"read_only": True},
        }

    monkeypatch.setattr(monitor._legacy, "_snapchat_accounts", accounts)
    monkeypatch.setattr(
        monitor._policy,
        "account_local_today",
        lambda _tz, now=None: date(2026, 8, 18),
    )
    monkeypatch.setattr(
        monitor._policy,
        "build_account_timezone_campaign_report",
        campaign_report,
    )
    monkeypatch.setattr(
        monitor._policy,
        "build_campaign_profitability",
        profitability,
    )

    rows = asyncio.run(
        monitor._snapchat_campaign_entities(
            object(), "owner", date(2026, 8, 16), date(2026, 8, 18)
        )
    )
    assert calls == [("platform", "conversion"), ("salla", "conversion")]
    assert len(rows) == 1
    row = rows[0]
    assert row["purchases"] == 5
    assert row["revenue_sar"] == 500.0
    assert row["provider_result_source"] == (
        "snapchat_ads_manager_conversion_reporting"
    )
    assert row["salla_campaign_results"] == {
        "source": "unified_orders:salla_exact_account_campaign_match",
        "orders": 4,
        "sales_sar": 450.0,
    }
    assert row["campaign_profitability"]["contribution_profit_sar"] == 150.0
    assert row["campaign_profitability"]["verified_against_page_salla"] is True


def test_snapchat_children_use_platform_conversion_and_page_verified_parent_salla(monkeypatch):
    async def accounts(_db, _user_id):
        return [_account(timezone_name="Asia/Riyadh")]

    seen = []

    async def campaign_report(**kwargs):
        seen.append(("campaign", kwargs["result_source"], kwargs["action_report_time"]))
        assert kwargs["result_source"] == monitor.RESULT_SOURCE_SALLA
        return {
            "account_timezone": "Asia/Riyadh",
            "campaigns": [{
                "campaign_id": "c-1",
                "salla_results": {"orders": 4, "sales_sar": 450.0},
            }],
        }

    async def profitability(*_args, **_kwargs):
        return {
            "by_campaign": {
                ("snap-1", "c-1"): {
                    "orders": 4,
                    "sales_sar": 450.0,
                    "product_cost_sar": 200.0,
                    "ad_spend_sar": 100.0,
                    "contribution_profit_sar": 150.0,
                    "profit_margin_pct": 33.33,
                    "cost_status": "complete",
                }
            }
        }

    async def group_report(*_args, **kwargs):
        seen.append(("group", kwargs["action_report_time"]))
        return {
            "account_timezone": "Asia/Riyadh",
            "ad_squads": [{
                "ad_squad_id": "g-1",
                "ad_squad_name": "المجموعة",
                "campaign_id": "c-1",
                "campaign_name": "الحملة",
                "campaign_status": "ACTIVE",
                "status": "ACTIVE",
                "spend_sar": 60.0,
                "sales_sar": 240.0,
                "orders": 3,
                "impressions": 600,
                "swipes": 30,
                "observed_days": 3,
                "data_complete": True,
                "budget": {"daily_native": 20.0},
            }],
        }

    async def ad_report(*_args, **kwargs):
        seen.append(("ad", kwargs["action_report_time"]))
        return {
            "account_timezone": "Asia/Riyadh",
            "ads": [{
                "ad_id": "a-1",
                "ad_name": "الإعلان",
                "ad_squad_id": "g-1",
                "ad_squad_name": "المجموعة",
                "campaign_id": "c-1",
                "campaign_name": "الحملة",
                "campaign_status": "ACTIVE",
                "ad_squad_status": "ACTIVE",
                "configured_status": "ACTIVE",
                "delivery_state": "DELIVERING",
                "delivery_status": "يتم التسليم",
                "spend_sar": 30.0,
                "sales_sar": 120.0,
                "orders": 2,
                "impressions": 300,
                "swipes": 15,
                "observed_days": 3,
                "data_complete": True,
            }],
        }

    monkeypatch.setattr(monitor._legacy, "_snapchat_accounts", accounts)
    monkeypatch.setattr(
        monitor._policy,
        "account_local_today",
        lambda _tz, now=None: date(2026, 8, 18),
    )
    monkeypatch.setattr(
        monitor._policy,
        "build_account_timezone_campaign_report",
        campaign_report,
    )
    monkeypatch.setattr(
        monitor._policy,
        "build_campaign_profitability",
        profitability,
    )
    monkeypatch.setattr(
        monitor._policy,
        "build_account_timezone_adsquad_report",
        group_report,
    )
    monkeypatch.setattr(
        monitor._policy,
        "build_account_timezone_ad_report",
        ad_report,
    )

    rows = asyncio.run(
        monitor._snapchat_child_entities(
            object(), "owner", date(2026, 8, 16), date(2026, 8, 18)
        )
    )
    assert seen == [
        ("campaign", "salla", "conversion"),
        ("group", "conversion"),
        ("ad", "conversion"),
    ]
    assert {row["entity_level"] for row in rows} == {"ad_group", "ad"}
    for row in rows:
        assert row["provider_result_source"] == (
            "snapchat_ads_manager_conversion_reporting"
        )
        assert row["salla_attribution_applied_to_entity_metrics"] is False
        assert row["commercial_context_scope"] == "parent_campaign_only"
        assert row["parent_campaign_salla_results"] == {
            "orders": 4,
            "sales_sar": 450.0,
        }
        assert row["parent_campaign_profitability"][
            "contribution_profit_sar"
        ] == 150.0
        assert row["parent_campaign_profitability"][
            "verified_against_page_salla"
        ] is True


def test_page_profit_is_suppressed_when_salla_page_cohort_disagrees():
    result = monitor._page_aligned_profitability(
        {
            "orders": 4,
            "sales_sar": 450.0,
            "product_cost_sar": 200.0,
            "contribution_profit_sar": 150.0,
            "cost_status": "complete",
        },
        {"orders": 3, "sales_sar": 300.0},
    )
    assert result["verified_against_page_salla"] is False
    assert result["contribution_profit_sar"] is None
    assert result["product_cost_sar"] is None
    assert result["cost_status"] == "page_window_mismatch"


class FakeCollection:
    def __init__(self):
        self.inserted = []
        self.updated = []

    async def insert_one(self, value):
        self.inserted.append(value)

    async def update_one(self, query, update, **_kwargs):
        self.updated.append((query, update))


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def test_openai_failure_never_creates_mezan_fallback_recommendations(monkeypatch):
    candidate = monitor._legacy._entity(
        provider="meta",
        level="campaign",
        entity_id="c-1",
        entity_name="Meta campaign",
        parent_name=None,
        status="ACTIVE",
        spend_sar=100,
        revenue_sar=300,
        purchases=3,
        impressions=1000,
        clicks=50,
        observed_days=3,
        data_complete=True,
        account_id="act-1",
        account_name="Meta",
    )

    async def campaign_entities(_db, _user, provider, _start, _end):
        return [candidate] if provider == "meta" else []

    async def empty(*_args, **_kwargs):
        return []

    async def no_meta_refresh(*_args, **_kwargs):
        return {}

    async def history(*_args, **_kwargs):
        return {}

    async def experiments(*_args, **_kwargs):
        return {
            "source": "owner_approved_executed_changes_only",
            "experiments": [],
        }

    async def profit(*_args, **_kwargs):
        return {"available": False}

    async def fail_openai(*_args, **_kwargs):
        raise RuntimeError("openai_api_key_missing")

    monkeypatch.setattr(monitor._policy, "_campaign_entities", campaign_entities)
    monkeypatch.setattr(monitor._policy, "_snapchat_child_entities", empty)
    monkeypatch.setattr(monitor._legacy, "_meta_child_entities", empty)
    monkeypatch.setattr(
        monitor._legacy,
        "_refresh_meta_entities",
        no_meta_refresh,
    )
    monkeypatch.setattr(monitor._legacy, "_campaign_history_context", history)
    monkeypatch.setattr(monitor._policy, "_experiment_outcomes_context", experiments)
    monkeypatch.setattr(monitor._legacy, "_business_profit_context", profit)
    monkeypatch.setattr(monitor._policy, "_ask_openai", fail_openai)

    db = FakeDB()
    result = asyncio.run(
        monitor.run_campaign_ai_monitor(
            db,
            "owner",
            now=lambda: datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        )
    )
    assert result["recommendation_source"] == "openai_unavailable"
    assert result["decision_authority"] == "openai_unavailable"
    assert result["recommendations"] == []
    assert result["source_contract"]["previous_mezan_recommendations_used"] is False
    assert result["source_contract"]["mezan_fallback_decisions_enabled"] is False
    assert "لم ينشئ ميزان أي توصية بديلة" in result["summary"]


def test_openai_payload_uses_executed_experiments_not_prior_mezan_recommendations(monkeypatch):
    captured = {}

    class Responses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status="completed",
                output_text=json.dumps({
                    "summary": "ok",
                    "recommendations": [],
                    "limitations": [],
                }),
            )

    class Client:
        def __init__(self, **_kwargs):
            self.responses = Responses()

        async def close(self):
            return None

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(monitor._legacy, "AsyncOpenAI", Client)
    asyncio.run(
        monitor._policy._ask_openai(
            [],
            now=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            campaign_history={},
            prior_decisions={
                "source": "owner_approved_executed_changes_only",
                "experiments": [],
            },
            business_profit={"available": False},
        )
    )
    payload = json.loads(captured["input"])
    assert "executed_experiments" in payload
    assert "prior_mezan_ai_decisions" not in payload
    assert (
        payload["source_contract"]["mezan_previous_recommendations_allowed"]
        is False
    )
    assert payload["source_contract"]["salla_child_attribution_allowed"] is False
