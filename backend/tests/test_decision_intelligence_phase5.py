from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import decision_intelligence.evidence_adapter as adapter
from decision_intelligence.phase5 import run_phase5_shadow_from_evidence
from unified_marketing.contract import CONTRACT_VERSION


PERIOD_DAY = date(2026, 8, 25)
NOW = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)
LAST_SYNC = datetime(2026, 8, 27, 0, 30, tzinfo=timezone.utc)


def _money(amount: float | None, currency: str) -> dict:
    return {"amount": amount, "currency": currency}


def _row(level: str, entity_id: str, *, name: str | None = None) -> dict:
    return {
        "provider": "snapchat_ads",
        "account": {
            "id": "account-1",
            "name": "Unified Account",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        },
        "period": {
            "date_from": PERIOD_DAY.isoformat(),
            "date_to": PERIOD_DAY.isoformat(),
            "timezone": "America/Los_Angeles",
            "action_report_time": "conversion",
        },
        "entity": {
            "level": level,
            "provider_level": "ad_squad" if level == "ad_group" else level,
            "id": entity_id,
            "name": name or entity_id,
            "status": "ACTIVE",
            "active": True,
            "campaign_id": "campaign-1" if level != "account" else None,
            "ad_group_id": "ad-group-1" if level == "ad" else None,
        },
        "delivery": {
            "spend": _money(100.0, "USD"),
            "spend_sar": _money(375.0, "SAR"),
            "impressions": 10000,
            "clicks": 500,
            "views": 3000,
            "ctr_pct": 5.0,
            "reach": 8000,
            "frequency": 1.25,
            "frequency_scope": "entity_period",
            "video_completion": 0.35,
        },
        "platform_outcomes": {
            "conversions": 10,
            "revenue": _money(900.0, "USD"),
            "roas": 9.0,
            "view_content": 300,
            "add_to_cart": 50,
            "start_checkout": 20,
            "add_billing": 12,
        },
        "commerce_outcomes": {
            "status": "complete",
            "orders": 8,
            "revenue": _money(2400.0, "SAR"),
            "roas": 6.4,
            "attribution_scope": "exact_campaign_match",
        },
        "commerce_profitability": {
            "status": "complete",
            "orders": 8,
            "sales": _money(2400.0, "SAR"),
            "product_cost": _money(1200.0, "SAR"),
            "known_product_cost": _money(1200.0, "SAR"),
            "ad_spend": _money(375.0, "SAR"),
            "contribution_profit": _money(825.0, "SAR"),
            "profit_margin_pct": 34.375,
            "cost_status": "complete",
            "missing_cost_orders": 0,
            "product_count": 2,
            "products": [],
            "profit_scope": "exact_campaign_match",
            "allocation_method": "exact_order_product",
        },
        "abandoned_cart_outcomes": {
            "status": "complete",
            "scope": "account_period",
            "cart_snapshots": 4,
            "abandoned_carts": 2,
            "recovered_carts": 1,
            "abandoned_value": _money(200.0, "SAR"),
            "top_products": [],
            "is_campaign_attributed": False,
            "causality_guard": "descriptive_only",
        },
        "quality": {
            "sync_status": "complete",
            "coverage_status": "complete",
            "source_fact_count": 24,
            "amount_complete": True,
            "reconciliation_status": "reconciled",
            "reason": None,
        },
        "lineage": {
            "adapter": "provider-adapter-v2",
            "source_version": "v2",
            "source_collection": "provider_internal_facts",
            "provider_metric_mapping": {"spend": "spend"},
        },
    }


def _report(level: str) -> dict:
    row_id = {
        "account": "account-1",
        "campaign": "campaign-1",
        "ad_group": "ad-group-1",
        "ad": "ad-1",
    }[level]
    row = _row(level, row_id, name=f"{level} evidence")
    return {
        "contract_version": CONTRACT_VERSION,
        "provider": "snapchat_ads",
        "entity_level": level,
        "account": row["account"],
        "period": row["period"],
        "totals": row,
        "rows": [row],
        "orders": [],
        "order_summary": {
            "status": "complete",
            "source": "commerce_adapter",
            "created_orders": 9,
            "financial_orders": 8,
            "financial_revenue": _money(2400.0, "SAR"),
            "matched_orders": 8,
            "matched_financial_orders": 8,
            "matched_financial_revenue": _money(2400.0, "SAR"),
            "unmatched_orders": 1,
            "ambiguous_orders": 0,
            "platform_attributed_conversions": 10,
            "platform_minus_matched_financial_orders": 2,
            "attribution_policy": "exact_campaign_match",
            "timezone": "America/Los_Angeles",
            "orders_total": 9,
            "orders_returned": 0,
            "truncated": False,
            "reason": None,
        },
        "management_context": {
            row_id: {
                "status": "ACTIVE",
                "active": True,
                "daily_budget_native": 100.0 if level != "ad" else None,
                "currency_scope": "account_native",
            }
        },
        "decision_eligibility": {
            "eligible": False,
            "reason": "recommendation_shadow_only",
        },
    }


