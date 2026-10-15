from __future__ import annotations

import pytest

from integrations_control_center import snapchat_decision_diagnostics as module


def _campaign(
    *,
    sales: float,
    cost: float,
    spend: float,
    units: float,
) -> dict:
    profit = sales - cost - spend
    return {
        "orders": int(units),
        "sales_sar": sales,
        "product_cost_sar": cost,
        "known_product_cost_sar": cost,
        "ad_spend_sar": spend,
        "contribution_profit_sar": profit,
        "roas": sales / spend if spend else None,
        "cpa_sar": spend / units if units else None,
        "cost_complete": True,
        "products": [
            {
                "identity": "product-1",
                "salla_product_id": "101",
                "name": "المنتج المعلن",
                "units": units,
                "sales_sar": sales,
            }
        ],
    }


def _evidence() -> dict:
    previous_campaign = _campaign(sales=1000, cost=500, spend=400, units=10)
    selected_campaign = _campaign(sales=900, cost=400, spend=200, units=9)
    previous_store = _campaign(sales=1500, cost=800, spend=400, units=15)
    selected_store = _campaign(sales=1400, cost=700, spend=200, units=14)
    source_previous = module._empty_source_breakdown()
    source_selected = module._empty_source_breakdown()
    source_previous["manual"] = {"orders": 3, "sales_sar": 300}
    source_selected["manual"] = {"orders": 5, "sales_sar": 500}
    source_selected["whatsapp_explicit"] = {"orders": 1, "sales_sar": 100}
    return {
        "account_ids": ["acc-1"],
        "campaigns": {
            "previous": {("acc-1", "campaign-1"): previous_campaign},
            "selected": {("acc-1", "campaign-1"): selected_campaign},
        },
        "accounts": {
            "previous": {
                "acc-1": module._aggregate_campaign_metrics([previous_campaign])
            },
            "selected": {
                "acc-1": module._aggregate_campaign_metrics([selected_campaign])
            },
        },
        "store": {"previous": previous_store, "selected": selected_store},
        "source_breakdown": {
            "previous": source_previous,
            "selected": source_selected,
        },
        "product_sources": {
            "previous": {},
            "selected": {
                "product-1": {
                    "snapchat": {"units": 9, "sales_sar": 900},
                    "manual": {"units": 4, "sales_sar": 400},
                    "whatsapp_explicit": {"units": 1, "sales_sar": 100},
                }
            },
        },
        "coverage": {
            "eligible_salla_orders": {"previous": 15, "selected": 14},
            "exact_campaign_orders": {"previous": 10, "selected": 9},
            "ambiguous_campaign_orders": {"previous": 0, "selected": 0},
            "unattributed_snapchat_orders": {"previous": 0, "selected": 0},
            "snapchat_campaign_performance_sync": {
                "complete": True,
                "accounts": {
                    "acc-1": {
                        "complete": True,
                        "windows": {
                            "previous": {"complete": True},
                            "selected": {"complete": True},
                        },
                    }
                },
            },
        },
    }


def _decision(*, after_budget: int = 80, reason: str = "") -> dict:
    return {
        "decision_id": "decision-1",
        "account_id": "acc-1",
        "entity_type": "campaign",
        "entity_id": "campaign-1",
        "action": "campaign.update",
        "effective_at": "2026-08-05T08:00:00+00:00",
        "field_diffs": [
            {"field": "daily_budget", "before": 100, "after": after_budget}
        ],
        "reason": reason,
        "annotations": [],
    }


def test_periods_are_inclusive_and_previous_is_equal_length():
    periods = module._periods("2026-08-05", "2026-08-11")

    assert periods["selected"] == {
        "date_from": "2026-08-05",
        "date_to": "2026-08-11",
        "days": 7,
    }
    assert periods["previous"] == {
        "date_from": "2026-07-29",
        "date_to": "2026-08-04",
        "days": 7,
    }
    assert periods["decision_window"]["date_from"] == "2026-08-02"


