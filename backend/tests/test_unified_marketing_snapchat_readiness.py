from datetime import datetime, timezone

import pytest

from unified_marketing import readiness


def _report(level, *, complete=True, commerce_complete=True):
    sync_status = "complete" if complete else "partial"
    return {
        "contract_version": "unified-marketing-data-v1",
        "provider": "snapchat_ads",
        "entity_level": level,
        "totals": {
            "delivery": {"spend": {"amount": 10, "currency": "USD"}},
            "platform_outcomes": {
                "conversions": 2,
                "revenue": {"amount": 25, "currency": "USD"},
                "roas": 2.5,
            },
            "commerce_outcomes": {
                "status": "complete",
                "orders": 2,
                "revenue": {"amount": 90, "currency": "SAR"},
                "roas": 2.4,
            },
            "quality": {
                "sync_status": sync_status,
                "coverage_status": sync_status,
                "source_fact_count": 24,
                "amount_complete": True if level == "account" else None,
                "reconciliation_status": (
                    "reconciled" if level == "account" else None
                ),
            },
        },
        "rows": [{"entity": {"id": f"{level}-1"}}],
        "order_summary": {
            "status": "complete" if commerce_complete else "partial",
            "truncated": False,
        },
        "decision_eligibility": {
            "eligible": False,
            "reason": "shadow_not_accepted",
        },
    }


def test_readiness_passes_only_for_closed_complete_reconciled_contract():
    result = readiness.evaluate_snapchat_unified_readiness(
        account_report=_report("account"),
        entity_reports={level: _report(level) for level in readiness.ENTITY_LEVELS},
        period_closed=True,
    )

    assert result["ready"] is True
    assert result["reasons"] == []
    assert result["decision_isolation"] == {
        "passed": True,
        "connected": False,
        "eligible": False,
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("open_period", "period_is_not_closed"),
        ("reconciliation", "provider_reconciliation_incomplete"),
        ("hierarchy", "entity_hierarchy_incomplete"),
        ("salla", "salla_comparison_incomplete"),
        ("decision", "decision_isolation_guard_failed"),
    ],
)
def test_readiness_fails_closed_for_each_acceptance_gate(mutation, reason):
    account = _report("account")
    entities = {level: _report(level) for level in readiness.ENTITY_LEVELS}
    period_closed = True
    if mutation == "open_period":
        period_closed = False
    elif mutation == "reconciliation":
        account["totals"]["quality"]["reconciliation_status"] = "partial"
    elif mutation == "hierarchy":
        entities["ad"]["totals"]["quality"]["sync_status"] = "partial"
        entities["ad"]["totals"]["quality"]["coverage_status"] = "partial"
    elif mutation == "salla":
        account["order_summary"]["status"] = "partial"
    else:
        entities["campaign"]["decision_eligibility"]["eligible"] = True

    result = readiness.evaluate_snapchat_unified_readiness(
        account_report=account,
        entity_reports=entities,
        period_closed=period_closed,
    )

    assert result["ready"] is False
    assert reason in result["reasons"]


@pytest.mark.asyncio
async def test_builder_defaults_to_last_closed_account_day_and_uses_gateway(
    monkeypatch,
):
    calls = []

    async def identity(*args, **kwargs):
        return {
            "id": "a1",
            "name": "Store",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        }

    async def account_report(*args, **kwargs):
        calls.append(("account", kwargs["date_from"], kwargs["date_to"]))
        return _report("account")

    async def entity_report(*args, **kwargs):
        calls.append(
            (kwargs["entity_level"], kwargs["date_from"], kwargs["date_to"])
        )
        return _report(kwargs["entity_level"])

    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_identity", identity
    )
    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_report", account_report
    )
    monkeypatch.setattr(
        readiness, "load_unified_marketing_entity_report", entity_report
    )

    result = await readiness.build_snapchat_unified_readiness(
        object(),
        "u1",
        now=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
    )

    assert result["ready"] is True
    assert result["period"]["date_from"] == "2026-08-26"
    assert result["period"]["closed"] is True
    assert {item[0] for item in calls} == {
        "account",
        "campaign",
        "ad_group",
        "ad",
    }
    assert result["consumable"]["gateway"] == "unified_marketing.gateway"
    assert result["decision_isolation"]["connected"] is False


@pytest.mark.asyncio
async def test_builder_fails_closed_when_one_entity_report_cannot_load(monkeypatch):
    async def identity(*args, **kwargs):
        return {
            "id": "a1",
            "name": "Store",
            "currency": "USD",
            "timezone": "Asia/Riyadh",
        }

    async def account_report(*args, **kwargs):
        return _report("account")

    async def entity_report(*args, **kwargs):
        if kwargs["entity_level"] == "ad_group":
            raise RuntimeError("provider shape drift")
        return _report(kwargs["entity_level"])

    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_identity", identity
    )
    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_report", account_report
    )
    monkeypatch.setattr(
        readiness, "load_unified_marketing_entity_report", entity_report
    )

    result = await readiness.build_snapchat_unified_readiness(
        object(),
        "u1",
        now=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
    )

    assert result["ready"] is False
    assert "entity_report_load_failed" in result["reasons"]
    assert result["errors"] == {"ad_group": "RuntimeError"}
    assert result["decision_isolation"]["eligible"] is False
