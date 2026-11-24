"""Recovery evidence is a bounded read, never a financial action."""
import asyncio
from unittest.mock import AsyncMock

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

from operational_balance_recovery_routes import attach_recovery_routes
from operational_balance_store import digest


async def request(*, principal=None, changed=False, method='GET'):
    calls = []

    async def current_user():
        if principal == {}:
            raise HTTPException(401)
        return principal if principal is not None else {'id': 'owner', 'role': 'owner'}

    async def scope(db, user, permission):
        calls.append(permission)
        return user, 'changed' if changed and len(calls) > 1 else 'owner', 'mezan2'

    app, router = FastAPI(), APIRouter(prefix='/operational-balances')
    attach_recovery_routes(router, object(), current_user, scope)
    app.include_router(router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
        return await client.request(method, '/api/operational-balances/recovery-proof'), calls


def test_owner_read_is_no_store_scope_bound_and_rechecked(monkeypatch):
    reader = AsyncMock(return_value={'state': 'unknown', 'proof': None, 'reason': 'not_recorded'})
    monkeypatch.setattr('operational_balance_recovery_proof.read_proof', reader)
    response, calls = asyncio.run(request())
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['session_scope'] == digest(['owner', 'owner'])
    assert response.json()['proof'] is None and response.json()['state'] == 'unknown'
    assert calls == ['reports', 'reports'] and reader.await_count == 1


@pytest.mark.parametrize('principal,expected', [({}, 401),
    ({'id': 'employee', 'role': 'employee'}, 403), ({'id': 'foreign', 'role': 'owner'}, 403)])
def test_nonowner_never_reads_proof(monkeypatch, principal, expected):
    reader = AsyncMock()
    monkeypatch.setattr('operational_balance_recovery_proof.read_proof', reader)
    response, _ = asyncio.run(request(principal=principal))
    assert response.status_code == expected and reader.await_count == 0


def test_permission_revoked_during_read_cannot_return_the_receipt(monkeypatch):
    reader = AsyncMock(return_value={'state': 'available', 'proof': {'private': 'never-return'}})
    monkeypatch.setattr('operational_balance_recovery_proof.read_proof', reader)
    response, calls = asyncio.run(request(changed=True))
    assert response.status_code == 403 and 'never-return' not in response.text
    assert calls == ['reports', 'reports']


def test_endpoint_has_no_mutation_method(monkeypatch):
    reader = AsyncMock()
    monkeypatch.setattr('operational_balance_recovery_proof.read_proof', reader)
    response, _ = asyncio.run(request(method='POST'))
    assert response.status_code == 405 and reader.await_count == 0
