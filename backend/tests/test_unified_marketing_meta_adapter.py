from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from decision_intelligence import evidence_adapter
from decision_intelligence.evidence_adapter import (
    evaluate_decision_evidence,
    load_decision_evidence,
)
from decision_intelligence.phase5 import run_phase5_shadow_from_evidence
from snapchat_v2.salla_outcomes import _match_order_campaign
from unified_marketing.adapters.meta_v2 import build_meta_v2_unified_report
from unified_marketing.adapters.snapchat_v2 import build_snapchat_v2_unified_report
from unified_marketing.contract import CONTRACT_VERSION
from unified_marketing.meta_shadow import evaluate_meta_unified_readiness

DAY = date(2026, 8, 25)
NOW = datetime(2026, 8, 27, 1, tzinfo=timezone.utc)
LAST_SYNC = datetime(2026, 8, 26, 22, tzinfo=timezone.utc)


def _account() -> dict:
    return {
        "ad_account_id": "act_42",
        "display_name": "Meta Store",
        "currency": "USD",
        "timezone": "Asia/Riyadh",
        "last_sync_at": LAST_SYNC,
        "account_status": "1",
        "active": True,
    }


def _period() -> dict:
    return {
        "date_from": DAY.isoformat(),
        "date_to": DAY.isoformat(),
        "timezone": "Asia/Riyadh",
        "action_report_time": "conversion",
    }


def _native_row(level: str, entity_id: str) -> dict:
    provider_level = {"account": "ad_account", "ad_group": "adset"}.get(level, level)
    return {
        "entity_type": provider_level,
        "external_id": entity_id,
        "name": f"{level}-{entity_id}",
        "campaign_id": "cmp-1" if level != "account" else None,
        "ad_group_id": "set-1" if level == "ad" else None,
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
        "active": True,
        "daily_budget_native": 100.0 if level in {"campaign", "ad_group"} else None,
        "lifetime_budget_native": None,
        "bid_amount_native": 7.0 if level == "ad_group" else None,
        "bid_strategy": "LOWEST_COST_WITH_BID_CAP" if level == "ad_group" else None,
        "billing_event": "IMPRESSIONS" if level == "ad_group" else None,
        "optimization_goal": "OFFSITE_CONVERSIONS" if level == "ad_group" else None,
        "settings_fields_present": {
            "account": ["account_status"],
            "campaign": [
                "status",
                "effective_status",
                "daily_budget",
                "lifetime_budget",
            ],
            "ad_group": [
                "status",
                "effective_status",
                "daily_budget",
                "lifetime_budget",
                "bid_amount",
                "bid_strategy",
                "billing_event",
                "optimization_goal",
            ],
            "ad": ["status", "effective_status"],
        }[level],
        "source_fact_count": 1,
        "performance_sync_status": "complete",
        "amount_complete": True,
        "reconciliation_status": "reconciled",
        "spend_native": 100.0,
        "spend_sar": 375.0,
        "impressions": 10_000,
        "clicks": 500,
        "purchases": 10,
        "purchase_value_native": 900.0,
        "source_collection": "mezan_meta_entity_performance_daily_v2",
        "observed_dates": [DAY.isoformat()],
        "expected_dates": [DAY.isoformat()],
        "salla_results": {
            "status": "complete" if level in {"account", "campaign"} else "unavailable",
            "orders": 8 if level in {"account", "campaign"} else None,
            "sales_sar": 2400.0 if level in {"account", "campaign"} else None,
            "roas": 6.4 if level in {"account", "campaign"} else None,
            "attribution_scope": "exact_campaign_match",
            "profitability": {
                "status": "complete",
                "orders": 8,
                "sales_sar": 2400.0,
                "product_cost_sar": 1200.0,
                "known_product_cost_sar": 1200.0,
                "ad_spend_sar": 375.0,
                "contribution_profit_sar": 825.0,
                "profit_margin_pct": 34.375,
                "cost_status": "complete",
                "missing_cost_orders": 0,
                "product_count": 1,
                "products": [],
                "profit_scope": "exact_campaign_match",
                "allocation_method": "exact_order_product",
            },
        },
    }


