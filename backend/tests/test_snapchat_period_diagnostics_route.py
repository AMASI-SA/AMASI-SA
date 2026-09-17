from datetime import date

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from snapchat_v2 import routes


@pytest.mark.asyncio
@pytest.mark.parametrize("case, expected", [
    ("owner", 200), ("employee", 403), ("anonymous", 401),
    ("invalid_split", 422), ("large_range", 422), ("changed_account", 409),
    ("limit", 422), ("timeout", 503), ("database_failure", 503),
])
async def test_diagnostic_enforces_access_scope_and_bounded_read(monkeypatch, case, expected):
    calls = []

    def current_user():
        if case == "anonymous":
            raise HTTPException(status_code=401)
        return {"id": "owner-tenant", "role": "employee" if case == "employee" else "owner"}

    def require_owner(user):
        if user["role"] != "owner":
            raise HTTPException(status_code=403)
        return user

    async def selected_account(db, user_id):
        assert user_id == "owner-tenant"
        return {"ad_account_id": "account", "timezone": "America/Los_Angeles"}

    async def diagnostic(db, user_id, **kwargs):
        calls.append(kwargs)
        assert user_id == "owner-tenant"
        assert kwargs["max_rows"] == 10_000
        assert kwargs["timezone_name"] == "America/Los_Angeles"
        assert kwargs["split_on"] == date(2026, 8, 25)
        if case == "limit":
            raise ValueError("private data")
        if case == "timeout":
            raise TimeoutError("private data")
        if case == "database_failure":
            raise RuntimeError("private connection detail")
        return {"candidate_rows": 1, "all_orders": {"whole": 1, "left": 0, "right": 0, "gap": 1}}

    monkeypatch.setattr(routes, "get_selected_account", selected_account)
    monkeypatch.setattr(routes, "audit_period_partition", diagnostic)
    router = APIRouter()
    routes.attach_snapchat_v2_routes(router, object(), current_user, require_owner)
    app = FastAPI()
    app.include_router(router)
    params = {"date_from": "2026-08-01", "date_to": "2026-09-17",
              "split_on": "2026-08-25", "ad_account_id": "account"}
    if case == "invalid_split":
        params["split_on"] = "2026-08-01"
    if case == "large_range":
        params["date_from"] = "2020-01-01"
    if case == "changed_account":
        params["ad_account_id"] = "other-account"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/snapchat-v2/period-diagnostics", params=params)
    assert response.status_code == expected
    assert "private" not in response.text
    if case in {"employee", "anonymous", "invalid_split", "large_range", "changed_account"}:
        assert not calls
    if case == "owner":
        assert response.json()["ad_account_id"] == "account"