def _bundle_inputs() -> tuple[dict, dict[str, dict]]:
    identity = {
        "provider": "snapchat_ads",
        "id": "account-1",
        "name": "Unified Account",
        "currency": "USD",
        "timezone": "America/Los_Angeles",
        "last_sync_at": LAST_SYNC,
    }
    return identity, {level: _report(level) for level in ("account", "campaign", "ad_group", "ad")}


def _evidence(identity: dict | None = None, reports: dict | None = None) -> dict:
    default_identity, default_reports = _bundle_inputs()
    return adapter.evaluate_decision_evidence(
        account_identity=identity or default_identity,
        reports=reports or default_reports,
        provider="snapchat_ads",
        date_from=PERIOD_DAY,
        date_to=PERIOD_DAY,
        now=NOW,
    )


def test_closed_reconciled_day_builds_shadow_recommendation_chain():
    evidence = _evidence()
    assert evidence["contract_version"] == CONTRACT_VERSION
    assert evidence["period"]["date_from"] == "2026-08-25"
    assert evidence["decision_ready"] is True
    assert all(gate["passed"] for gate in evidence["gates"].values())
    assert evidence["source"] == {
        "reader": "unified_marketing.gateway",
        "contract_only": True,
    }

    result = run_phase5_shadow_from_evidence(evidence)
    assert result["mode"] == "recommendation_shadow"
    assert result["summary"] == {
        "candidates_evaluated": 1,
        "recommendations": 1,
        "blocked": 0,
        "blocked_reasons": [],
    }
    decision = result["decisions"][0]
    assert decision["status"] == "RECOMMENDATION_SHADOW"
    assert decision["recommendation"]["action"] == "TEST"
    assert decision["recommendation"]["confidence"] is None
    assert decision["recommendation"]["expected_profit_delta_sar"] is None
    assert decision["simulation"]["scenario"] == "bounded_budget_increase_5pct_shadow"
    assert decision["evidence"]["current_state_snapshot"] == {
        "status": "ACTIVE",
        "active": True,
        "daily_budget_native": 100.0,
        "currency_scope": "account_native",
    }
    assert decision["simulation"]["forecast_used"] is False
    assert decision["impact_prediction"] == {
        "status": "unknown",
        "expected_profit_delta_sar": None,
        "downside_sar": None,
        "upside_sar": None,
        "confidence": None,
        "evidence_basis": None,
        "reason": "measured_elasticity_experiment_or_validated_model_evidence_unavailable",
        "evidence_required": [
            "measured_elasticity",
            "controlled_experiment",
            "validated_model_output",
        ],
    }
    assert result["lineage"]["reader"] == "unified_marketing.gateway"
    assert result["lineage"]["contract_version"] == CONTRACT_VERSION
    assert result["lineage"]["entities"][0]["source"]["source_version"] == "v2"
    assert decision["approval"]["state"] == "PENDING"
    assert decision["approval"]["execution_performed"] is False
    assert decision["execution_allowed"] is False
    assert result["write_policy"]["platform_writes_performed"] is False


@pytest.mark.parametrize(
    ("gate", "mutate"),
    [
        (
            "coverage",
            lambda identity, reports: reports["campaign"]["totals"]["quality"].update(
                {"coverage_status": "partial"}
            ),
        ),
        (
            "reconciliation",
            lambda identity, reports: reports["account"]["totals"]["quality"].update(
                {"reconciliation_status": "pending"}
            ),
        ),
        (
            "freshness",
            lambda identity, reports: identity.update(
                {"last_sync_at": datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)}
            ),
        ),
        (
            "attribution",
            lambda identity, reports: reports["account"]["order_summary"].update(
                {"truncated": True}
            ),
        ),
        (
            "financial_coverage",
            lambda identity, reports: reports["account"]["totals"][
                "commerce_profitability"
            ].update({"status": "partial", "missing_cost_orders": 1}),
        ),
    ],
)
def test_required_quality_gate_blocks_decision(gate, mutate):
    identity, reports = _bundle_inputs()
    mutate(identity, reports)
    evidence = _evidence(identity, reports)
    assert evidence["decision_ready"] is False
    assert evidence["gates"][gate]["passed"] is False
    assert gate in evidence["blocked_by"]

    result = run_phase5_shadow_from_evidence(evidence)
    assert result["summary"]["recommendations"] == 0
    assert result["summary"]["blocked"] == 1
    assert gate in result["decisions"][0]["blocked_by"]
    assert result["decisions"][0]["simulation"] is None
    assert result["decisions"][0]["approval"] is None