def _order_summary() -> dict:
    return {
        "coverage_status": "complete",
        "total_salla_created_orders": 9,
        "total_financial_orders": 8,
        "total_financial_sales_sar": 2400.0,
        "campaign_matched_orders": 8,
        "campaign_matched_financial_orders": 8,
        "campaign_matched_financial_sales_sar": 2400.0,
        "non_campaign_orders": 1,
        "ambiguous_orders": 0,
        "platform_attributed_purchases": 10,
        "platform_minus_confirmed_campaign_orders": 2,
        "campaign_attribution_policy": "exact_meta_campaign_id_or_unique_name",
        "date_timezone": "Asia/Riyadh",
        "orders_total": 9,
        "orders_returned": 9,
        "truncated": False,
    }


def _report(level: str) -> dict:
    entity_id = {
        "account": "act_42",
        "campaign": "cmp-1",
        "ad_group": "set-1",
        "ad": "ad-1",
    }[level]
    row = _native_row(level, entity_id)
    return build_meta_v2_unified_report(
        account_value=_account(),
        period_value=_period(),
        entity_type=row["entity_type"],
        rows=[row],
        totals=deepcopy(row),
        sync_status="complete",
        order_summary=_order_summary() if level == "account" else {},
    )


def _reports() -> dict[str, dict]:
    return {
        level: _report(level) for level in ("account", "campaign", "ad_group", "ad")
    }


def test_meta_adapter_contract_parity_with_snapchat_contract():
    meta = _report("campaign")
    snap_row = {
        **_native_row("campaign", "cmp-1"),
        "ad_account_id": "act_42",
        "swipes": 500,
        "video_views": 0,
        "view_content": 0,
        "add_to_cart": 0,
        "start_checkout": 0,
        "add_billing": 0,
    }
    snap = build_snapchat_v2_unified_report(
        account_value=_account(),
        period_value=_period(),
        entity_type="campaign",
        rows=[snap_row],
        totals=snap_row,
        sync_status="complete",
    )

    assert meta["contract_version"] == snap["contract_version"] == CONTRACT_VERSION
    assert set(meta) >= set(snap)
    assert set(meta["rows"][0]) == set(snap["rows"][0])
    assert meta["provider"] == "meta_ads"
    assert meta["rows"][0]["entity"]["provider_level"] == "campaign"


def test_meta_identity_hierarchy_and_settings_are_deterministic():
    reports = _reports()
    campaign = reports["campaign"]["rows"][0]
    ad_group = reports["ad_group"]["rows"][0]
    ad = reports["ad"]["rows"][0]

    assert campaign["account"]["id"] == "act_42"
    assert campaign["entity"]["campaign_id"] == "cmp-1"
    assert ad_group["entity"]["campaign_id"] == "cmp-1"
    assert ad_group["entity"]["ad_group_id"] == "set-1"
    assert ad["entity"]["campaign_id"] == "cmp-1"
    assert ad["entity"]["ad_group_id"] == "set-1"
    assert reports["ad_group"]["management_context"]["set-1"] == {
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
        "active": True,
        "campaign_id": "cmp-1",
        "ad_group_id": "set-1",
        "daily_budget_native": 100.0,
        "lifetime_budget_native": None,
        "bid_amount_native": 7.0,
        "bid_strategy": "LOWEST_COST_WITH_BID_CAP",
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "currency_scope": "account_native",
        "settings_evidence_status": "complete",
        "source": "mezan_meta_entity_snapshots_v2",
    }


def test_meta_reuses_snapchat_exact_salla_attribution_semantics():
    meta = _match_order_campaign(
        {"source": "Meta Ads", "utm_campaign": "Launch Campaign"},
        id_lookup={},
        name_lookup={"launch campaign": ("act-meta", "cmp-meta")},
        provider_key="meta",
    )
    snapchat = _match_order_campaign(
        {"source": "Snapchat Ads", "utm_campaign": "Launch Campaign"},
        id_lookup={},
        name_lookup={"launch campaign": ("act-snap", "cmp-snap")},
        provider_key="snapchat",
    )

    assert meta == (("act-meta", "cmp-meta"), "campaign_name")
    assert snapchat == (("act-snap", "cmp-snap"), "campaign_name")
    foreign = _match_order_campaign(
        {"source": "Snapchat Ads", "utm_campaign": "Launch Campaign"},
        id_lookup={},
        name_lookup={"launch campaign": ("act-meta", "cmp-meta")},
        provider_key="meta",
    )
    assert foreign == (None, "foreign_platform")