def test_manual_whatsapp_and_other_platform_sources_never_collapse():
    assert module._source_bucket("manual") == "manual"
    assert module._source_bucket("whatsapp") == "whatsapp_explicit"
    assert module._source_bucket("meta") == "other_ad_platforms"
    assert module._source_bucket(None) == "unknown"


@pytest.mark.asyncio
async def test_diagnostic_links_exact_campaign_and_product_change_without_causality(
    monkeypatch,
):
    async def fake_evidence(*args, **kwargs):
        return _evidence()

    async def fake_decisions(*args, **kwargs):
        decision = _decision(after_budget=80, reason="ربما بسبب الراتب")
        decision["effective_at"] = "2026-07-28T08:00:00+00:00"
        return [decision]

    async def fake_campaign_id(*args, **kwargs):
        return "campaign-1"

    monkeypatch.setattr(module, "_load_comparison_evidence", fake_evidence)
    monkeypatch.setattr(module, "_load_decisions", fake_decisions)
    monkeypatch.setattr(module, "resolve_decision_campaign_id", fake_campaign_id)

    result = await module.diagnose_ad_business_change(
        object(),
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-11",
        account_id="acc-1",
    )

    assert result["read_only"] is True
    assert result["headline"]["direction"] == "down"
    assert result["headline"]["sales_fell_but_contribution_profit_rose"] is True
    row = result["decisions"][0]
    assert row["classification"] == "likely_contributor"
    assert row["measurement_scope"] == "campaign_exact"
    assert row["association_not_causation"] is True
    assert row["measured_change"]["delta"] == -100
    assert (
        row["product_evidence"][0]["campaign_exact_attribution"]["units"]["delta"] == -1
    )
    assert row["product_evidence"][0]["whole_store_product"]["units"]["delta"] == -1
    assert row["product_evidence"][0]["selected_period_source_units"]["manual"] == 4
    assert row["unverified_context_not_used"][0]["category"] == "salary_or_payday"
    assert (
        result["store_order_sources"]["manual"]["changes"]["sales_sar"]["delta"] == 200
    )
    assert result["store_order_sources"]["whatsapp_explicit"]["selected"]["orders"] == 1
    assert "never_labelled_whatsapp" in result["coverage"]["manual_source_policy"]


@pytest.mark.asyncio
async def test_opposite_direction_is_contradictory_and_not_ranked_likely(monkeypatch):
    async def fake_evidence(*args, **kwargs):
        return _evidence()

    async def fake_decisions(*args, **kwargs):
        decision = _decision(after_budget=150)
        decision["effective_at"] = "2026-07-28T08:00:00+00:00"
        return [decision]

    async def fake_campaign_id(*args, **kwargs):
        return "campaign-1"

    monkeypatch.setattr(module, "_load_comparison_evidence", fake_evidence)
    monkeypatch.setattr(module, "_load_decisions", fake_decisions)
    monkeypatch.setattr(module, "resolve_decision_campaign_id", fake_campaign_id)

    result = await module.diagnose_ad_business_change(
        object(), "owner-1", date_from="2026-08-05", date_to="2026-08-11"
    )

    assert result["decisions"][0]["classification"] == "contradictory"
    assert result["likely_contributors"] == []


def test_profit_metric_is_insufficient_when_mezan_cost_is_missing():
    classification, confidence, caveats = module._classify_association(
        expected_direction="down",
        measured=module._metric_change(100, 50),
        scope="campaign_exact",
        timing="inside_selected_period",
        cost_complete=False,
    )

    assert classification == "insufficient"
    assert confidence == 0.25
    assert "mezan_product_cost_incomplete" in caveats


def test_budget_change_does_not_invent_contribution_profit_direction():
    direction, basis = module._decision_expected_direction(
        _decision(after_budget=80), "contribution_profit_sar"
    )

    assert direction is None
    assert basis == "profit_direction_not_derivable_from_ad_setting_change"


@pytest.mark.parametrize("status", ["INACTIVE", "NOT_ACTIVE", "DISABLED", "STOPPED"])
def test_non_active_status_tokens_are_never_misread_as_activation(status):
    direction, basis = module._decision_expected_direction(
        {
            "field_diffs": [
                {"field": "status", "before": "ACTIVE", "after": status}
            ]
        },
        "sales_sar",
    )

    assert direction == "down"
    assert basis == "status_disabled"


