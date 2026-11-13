"""Actual management router and auth dependency, no provider/network calls."""
import json
from copy import deepcopy
from fastapi import APIRouter, FastAPI, Request
import httpx
import pytest
import auth
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from tests.google_namespace_bootstrap_ui_fixtures import PATH, public_fixtures

PREFIX = '/integrations-v2/google_ads/automation/namespace-bootstrap'


class ForbiddenProvider:
    def __init__(self, *args, **kwargs):
        raise AssertionError('storage routes must never construct an advertising provider')


def management_app(db):
    app, router = FastAPI(), APIRouter(prefix='/integrations-v2')
    async def current(request: Request):
        return await auth.get_current_user_from_db(request, db)
    attach_google_ads_management_routes(router, db, current, client_factory=ForbiddenProvider, ai_client_factory=ForbiddenProvider)
    app.state.management_routes = list(router.routes)
    app.include_router(router)
    return app


class Users:
    async def find_one(self, query):
        assert query == {'id': 'owner'}
        return {'id': 'owner', 'role': 'owner'}


class OnlyAuthDB:
    users = Users()
    def __getattr__(self, name):
        raise AssertionError('invalid request must not reach storage: ' + name)


def fixture_auth(monkeypatch):
    monkeypatch.setattr(auth, 'get_jwt_secret', lambda: 'synthetic_fixture_only')
    monkeypatch.setattr(auth.jwt, 'decode', lambda *a, **k: {'sub': 'owner', 'type': 'access', 'mfa': True, 'iat': 2000000000})


def test_public_fixture_matches_frozen_runtime_serializer_without_invented_authority():
    assert json.loads(PATH.read_text()) == public_fixtures()


def test_full_router_attaches_each_storage_endpoint_once_without_provider_construction():
    app = management_app(OnlyAuthDB())
    paths = [(r.path, method) for r in app.state.management_routes for method in getattr(r, 'methods', [])]
    for suffix, method in [('/receipt', 'GET'), ('/prepare', 'POST'), ('/execute', 'POST')]:
        assert paths.count((PREFIX + suffix, method)) == 1
    for suffix, methods in [('storage-commit-readiness', ['GET', 'POST']), ('namespace-prerequisites', ['GET'])]:
        for method in methods: assert paths.count(('/integrations-v2/google_ads/automation/' + suffix, method)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('method,suffix', [('GET', '/receipt'), ('POST', '/prepare'), ('POST', '/execute')])
async def test_attached_routes_require_real_authentication_before_any_storage(method, suffix):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(management_app(OnlyAuthDB())), base_url='http://test') as client:
        response = await client.request(method, PREFIX + suffix, json={})
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix,payload', [
    ('prepare', {'request_nonce': 'a' * 32}),
    ('prepare', {'request_nonce': 'a' * 32, 'acknowledge_preparation_only': 1}),
    ('prepare', {'request_nonce': 'a' * 32, 'acknowledge_preparation_only': True, 'account_id': '5052346701'}),
    ('execute', {'request_nonce': 'a' * 32, 'execution_token': 'b' * 64, 'observation_digest': 'c' * 64,
        'manifest_digest': 'd' * 64, 'acknowledge_storage_only': True, 'enabled': True}),
])
async def test_actual_auth_then_strict_attached_request_model_rejects_before_storage(monkeypatch, suffix, payload):
    fixture_auth(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(management_app(OnlyAuthDB())), base_url='http://test', headers={'Authorization': 'Bearer synthetic'}) as client:
        response = await client.post(PREFIX + '/' + suffix, json=deepcopy(payload))
    assert response.status_code == 422, response.text
