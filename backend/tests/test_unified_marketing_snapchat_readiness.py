from datetime import datetime, timezone

import pytest

from unified_marketing import readiness
from unified_marketing.readers.snapchat_v2 import (
    load_snapchat_v2_entity_readiness_evidence,
)


class _Collection:
    def __init__(self, *, find_one=None, incomplete=False):
        self.find_one_result = find_one
        self.incomplete = incomplete

    async def find_one(self, query, projection=None, sort=None):
        if "coverage.status" in query:
            return {"_id": "incomplete"} if self.incomplete else None
        return self.find_one_result

    async def count_documents(self, query):
        return 3

    async def distinct(self, field, query):
        if field == "report_date":
            return ["2026-08-26"]
        if field == "external_id":
            return ["c1", "c2", "c3"]
        return []


class _Db:
    def __init__(self, *, incomplete=False):
        self.collections = {
            "mezan_snapchat_accounts_v2": _Collection(
                find_one={
                    "user_id": "u1",
                    "provider": "snapchat_ads",
                    "ad_account_id": "a1",
                    "timezone": "America/Los_Angeles",
                    "selected": True,
                    "active": True,
                }
            ),
            "mezan_snapchat_sync_runs_v2": _Collection(
                find_one={"sync_run_id": "r1", "campaign_sync_status": "complete"}
            ),
            "mezan_snapchat_daily_total_facts_v2": _Collection(
                incomplete=incomplete
            ),
        }

    def __getitem__(self, name):
        return self.collections[name]


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


def _evidence(level, *, complete=True):
    status = "complete" if complete else "partial"
    return {
        "contract_version": "unified-marketing-data-v1",
        "provider": "snapchat_ads",
        "entity_level": level,
        "contract_valid": True,
        "complete": complete,
        "row_count": 3,
        "source_fact_count": 3,
        "sync_status": status,
        "coverage_status": status,
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


@pytest.mark.asyncio
async def test_compact_hierarchy_evidence_is_period_specific_and_fail_closed():
    complete = await load_snapchat_v2_entity_readiness_evidence(
        _Db(),
        "u1",
        entity_level="campaign",
        date_from=datetime(2026, 8, 26).date(),
        date_to=datetime(2026, 8, 26).date(),
        timezone_name="America/Los_Angeles",
    )
    incomplete = await load_snapchat_v2_entity_readiness_evidence(
        _Db(incomplete=True),
        "u1",
        entity_level="campaign",
        date_from=datetime(2026, 8, 26).date(),
        date_to=datetime(2026, 8, 26).date(),
        timezone_name="America/Los_Angeles",
    )

    assert complete["complete"] is True
    assert complete["row_count"] == 3
    assert complete["source_fact_count"] == 3
    assert incomplete["complete"] is False
    assert incomplete["coverage_status"] == "partial"


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

    async def entity_evidence(*args, **kwargs):
        calls.append(
            (kwargs["entity_level"], kwargs["date_from"], kwargs["date_to"])
        )
        return _evidence(kwargs["entity_level"])

    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_identity", identity
    )
    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_report", account_report
    )
    monkeypatch.setattr(
        readiness,
        "load_unified_marketing_entity_readiness_evidence",
        entity_evidence,
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

    async def entity_evidence(*args, **kwargs):
        if kwargs["entity_level"] == "ad_group":
            raise RuntimeError("provider shape drift")
        return _evidence(kwargs["entity_level"])

    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_identity", identity
    )
    monkeypatch.setattr(
        readiness, "load_unified_marketing_account_report", account_report
    )
    monkeypatch.setattr(
        readiness,
        "load_unified_marketing_entity_readiness_evidence",
        entity_evidence,
    )

    result = await readiness.build_snapchat_unified_readiness(
        object(),
        "u1",
        now=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
    )

    assert result["ready"] is False
    assert "readiness_evidence_load_failed" in result["reasons"]
    assert result["errors"] == {"ad_group": "RuntimeError"}
    assert result["decision_isolation"]["eligible"] is False
