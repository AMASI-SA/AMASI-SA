"""HTTP permission/body/owner gates for visible-page-only refresh."""
import asyncio

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

from operational_balance_store import STATES, digest, mutate
from operational_demand_refresh import LEASES
from operational_demand_refresh_routes import attach_demand_refresh_routes
from test_operational_balance_demand_refresh import DB


PATH = "/api/operational-balances/refresh"


async def post(db, monkeypatch, *, payload=None, principal=None, scope_override=None, revoke=False):
    import operational_balance_service
    calls = []

    async def current_user():
        if principal == {}:
            raise HTTPException(401)
        return principal or {"id": "staff"}

    async def scope(db, user, permission):
        calls.append(permission)
        if user.get("disabled"):
            raise HTTPException(403, detail={"code": "operational_actor_inactive"})
        return {"id": user["id"]}, "owner", "mezan2"

    async def refresh(db, owner):
        async def apply(state):
            if revoke:
                user_data["disabled"] = True
            state["engine"] = {"as_of": "2026-11-12T12:00:00+00:00"}
            return state
        return await mutate(db, owner, apply)

    user_data = principal if principal is not None else {"id": "staff"}
    monkeypatch.setattr(operational_balance_service, "refresh", refresh)
    router = APIRouter(prefix="/operational-balances")
    attach_demand_refresh_routes(router, db, current_user, scope_override or scope)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        response = await client.post(PATH, json=payload if payload is not None else {"visible": True})
    return response, calls


def test_first_visible_page_needs_no_saved_session_digest(monkeypatch):
    db = DB()
    response, calls = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "refreshed", "as_of": "2026-11-12T12:00:00+00:00",
                               "revision": 2, "session_scope": digest(["owner", "staff"])}
    assert calls and set(calls) == {"reports"}
    assert db.data[LEASES] == {}


def test_native_evidence_is_acquired_inside_the_same_lease_before_one_refresh(monkeypatch):
    import operational_visible_evidence
    from operational_demand_refresh import demand_context

    db = DB()
    seen = []

    async def acquire(database, owner):
        context = demand_context()
        assert database is db and owner == "owner"
        assert context is not None and context.db is db and context.owner == owner
        assert db.data[LEASES]  # Acquisition shares this request's distributed lease.
        assert db.data[STATES][owner]["revision"] == 1
        seen.append("acquired")

    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 200, response.text
    assert seen == ["acquired"]
    assert db.data[STATES]["owner"]["revision"] == 2
    assert db.data[LEASES] == {}


@pytest.mark.parametrize("payload", [{}, {"visible": False}, {"visible": "true"}, {"visible": 1},
                                     {"visible": True, "owner_id": "other"}])
def test_hidden_or_forged_page_request_never_starts_work(monkeypatch, payload):
    db = DB()
    response, _ = asyncio.run(post(db, monkeypatch, payload=payload))
    assert response.status_code == 422
    assert db.writes == []


@pytest.mark.parametrize("principal,status", [({}, 401), ({"id": "staff", "disabled": True}, 403)])
def test_refresh_requires_authenticated_active_membership(monkeypatch, principal, status):
    db = DB()
    response, _ = asyncio.run(post(db, monkeypatch, principal=principal))
    assert response.status_code == status
    assert db.writes == []


def test_saved_session_digest_cannot_be_reused_for_another_owner(monkeypatch):
    db = DB()
    response, _ = asyncio.run(post(db, monkeypatch, payload={"visible": True,
        "expected_session_scope": digest(["other", "staff"])}))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "operational_actor_scope_changed"
    assert db.writes == []


def test_scope_change_during_preflight_never_writes_a_lease(monkeypatch):
    db = DB()
    checks = 0

    async def scope(db, user, permission):
        nonlocal checks
        checks += 1
        return {"id": "staff"}, "owner" if checks == 1 else "other", "mezan2"

    response, _ = asyncio.run(post(db, monkeypatch, scope_override=scope))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "operational_actor_scope_changed"
    assert db.writes == []


def test_permission_revoked_after_calculation_prevents_save(monkeypatch):
    db = DB()
    response, _ = asyncio.run(post(db, monkeypatch, principal={"id": "staff"}, revoke=True))
    assert response.status_code == 403
    assert db.data[STATES]["owner"]["revision"] == 1
    assert db.data[LEASES] == {}


@pytest.mark.parametrize("state_status", ["draft", "frozen"])
def test_inactive_system_returns_read_status_without_lease_or_calculation(monkeypatch, state_status):
    db = DB()
    db.data[STATES]["owner"]["status"] = state_status
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 200
    assert response.json()["status"] == state_status
    assert db.data[STATES]["owner"]["revision"] == 1
    assert db.writes == []


def test_accounting_activation_returns_read_status_without_refresh(monkeypatch):
    db = DB()
    db.data["mz2_atomic_owners"] = {"owner": {"_id": "owner", "ledger_backend_state": "v2_active"}}
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 200
    assert response.json()["status"] == "accounting_active"
    assert db.writes == []


def test_real_router_native_refresh_uses_real_actor_report_grant(monkeypatch):
    from mobile_app_permissions import OPERATIONAL_APP_READ
    from operational_balance_routes import make_operational_balance_router
    import operational_balance_service

    async def scenario():
        db = DB()
        db.data["users"] = {
            "owner": {"id": "owner", "role": "owner", "is_active": True},
            "staff": {"id": "staff", "role": "employee", "created_by": "owner", "is_active": True},
        }
        access = {"owner_user_id": "owner", "user_id": "staff", "enabled": True,
                  "permissions": [OPERATIONAL_APP_READ]}
        db.data["mezan_mobile_app_access_v1"] = {"access": access}

        async def principal():
            return {"id": "owner", "_mobile_actor_id": "staff", "_session_client": "amasi_mobile"}

        async def refresh(db, owner):
            async def apply(state):
                state["engine"]["as_of"] = "2026-11-12T12:00:00+00:00"
                return state
            return await mutate(db, owner, apply)

        monkeypatch.setattr(operational_balance_service, "refresh", refresh)
        app = FastAPI()
        app.include_router(make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            response = await client.post(PATH, json={"visible": True})
            assert response.status_code == 200, response.text
            assert response.json()["session_scope"] == digest(["owner", "staff"])
            writes_before = len(db.writes)
            access["permissions"] = []
            response = await client.post(PATH, json={"visible": True})
            assert response.status_code == 403
            assert response.json()["detail"]["code"] == "operational_app_permission_required"
            assert len(db.writes) == writes_before
    asyncio.run(scenario())