@pytest.mark.asyncio
async def test_adapter_loads_only_through_unified_gateway(monkeypatch):
    identity, reports = _bundle_inputs()
    calls: list[tuple[str, str | None]] = []

    async def load_identity(*_args, **_kwargs):
        calls.append(("identity", None))
        return identity

    async def load_account(*_args, **_kwargs):
        calls.append(("account", None))
        return reports["account"]

    async def load_entities(*_args, **kwargs):
        level = kwargs["entity_level"]
        calls.append(("entity", level))
        return reports[level]

    monkeypatch.setattr(
        adapter.unified_gateway,
        "load_unified_marketing_account_identity",
        load_identity,
    )
    monkeypatch.setattr(
        adapter.unified_gateway,
        "load_unified_marketing_account_report",
        load_account,
    )
    monkeypatch.setattr(
        adapter.unified_gateway,
        "load_unified_marketing_entity_report",
        load_entities,
    )

    result = await adapter.load_decision_evidence(
        object(),
        "owner-1",
        provider="snapchat_ads",
        date_from=PERIOD_DAY,
        date_to=PERIOD_DAY,
        now=NOW,
    )

    assert result["decision_ready"] is True
    assert sorted(calls) == [
        ("account", None),
        ("entity", "ad"),
        ("entity", "ad_group"),
        ("entity", "campaign"),
        ("identity", None),
    ]


@pytest.mark.asyncio
async def test_gateway_load_failure_blocks_every_decision(monkeypatch):
    identity, reports = _bundle_inputs()

    async def load_identity(*_args, **_kwargs):
        return identity

    async def load_account(*_args, **_kwargs):
        return reports["account"]

    async def load_entities(*_args, **kwargs):
        if kwargs["entity_level"] == "ad_group":
            raise TimeoutError("gateway timeout")
        return reports[kwargs["entity_level"]]

    monkeypatch.setattr(
        adapter.unified_gateway,
        "load_unified_marketing_account_identity",
        load_identity,
    )
    monkeypatch.setattr(
        adapter.unified_gateway,
        "load_unified_marketing_account_report",
        load_account,
    )
    monkeypatch.setattr(
        adapter.unified_gateway,
        "load_unified_marketing_entity_report",
        load_entities,
    )

    evidence = await adapter.load_decision_evidence(
        object(),
        "owner-1",
        provider="snapchat_ads",
        date_from=PERIOD_DAY,
        date_to=PERIOD_DAY,
        now=NOW,
    )
    result = run_phase5_shadow_from_evidence(evidence)

    assert evidence["decision_ready"] is False
    assert evidence["gates"]["contract"]["loader_errors"] == {
        "ad_group": "TimeoutError"
    }
    assert "contract" in result["summary"]["blocked_reasons"]
    assert "coverage" in result["summary"]["blocked_reasons"]
    assert result["summary"]["recommendations"] == 0


def test_phase5_has_no_provider_storage_or_scheduler_dependency():
    root = Path(__file__).resolve().parents[1]
    phase_files = [
        root / "decision_intelligence" / "evidence_adapter.py",
        root / "decision_intelligence" / "phase5.py",
        root / "decision_intelligence" / "routes.py",
    ]
    forbidden_import_prefixes = (
        "snapchat_v2",
        "integrations_control_center.snapchat",
        "campaign_ai",
    )
    for path in phase_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(
            imported.startswith(forbidden_import_prefixes)
            for imported in imports
        ), (path.name, imports)
        assert "mezan_snapchat" not in source
        assert "insert_one" not in source
        assert "update_one" not in source
        assert "delete_one" not in source

    adapter_source = phase_files[0].read_text(encoding="utf-8")
    assert "unified_marketing.gateway" in adapter_source
    assert "load_unified_marketing_account_report" in adapter_source
    assert "load_unified_marketing_entity_report" in adapter_source


def test_phase5_output_explicitly_disables_writes_and_scheduler():
    result = run_phase5_shadow_from_evidence(_evidence())
    assert result["write_policy"] == {
        "platform_writes_enabled": False,
        "platform_writes_performed": False,
        "database_writes_performed": False,
    }
    assert result["scheduler_integration"] == {
        "campaign_ai_scheduler_connected": False,
        "automatic_execution_connected": False,
    }
    assert result["approval_workflow"]["auto_approval_enabled"] is False
    assert result["approval_workflow"]["approval_can_execute"] is False


def test_phase5_does_not_invent_forecast_or_confidence():
    result = run_phase5_shadow_from_evidence(_evidence())
    decision = result["decisions"][0]
    assert decision["simulation"]["forecast_used"] is False
    assert decision["impact_prediction"]["status"] == "unknown"
    assert decision["impact_prediction"]["expected_profit_delta_sar"] is None
    assert decision["impact_prediction"]["confidence"] is None
    assert decision["recommendation"]["expected_profit_delta_sar"] is None
    assert decision["recommendation"]["confidence"] is None


def test_phase5_route_is_get_only_and_owner_guarded():
    root = Path(__file__).resolve().parents[1]
    source = (root / "decision_intelligence" / "routes.py").read_text(
        encoding="utf-8"
    )
    assert 'router.get("/decision-intelligence/phase5/shadow")' in source
    assert "router.post" not in source
    assert "router.put" not in source
    assert "router.patch" not in source
    assert "router.delete" not in source
    assert "owner = require_owner(user)" in source
