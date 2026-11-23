"""The global health read is owner-scoped, bounded and has no finance capability."""
import asyncio
from copy import deepcopy

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

import operational_balance_freshness as health
from operational_balance_freshness_routes import attach_freshness_routes
from operational_balance_store import digest
from test_operational_balance_freshness import DB, NOW, OWNER, sources


PATH = "/api/operational-balances/freshness"


async def get(db, monkeypatch, *, principal=None, revoke=False, unavailable=False, method="GET"):
    import operational_source_freshness
    calls = []

    async def current_user():
        if principal == {}:
            raise HTTPException(401)
        return principal or {"id": OWNER, "role": "owner"}

    async def scope(database, user, permission):
        calls.append(permission)
        if user.get("disabled"):
            raise HTTPException(403)
        return user, "other-owner" if revoke and len(calls) > 1 else OWNER, "mezan2"

    async def read_sources(*args, **kwargs):
        return sources()

    original = health.read_freshness
    async def read_at_now(*args, **kwargs):
        if unavailable:
            raise TimeoutError("secret://database")
        return await original(*args, now=NOW)

    monkeypatch.setattr(operational_source_freshness, "read_source_freshness", read_sources)
    monkeypatch.setattr(health, "read_freshness", read_at_now)
    app, router = FastAPI(), APIRouter(prefix="/operational-balances")
    attach_freshness_routes(router, db, current_user, scope)
    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        result = await client.request(method, PATH + "?owner_id=other-owner")
    return result, calls


def test_get_is_owner_bound_metadata_only_and_cannot_be_cached(monkeypatch):
    db = DB()
    before = deepcopy(db.data)
    response, calls = asyncio.run(get(db, monkeypatch))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["actor"] == {"id": OWNER, "is_owner": True}
    assert payload["session_scope"] == digest([OWNER, OWNER])
    assert response.headers["cache-control"] == "no-store"
    assert calls == ["reports", "reports"]
    assert db.writes == [] and db.data == before
    assert "12345" not in response.text


@pytest.mark.parametrize("principal,expected", [({}, 401),
    ({"id": "staff", "role": "employee"}, 403),
    ({"id": "other-owner", "role": "owner"}, 403),
    ({"id": OWNER, "role": "owner", "disabled": True}, 403)])
def test_no_status_read_for_unauthorized_actor(monkeypatch, principal, expected):
    db = DB()
    response, _ = asyncio.run(get(db, monkeypatch, principal=principal))
    assert response.status_code == expected
    assert db.reads == [] and db.writes == []


def test_scope_revoked_during_read_cannot_return_previous_owner_health(monkeypatch):
    db = DB()
    response, _ = asyncio.run(get(db, monkeypatch, revoke=True))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "operational_actor_scope_changed"
    assert "sources" not in response.json() and db.writes == []


def test_unavailable_metadata_is_an_explicit_failure_not_healthy_empty_data(monkeypatch):
    db = DB()
    response, _ = asyncio.run(get(db, monkeypatch, unavailable=True))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "operational_freshness_unavailable"
    assert "secret" not in response.text and db.writes == []


def test_health_endpoint_does_not_expose_a_post_action(monkeypatch):
    db = DB()
    response, _ = asyncio.run(get(db, monkeypatch, method="POST"))
    assert response.status_code == 405
    assert db.reads == [] and db.writes == []