@pytest.mark.parametrize("action", ["campaign.create", "campaign.delete"])
def test_paused_create_or_delete_action_does_not_invent_delivery_direction(action):
    direction, basis = module._decision_expected_direction(
        {
            "action": action,
            "after": {"status": "PAUSED"},
            "field_diffs": [],
        },
        "sales_sar",
    )

    assert direction is None
    assert basis == "direction_not_derivable_from_ledger_change"


@pytest.mark.parametrize("metric", ["roas", "cpa_sar"])
def test_budget_change_does_not_invent_efficiency_direction(metric):
    direction, basis = module._decision_expected_direction(
        _decision(after_budget=150), metric
    )

    assert direction is None
    assert basis == "efficiency_direction_not_derivable_from_ad_setting_change"


@pytest.mark.asyncio
async def test_decision_inside_selected_period_is_not_ranked_as_contributor(
    monkeypatch,
):
    async def fake_evidence(*args, **kwargs):
        return _evidence()

    async def fake_decisions(*args, **kwargs):
        decision = _decision(after_budget=80)
        decision["effective_at"] = "2026-08-11T20:00:00+00:00"
        return [decision]

    async def fake_campaign_id(*args, **kwargs):
        return "campaign-1"

    monkeypatch.setattr(module, "_load_comparison_evidence", fake_evidence)
    monkeypatch.setattr(module, "_load_decisions", fake_decisions)
    monkeypatch.setattr(module, "resolve_decision_campaign_id", fake_campaign_id)

    result = await module.diagnose_ad_business_change(
        object(),
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-11",
        account_id="acc-1",
    )

    row = result["decisions"][0]
    assert row["classification"] == "insufficient"
    assert "selected_period_contains_pre_decision_results" in row["caveats"]


@pytest.mark.asyncio
async def test_decision_inside_previous_period_does_not_contaminate_baseline(
    monkeypatch,
):
    async def fake_evidence(*args, **kwargs):
        return _evidence()

    async def fake_decisions(*args, **kwargs):
        decision = _decision(after_budget=80)
        decision["effective_at"] = "2026-08-06T08:00:00+00:00"
        return [decision]

    async def fake_campaign_id(*args, **kwargs):
        return "campaign-1"

    monkeypatch.setattr(module, "_load_comparison_evidence", fake_evidence)
    monkeypatch.setattr(module, "_load_decisions", fake_decisions)
    monkeypatch.setattr(module, "resolve_decision_campaign_id", fake_campaign_id)

    result = await module.diagnose_ad_business_change(
        object(),
        "owner-1",
        date_from="2026-08-08",
        date_to="2026-08-10",
        account_id="acc-1",
    )

    row = result["decisions"][0]
    assert row["timing"] == "inside_previous_period"
    assert row["classification"] == "insufficient"
    assert "baseline_contains_post_decision_results" in row["caveats"]
    assert result["likely_contributors"] == []


def test_one_verified_context_does_not_verify_a_separate_user_suggestion():
    decision = _decision(reason="اقتراح متعلق بالراتب")
    decision["evidence"] = {
        "decision_evidence": [
            {
                "kind": "trend",
                "value": "تحسن آخر 3 أيام",
                "source": "mezan metrics",
                "verification_status": "verified",
                "used_in_decision": True,
            },
            {
                "kind": "salary payday",
                "value": "ربما نزل الراتب",
                "source": "user suggestion",
                "verification_status": "user_suggestion",
                "used_in_decision": False,
            },
        ]
    }

    verified, unverified = module._context_evidence(decision)

    assert {row["category"] for row in verified} == {"trend"}
    assert {row["category"] for row in unverified} == {"salary_or_payday"}


