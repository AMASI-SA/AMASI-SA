from datetime import date

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from snapchat_v2 import routes


@pytest.mark.asyncio
async def test_default_route_rejects_disabled_selected_account(monkeypatch):
    async def resolve(db, user, account):
        assert account is None
        return None
    monkeypatch.setattr(routes, "resolve_account", resolve)
    with pytest.raises(routes.HTTPException) as error:
        await routes._selected_account_or_404(object(), "owner")
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("path,extra", [
    ("report", {"date_from": "2026-10-15", "date_to": "2026-10-15"}),
    ("hourly", {"report_date": "2026-10-15"}),
    ("campaigns", {"date_from": "2026-10-15", "date_to": "2026-10-15"}),
    ("ad-squads", {}), ("ads", {}),
    ("unified-readiness", {}),
])
async def test_explicit_foreign_or_disabled_account_is_rejected_before_data_reads(monkeypatch, path, extra):
    async def resolve(db, user, account):
        assert user == "owner"
        assert account == "foreign"
        raise LookupError("private detail")
    monkeypatch.setattr(routes, "resolve_account", resolve)
    router = APIRouter()
    routes.attach_snapchat_v2_routes(router, object(), lambda: {"id": "owner"}, lambda u: u)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/snapchat-v2/{path}", params={"ad_account_id": "foreign", **extra})
    assert response.status_code == 404
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_second_account_report_uses_its_own_currency_timezone_and_scope(monkeypatch):
    scopes = []
    async def resolve(db, user, account):
        assert account == "second"
        return {"ad_account_id": "second", "currency": "SAR", "timezone": "Asia/Riyadh"}
    async def projections(db, **kwargs):
        scopes.append(kwargs)
        return []
    async def reconciliation(db, **kwargs):
        scopes.append(kwargs)
        return []
    monkeypatch.setattr(routes, "resolve_account", resolve)
    monkeypatch.setattr(routes, "list_daily_projections", projections)
    monkeypatch.setattr(routes, "list_reconciliation", reconciliation)
    router = APIRouter()
    routes.attach_snapchat_v2_routes(router, object(), lambda: {"id": "owner"}, lambda u: u)
    endpoint = next(r.endpoint for r in router.routes if r.name == "snapchat_v2_report_route")
    result = await endpoint(ad_account_id="second", date_from=date(2026,10,15), date_to=date(2026,10,15), timezone="account", action_report_time="conversion", user={"id":"owner"})
    assert result["ad_account_id"] == "second"
    assert result["currency"] == "SAR"
    assert result["projection_timezone"] == "Asia/Riyadh"
    assert all(scope["ad_account_id"] == "second" and scope["user_id"] == "owner" for scope in scopes)
