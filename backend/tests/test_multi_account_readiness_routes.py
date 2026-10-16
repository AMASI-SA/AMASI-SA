from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from decision_intelligence import evidence_adapter
from decision_intelligence.routes import attach_decision_intelligence_phase5_routes
from unified_marketing import readiness
from tests.test_decision_intelligence_phase5 import _bundle_inputs
from tests.test_unified_marketing_snapchat_readiness import _evidence, _report


@pytest.mark.parametrize("requested", [None, "account-2"])
def test_phase5_http_route_scopes_all_evidence_loads(monkeypatch, requested):
    identity, reports = _bundle_inputs()
    effective = requested or identity["id"]
    identity["id"] = effective
    identity["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    for report in reports.values():
        report["account"]["id"] = effective
        report["totals"]["account"]["id"] = effective
        for row in report["rows"]:
            row["account"]["id"] = effective
    calls = []
    def record(user, kwargs):
        assert user == "owner"
        assert kwargs.get("account_id") == requested
        assert ("account_id" in kwargs) is (requested is not None)
        calls.append(kwargs)
    async def account_identity(db, user, **kwargs):
        record(user, kwargs)
        return identity
    async def account_report(db, user, **kwargs):
        record(user, kwargs)
        return reports["account"]
    async def entity_report(db, user, **kwargs):
        record(user, kwargs)
        return reports[kwargs["entity_level"]]
    monkeypatch.setattr(evidence_adapter.unified_gateway, "load_unified_marketing_account_identity", account_identity)
    monkeypatch.setattr(evidence_adapter.unified_gateway, "load_unified_marketing_account_report", account_report)
    monkeypatch.setattr(evidence_adapter.unified_gateway, "load_unified_marketing_entity_report", entity_report)
    app, router = FastAPI(), APIRouter()
    async def current_user():
        return {"id": "owner"}
    def require_owner(user):
        assert user["id"] == "owner"
        return user
    attach_decision_intelligence_phase5_routes(router, object(), current_user, require_owner)
    app.include_router(router)
    params = {"date_from": "2026-08-25", "date_to": "2026-08-25"}
    if requested:
        params["account_id"] = requested
    response = TestClient(app).get("/decision-intelligence/phase5/shadow", params=params)
    assert response.status_code == 200, response.text
    assert len(calls) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [None, "account-2"])
async def test_readiness_scopes_compact_proof_and_uses_account_closed_day(monkeypatch, requested):
    calls = []
    def record(user, kwargs):
        assert user == "owner"
        assert kwargs.get("account_id") == requested
        assert ("account_id" in kwargs) is (requested is not None)
        calls.append(kwargs)
    async def account_identity(db, user, **kwargs):
        record(user, kwargs)
        return {"id": requested or "account-1", "timezone": "America/New_York"}
    async def account_report(db, user, **kwargs):
        record(user, kwargs)
        return _report("account")
    async def entity_report(db, user, **kwargs):
        record(user, kwargs)
        return _evidence(kwargs["entity_level"])
    monkeypatch.setattr(readiness, "load_unified_marketing_account_identity", account_identity)
    monkeypatch.setattr(readiness, "load_unified_marketing_account_report", account_report)
    monkeypatch.setattr(readiness, "load_unified_marketing_entity_readiness_evidence", entity_report)
    result = await readiness.build_snapchat_unified_readiness(
        object(), "owner", account_id=requested,
        now=datetime(2026, 10, 16, 2, tzinfo=timezone.utc),
    )
    assert result["ready"] is True
    assert result["consumable"]["account"]["id"] == (requested or "account-1")
    assert result["period"]["date_to"] == "2026-10-14"
    assert len(calls) == 5
    assert all(call["timezone_name"] == "America/New_York" for call in calls[1:])


@pytest.mark.asyncio
async def test_readiness_rejects_wrong_account_identity(monkeypatch):
    async def wrong_identity(*args, **kwargs):
        return {"id": "other", "timezone": "Asia/Riyadh"}
    monkeypatch.setattr(readiness, "load_unified_marketing_account_identity", wrong_identity)
    result = await readiness.build_snapchat_unified_readiness(object(), "owner", account_id="requested")
    assert result["ready"] is False