@pytest.mark.asyncio
async def test_roas_is_supported_as_a_measured_diagnostic(monkeypatch):
    async def fake_evidence(*args, **kwargs):
        return _evidence()

    async def fake_decisions(*args, **kwargs):
        return [_decision(after_budget=80)]

    async def fake_campaign_id(*args, **kwargs):
        return "campaign-1"

    monkeypatch.setattr(module, "_load_comparison_evidence", fake_evidence)
    monkeypatch.setattr(module, "_load_decisions", fake_decisions)
    monkeypatch.setattr(module, "resolve_decision_campaign_id", fake_campaign_id)

    result = await module.diagnose_ad_business_change(
        object(),
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-11",
        metric="roas",
        account_id="acc-1",
    )

    assert result["headline"]["previous"] == 2.5
    assert result["headline"]["selected"] == 4.5
    assert result["headline"]["direction"] == "up"


@pytest.mark.parametrize(
    "metric",
    ["ad_spend_sar", "contribution_profit_sar", "roas", "cpa_sar"],
)
@pytest.mark.asyncio
async def test_spend_dependent_diagnostic_fails_closed_without_both_window_proofs(
    monkeypatch,
    metric,
):
    evidence = _evidence()
    account_proof = evidence["coverage"]["snapchat_campaign_performance_sync"][
        "accounts"
    ]["acc-1"]
    account_proof["complete"] = False
    account_proof["windows"]["selected"]["complete"] = False

    async def fake_evidence(*args, **kwargs):
        return evidence

    async def fake_decisions(*args, **kwargs):
        decision = _decision(after_budget=80)
        decision["effective_at"] = "2026-07-28T08:00:00+00:00"
        return [decision]

    async def fake_campaign_id(*args, **kwargs):
        return "campaign-1"

    monkeypatch.setattr(module, "_load_comparison_evidence", fake_evidence)
    monkeypatch.setattr(module, "_load_decisions", fake_decisions)
    monkeypatch.setattr(module, "resolve_decision_campaign_id", fake_campaign_id)

    result = await module.diagnose_ad_business_change(
        object(),
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-11",
        metric=metric,
        account_id="acc-1",
    )

    row = result["decisions"][0]
    assert row["classification"] == "insufficient"
    assert row["measured_change"] == {
        "previous": None,
        "selected": None,
        "delta": None,
        "delta_pct": None,
        "direction": "unknown",
    }
    assert result["likely_contributors"] == []
    assert result["headline"]["data_complete"] is False
    assert result["headline"]["delta"] is None
    assert result["headline"]["direction"] == "unknown"
    assert (
        "snapchat_campaign_performance_sync_incomplete_for_previous_and_selected_windows"
        in row["caveats"]
    )
    assert (
        "snapchat_campaign_performance_sync_incomplete_requested_metric_not_measurable"
        in result["caveats"]
    )


@pytest.mark.parametrize("metric", ["sales_sar", "orders"])
@pytest.mark.asyncio
async def test_salla_sales_and_orders_remain_independent_of_campaign_fact_proof(
    monkeypatch,
    metric,
):
    evidence = _evidence()
    account_proof = evidence["coverage"]["snapchat_campaign_performance_sync"][
        "accounts"
    ]["acc-1"]
    account_proof["complete"] = False
    account_proof["windows"]["selected"]["complete"] = False

    async def fake_evidence(*args, **kwargs):
        return evidence

    async def fake_decisions(*args, **kwargs):
        decision = _decision(after_budget=80)
        decision["effective_at"] = "2026-07-28T08:00:00+00:00"
        return [decision]

    async def fake_campaign_id(*args, **kwargs):
        return "campaign-1"

    monkeypatch.setattr(module, "_load_comparison_evidence", fake_evidence)
    monkeypatch.setattr(module, "_load_decisions", fake_decisions)
    monkeypatch.setattr(module, "resolve_decision_campaign_id", fake_campaign_id)

    result = await module.diagnose_ad_business_change(
        object(),
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-11",
        metric=metric,
        account_id="acc-1",
    )

    assert result["decisions"][0]["classification"] == "likely_contributor"
    assert result["headline"]["data_complete"] is True
    assert result["headline"]["delta"] is not None
