from copy import deepcopy
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
import campaign_ai_unified_source as source
from tests.test_decision_intelligence_phase5 import _bundle_inputs, PERIOD_DAY, NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("second_failure", [None, "coverage", "load"])
async def test_accounts_keep_independent_candidates_and_readiness(monkeypatch, second_failure):
    first_identity, first_reports = _bundle_inputs()
    second_identity, second_reports = deepcopy(first_identity), deepcopy(first_reports)
    second_identity["id"] = "account-2"
    for report in second_reports.values():
        report["account"]["id"] = "account-2"
        report["totals"]["account"]["id"] = "account-2"
        for row in report["rows"]:
            row["account"]["id"] = "account-2"
    if second_failure == "coverage":
        second_reports["ad"]["totals"]["quality"]["coverage_status"] = "partial"
    identities = {first_identity["id"]: first_identity, "account-2": second_identity}
    reports = {first_identity["id"]: first_reports, "account-2": second_reports}
    monkeypatch.setattr(source, "load_unified_marketing_account_identities", AsyncMock(return_value=list(identities.values())))
    async def identity(db, user, **kwargs):
        assert user == "owner"
        return identities[kwargs["account_id"]]
    async def account(db, user, **kwargs):
        return reports[kwargs["account_id"]]["account"]
    async def entity(db, user, **kwargs):
        if second_failure == "load" and kwargs["account_id"] == "account-2":
            raise RuntimeError("unavailable")
        return reports[kwargs["account_id"]][kwargs["entity_level"]]
    monkeypatch.setattr(source, "load_unified_marketing_account_identity", identity)
    monkeypatch.setattr(source, "load_unified_marketing_account_report", account)
    monkeypatch.setattr(source, "load_unified_marketing_entity_report", entity)
    monkeypatch.setattr(source._legacy, "_utcnow", lambda: NOW)
    monkeypatch.setattr(source, "_local_range", lambda *args: (PERIOD_DAY, PERIOD_DAY))
    result = await source.load_snapchat_unified_ai_entities(None, "owner", PERIOD_DAY, PERIOD_DAY)
    expected = {first_identity["id"]} if second_failure else {first_identity["id"], "account-2"}
    assert {row["account_id"] for row in result["campaigns"]} == expected
    assert len(result["children"]) == 2 * len(expected)
    assert result["decision_eligibility"]["eligible"] is (not second_failure)
    assert len(result["account_diagnostics"]) == 2
    assert result["account_diagnostics"][0]["decision_eligibility"]["eligible"] is True
    if not second_failure:
        assert len({row["source_evidence_fingerprint"] for row in result["campaigns"]}) == 2


def test_account_windows_are_independent_at_midnight(monkeypatch):
    monkeypatch.setattr(source._legacy, "_utcnow", lambda: datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    requested = date(2026, 10, 15)
    assert source._local_range("Asia/Riyadh", requested, requested) == (date(2026, 10, 15),) * 2
    assert source._local_range("America/New_York", requested, requested) == (date(2026, 10, 14),) * 2


@pytest.mark.asyncio
async def test_no_enabled_accounts_cannot_fallback_to_selected(monkeypatch):
    monkeypatch.setattr(source, "load_unified_marketing_account_identities", AsyncMock(return_value=[]))
    identity = AsyncMock()
    monkeypatch.setattr(source, "load_unified_marketing_account_identity", identity)
    result = await source.load_snapchat_unified_ai_entities(None, "owner", PERIOD_DAY, PERIOD_DAY)
    assert result["campaigns"] == []
    assert result["decision_eligibility"]["eligible"] is False
    identity.assert_not_called()