def test_meta_unified_reader_has_no_provider_client_or_write_primitives():
    backend = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        (backend / relative).read_text(encoding="utf-8")
        for relative in (
            "unified_marketing/readers/meta_v2.py",
            "unified_marketing/adapters/meta_v2.py",
            "unified_marketing/meta_shadow.py",
            "unified_marketing/gateway.py",
        )
    )
    for forbidden in (
        "import httpx",
        "AsyncClient",
        "meta_graph_base",
        ".insert_one(",
        ".insert_many(",
        ".update_one(",
        ".delete_many(",
        ".replace_one(",
        ".bulk_write(",
    ):
        assert forbidden not in sources


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing_day", "daily_coverage_incomplete"),
        ("unreconciled", "provider_reconciliation_incomplete"),
        ("settings", "settings_evidence_incomplete"),
        ("attribution", "salla_attribution_incomplete"),
        ("profitability", "profitability_incomplete"),
        ("hierarchy", "hierarchy_incomplete"),
    ],
)
def test_meta_readiness_fails_closed(mutation, reason):
    reports = _reports()
    if mutation == "missing_day":
        reports["ad"]["native_evidence"]["observed_dates"] = []
    elif mutation == "unreconciled":
        reports["account"]["totals"]["quality"]["reconciliation_status"] = "partial"
    elif mutation == "settings":
        reports["ad_group"]["management_context"]["set-1"][
            "settings_evidence_status"
        ] = "partial"
    elif mutation == "attribution":
        reports["account"]["order_summary"]["ambiguous_orders"] = 1
    elif mutation == "profitability":
        reports["account"]["totals"]["commerce_profitability"]["status"] = "partial"
    else:
        reports["ad"]["rows"][0]["entity"]["ad_group_id"] = "missing-set"

    result = evaluate_meta_unified_readiness(
        account_identity={**_account(), "id": "act_42", "last_sync_at": LAST_SYNC},
        reports=reports,
        date_from=DAY,
        date_to=DAY,
        now=NOW,
    )

    assert result["ready"] is False
    assert reason in result["reasons"]


def test_meta_native_vs_unified_shadow_and_phase5_accept_only_after_readiness():
    reports = _reports()
    identity = {**_account(), "id": "act_42", "last_sync_at": LAST_SYNC}
    readiness = evaluate_meta_unified_readiness(
        account_identity=identity,
        reports=reports,
        date_from=DAY,
        date_to=DAY,
        now=NOW,
    )
    assert readiness["ready"] is True
    assert readiness["shadow_comparison"]["passed"] is True
    assert readiness["shadow_comparison"]["mismatches"] == []

    evidence = evaluate_decision_evidence(
        account_identity=identity,
        reports=reports,
        provider="meta_ads",
        date_from=DAY,
        date_to=DAY,
        now=NOW,
        shadow_acceptance=readiness,
    )
    result = run_phase5_shadow_from_evidence(evidence)

    assert evidence["decision_ready"] is True
    assert evidence["gates"]["shadow_acceptance"]["passed"] is True
    assert result["mode"] == "recommendation_shadow"
    assert result["summary"]["recommendations"] == 1
    assert result["approval_workflow"]["approval_can_execute"] is False
    assert result["scheduler_integration"]["automatic_execution_connected"] is False
    assert result["write_policy"] == {
        "platform_writes_enabled": False,
        "platform_writes_performed": False,
        "database_writes_performed": False,
    }

    readiness["ready"] = False
    readiness["reasons"] = ["shadow_mismatch"]
    blocked = evaluate_decision_evidence(
        account_identity=identity,
        reports=reports,
        provider="meta_ads",
        date_from=DAY,
        date_to=DAY,
        now=NOW,
        shadow_acceptance=readiness,
    )
    assert blocked["decision_ready"] is False
    assert "shadow_acceptance" in blocked["blocked_by"]


