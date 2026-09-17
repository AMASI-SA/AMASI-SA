"""Full-population attribution evidence must survive the unified boundary."""
from datetime import date

import pytest

from tests.test_snapchat_reporting_v2_period_candidates import database, row
from snapchat_v2.salla_outcomes import load_salla_campaign_outcomes
from unified_marketing.adapters.snapchat_v2 import _commerce_order_summary
from tests.test_decision_intelligence_phase5 import _bundle_inputs, _evidence
from unified_marketing.attribution import campaign_attribution_complete
from unified_marketing.readiness import evaluate_snapchat_unified_readiness
from tests.test_unified_marketing_snapchat_readiness import _report


@pytest.mark.asyncio
async def test_gap_reasons_cover_only_snapchat_orders_before_audit_pagination():
    rows = [row(order_number=str(i)) for i in range(501)]
    rows += [
        row(order_number="missing", campaign_id=None),
        row(order_number="click", campaign_id=None,
            utm_campaign="Using the CLICKID sccid provided by snapchat"),
        row(order_number="unknown", campaign_id="missing-in-catalog"),
        row(order_number="ambiguous", campaign_id=None, campaign_name="Duplicate"),
        row(order_number="direct", source="direct", campaign_id=None),
        row(order_number="other-tenant", user_id="other", campaign_id=None),
    ]
    result = await load_salla_campaign_outcomes(
        database(rows), "owner", account_id="account",
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
        timezone_name="America/Los_Angeles",
        identities=[
            {"account_id": "account", "campaign_id": "campaign", "campaign_name": "Campaign"},
            {"account_id": "account", "campaign_id": "a", "campaign_name": "Duplicate"},
            {"account_id": "account", "campaign_id": "b", "campaign_name": "Duplicate"},
        ],
    )
    proof = result["summary"]["campaign_attribution"]
    assert proof == {
        "status": "partial", "evaluated_orders": 505, "matched_orders": 501,
        "unmatched_orders": 4, "coverage_pct": 99.21,
        "reason_counts": {"campaign_identity_missing": 1, "click_reference_only": 1,
                          "campaign_not_in_catalog": 1, "ambiguous_name": 1},
        "date_scope": "account_timezone", "complete_population": True,
    }
    assert result["truncated"] is True
    summary = _commerce_order_summary(result["summary"]).model_dump()
    assert summary["campaign_attribution"] == proof


@pytest.mark.parametrize("proof", [None, {}, {
    "status": "partial", "evaluated_orders": 10, "matched_orders": 8,
    "unmatched_orders": 2, "coverage_pct": 80,
    "reason_counts": {"campaign_identity_missing": 2},
    "date_scope": "account_timezone", "complete_population": True,
}, {
    "status": "complete", "evaluated_orders": 10, "matched_orders": 8,
    "unmatched_orders": 0, "coverage_pct": 100, "reason_counts": {},
    "date_scope": "account_timezone", "complete_population": True,
}])
def test_decision_gate_rejects_missing_partial_or_inconsistent_attribution(proof):
    identity, reports = _bundle_inputs()
    reports["account"]["order_summary"]["campaign_attribution"] = proof
    evidence = _evidence(identity, reports)
    assert evidence["gates"]["attribution"]["passed"] is False
    assert "attribution" in evidence["blocked_by"]
    assert all(not row["decision_eligible"] for row in evidence["candidates"])


@pytest.mark.parametrize("total", [0, 8])
def test_complete_population_with_no_gap_is_consumable(total):
    assert campaign_attribution_complete({
        "status": "complete", "evaluated_orders": total, "matched_orders": total,
        "unmatched_orders": 0, "coverage_pct": 100 if total else None,
        "reason_counts": {}, "date_scope": "account_timezone", "complete_population": True,
    })


@pytest.mark.parametrize("change", [
    {"complete_population": False}, {"date_scope": "salla_order_date"},
    {"evaluated_orders": True}, {"matched_orders": -1}, {"unmatched_orders": None},
    {"reason_counts": {"campaign_identity_missing": 1}}, {"coverage_pct": 99.9},
])
def test_readiness_rejects_untrusted_attribution_proof(change):
    account = _report("account")
    account["order_summary"]["campaign_attribution"].update(change)
    result = evaluate_snapchat_unified_readiness(
        account_report=account,
        entity_reports={level: _report(level) for level in ("campaign", "ad_group", "ad")},
        period_closed=True,
    )
    assert result["ready"] is False
    assert "campaign_attribution_incomplete" in result["reasons"]


@pytest.mark.asyncio
async def test_attribution_proof_uses_account_date_not_legacy_stored_date():
    result = await load_salla_campaign_outcomes(
        database([row(campaign_id=None, order_date="2026-08-20", created_at="2026-08-26T07:00:00Z")]),
        "owner", account_id="account", date_from=date(2026, 8, 26), date_to=date(2026, 8, 26),
        timezone_name="America/Los_Angeles", identities=[],
    )
    assert result["summary"]["snapchat_attributed_orders"] == 0
    assert result["summary"]["campaign_attribution"]["unmatched_orders"] == 1
