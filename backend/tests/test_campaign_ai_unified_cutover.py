from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_active_snapchat_ai_evidence_consumers_use_unified_gateway():
    for name in (
        "campaign_ai_funnel_evidence_v3.py",
        "campaign_ai_temporal_evidence_v3.py",
        "campaign_ai_product_intelligence_v3.py",
    ):
        source = (ROOT / name).read_text()
        assert "unified_marketing.gateway" in source
        assert "snapchat_account_timezone_manager" not in source
        assert "snapchat_native_data_common" not in source


def test_unified_ai_cutover_keeps_named_v1_rollback_readers():
    source = (ROOT / "campaign_ai_policy_v2.py").read_text()
    assert "async def _snapchat_v1_campaign_entities(" in source
    assert "async def _snapchat_v1_child_entities(" in source
    assert "async def _snapchat_campaign_entities(" in source
    assert "async def _snapchat_child_entities(" in source
    assert "load_snapchat_unified_ai_entities" in source


def test_unified_shadow_compares_named_rollback_not_active_source():
    source = (ROOT / "campaign_ai_unified_shadow.py").read_text()
    assert "_snapchat_v1_campaign_entities" in source
    assert "_snapchat_v1_child_entities" in source
    assert "cutover_active\": True" in source


import pytest
from unittest.mock import AsyncMock
from tests.test_decision_intelligence_phase5 import _bundle_inputs, PERIOD_DAY, NOW

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "coverage", "account", "owner", "provider", "candidate_finance", "load_failure"])
async def test_shared_ai_loader_requires_canonical_proof(monkeypatch, failure):
    import campaign_ai_unified_source as source
    identity, reports = _bundle_inputs()
    if failure == "coverage":
        reports["ad"]["totals"]["quality"]["coverage_status"] = "partial"
    elif failure == "account":
        reports["ad"]["account"]["id"] = "another-account"
    elif failure == "owner":
        identity["user_id"] = "another-owner"
    elif failure == "provider":
        identity["provider"] = "meta_ads"
    elif failure == "candidate_finance":
        reports["campaign"]["rows"][0]["commerce_profitability"]["status"] = "partial"
    monkeypatch.setattr(source, "load_unified_marketing_account_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(source, "load_unified_marketing_account_identities", AsyncMock(return_value=[identity]))
    account_read = AsyncMock(return_value=reports["account"])
    monkeypatch.setattr(source, "load_unified_marketing_account_report", account_read)
    calls = []
    async def entity_read(db, user, **kwargs):
        calls.append(kwargs)
        if failure == "load_failure" and kwargs["entity_level"] == "ad":
            raise RuntimeError("provider unavailable")
        return reports[kwargs["entity_level"]]
    monkeypatch.setattr(source, "load_unified_marketing_entity_report", entity_read)
    monkeypatch.setattr(source._legacy, "_utcnow", lambda: NOW)
    monkeypatch.setattr(source, "_local_range", lambda *args: (PERIOD_DAY, PERIOD_DAY))
    result = await source.load_snapchat_unified_ai_entities(None, "owner", PERIOD_DAY, PERIOD_DAY)
    account_read.assert_awaited_once()
    assert len(calls) == 3
    assert all(call["include_stale"] is False for call in calls)
    if failure:
        assert result["campaigns"] == []
        assert result["children"] == []
        if failure != "candidate_finance":
            assert result["decision_eligibility"]["eligible"] is False
    else:
        assert result["decision_eligibility"]["eligible"] is True
        assert len(result["campaigns"]) == 1
        assert len(result["children"]) == 2
        assert result["campaigns"][0]["provider"] == "snapchat"
        from unified_marketing.decision_binding import source_evidence_fingerprint
        for candidate in result["campaigns"] + result["children"]:
            level = candidate["entity_level"]
            row = next(row for row in reports[level]["rows"] if row["entity"]["id"] == candidate["entity_id"])
            parent = reports["campaign"]["rows"][0] if level != "campaign" else None
            management = (reports[level].get("management_context") or {}).get(candidate["entity_id"]) or {}
            assert candidate["source_evidence_fingerprint"] == source_evidence_fingerprint(
                "owner", row, management, parent_campaign=parent,
            )



def test_default_ai_period_is_last_closed_account_day(monkeypatch):
    import campaign_ai_unified_source as source
    from datetime import date
    monkeypatch.setattr(source._legacy, "_utcnow", lambda: NOW)
    start, end = source._local_range("America/Los_Angeles", date(2026, 8, 20), date(2026, 8, 26))
    assert end == date(2026, 8, 25)
    assert (end - start).days == 6


@pytest.mark.asyncio
async def test_future_platform_uses_same_ai_pipeline(monkeypatch):
    import campaign_ai_unified_source as source
    from unified_marketing.providers import PROVIDERS, MarketingProvider
    identity, reports = _bundle_inputs()
    identity["provider"] = "future_ads"
    for report in reports.values():
        report["provider"] = "future_ads"
        report["totals"]["provider"] = "future_ads"
        for row in report["rows"]:
            row["provider"] = "future_ads"
    async def entity_read(db, user, **kwargs):
        assert user == "owner"
        return reports[kwargs["entity_level"]]
    monkeypatch.setattr(source, "load_unified_marketing_account_identities", AsyncMock(return_value=[identity]))
    account_read = AsyncMock(return_value=reports["account"])
    monkeypatch.setitem(PROVIDERS, "future_ads", MarketingProvider(
        "future_ads", "future", "future-contract", {
            "account_identity": AsyncMock(return_value=identity),
            "entity_report": entity_read, "account_report": account_read,
        },
    ))
    monkeypatch.setattr(source._legacy, "_utcnow", lambda: NOW)
    monkeypatch.setattr(source, "_local_range", lambda *args: (PERIOD_DAY, PERIOD_DAY))
    result = await source.load_unified_ai_entities(None, "owner", PERIOD_DAY, PERIOD_DAY, provider="future_ads")
    assert result["decision_eligibility"]["eligible"] is True
    assert len(result["campaigns"]) == 1
    assert result["campaigns"][0]["provider"] == "future"
    assert result["campaigns"][0]["provider_result_source"] == "future-contract"
    assert len(result["children"]) == 2