def test_meta_readiness_uses_account_timezone_for_closed_window_and_staleness():
    riyadh_reports = _reports()
    riyadh_identity = {
        **_account(),
        "id": "act_42",
        "last_sync_at": datetime(2026, 8, 25, 21, 30, tzinfo=timezone.utc),
    }
    riyadh = evaluate_meta_unified_readiness(
        account_identity=riyadh_identity,
        reports=riyadh_reports,
        date_from=DAY,
        date_to=DAY,
        now=datetime(2026, 8, 25, 22, 0, tzinfo=timezone.utc),
    )
    assert riyadh["period"]["closed"] is True
    assert riyadh["ready"] is True

    los_angeles_reports = deepcopy(riyadh_reports)
    for report in los_angeles_reports.values():
        report["account"]["timezone"] = "America/Los_Angeles"
        report["period"]["timezone"] = "America/Los_Angeles"
        report["native_evidence"]["timezone"] = "America/Los_Angeles"
        report["totals"]["account"]["timezone"] = "America/Los_Angeles"
        for row in report["rows"]:
            row["account"]["timezone"] = "America/Los_Angeles"
            row["period"]["timezone"] = "America/Los_Angeles"
    los_angeles = evaluate_meta_unified_readiness(
        account_identity={**riyadh_identity, "timezone": "America/Los_Angeles"},
        reports=los_angeles_reports,
        date_from=DAY,
        date_to=DAY,
        now=datetime(2026, 8, 25, 22, 0, tzinfo=timezone.utc),
    )
    assert los_angeles["period"]["closed"] is False
    assert "period_is_not_closed" in los_angeles["reasons"]

    stale = evaluate_meta_unified_readiness(
        account_identity=riyadh_identity,
        reports=riyadh_reports,
        date_from=DAY,
        date_to=DAY,
        now=datetime(2026, 8, 28, 22, 0, tzinfo=timezone.utc),
    )
    assert stale["ready"] is False
    assert "freshness_incomplete" in stale["reasons"]


@pytest.mark.asyncio
async def test_meta_evidence_loader_accepts_shadow_only_after_five_gateway_reads(
    monkeypatch,
):
    reports = _reports()
    calls: list[str] = []

    async def fake_identity(db, user_id, *, provider):
        del db, user_id
        calls.append("identity")
        assert provider == "meta_ads"
        return {**_account(), "id": "act_42", "last_sync_at": LAST_SYNC}

    async def fake_account(db, user_id, **kwargs):
        del db, user_id
        calls.append("account")
        assert kwargs["provider"] == "meta_ads"
        return deepcopy(reports["account"])

    async def fake_entity(db, user_id, **kwargs):
        del db, user_id
        level = kwargs["entity_level"]
        calls.append(level)
        assert kwargs["provider"] == "meta_ads"
        assert kwargs["include_stale"] is False
        return deepcopy(reports[level])

    monkeypatch.setattr(
        evidence_adapter.unified_gateway,
        "load_unified_marketing_account_identity",
        fake_identity,
    )
    monkeypatch.setattr(
        evidence_adapter.unified_gateway,
        "load_unified_marketing_account_report",
        fake_account,
    )
    monkeypatch.setattr(
        evidence_adapter.unified_gateway,
        "load_unified_marketing_entity_report",
        fake_entity,
    )

    evidence = await load_decision_evidence(
        object(),
        "owner-1",
        provider="meta_ads",
        date_from=DAY,
        date_to=DAY,
        now=NOW,
    )
    phase5 = run_phase5_shadow_from_evidence(evidence)

    assert calls == ["identity", "account", "campaign", "ad_group", "ad"]
    assert evidence["gates"]["shadow_acceptance"]["passed"] is True
    assert evidence["decision_ready"] is True
    assert phase5["mode"] == "recommendation_shadow"
    assert phase5["approval_workflow"]["approval_can_execute"] is False
    assert phase5["scheduler_integration"]["automatic_execution_connected"] is False
