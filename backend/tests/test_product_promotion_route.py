"""Retired product campaign endpoint remains cheap and owner protected."""
from dataclasses import replace
import httpx
import pytest
from fastapi import FastAPI, HTTPException
import dashboard_v2_routes as routes
import product_promotion_estimates as estimates
import resource_governor


@pytest.fixture
def route_app(monkeypatch):
    sample = replace(resource_governor.memory_snapshot(), usage_ratio=0.25)
    monkeypatch.setattr(resource_governor, 'memory_snapshot', lambda: sample)
    async def user():
        return {'id': 'authenticated-owner', 'role': 'owner'}
    def require_owner(current):
        if current.get('role') != 'owner':
            raise HTTPException(403)
    app = FastAPI()
    app.include_router(routes.make_dashboard_v2_router(object(), user, None, require_owner))
    return app, user


PARAMS = dict(from_date='2026-11-01', to_date='2026-11-07', product_id='p1',
              platform='snapchat', account_id='a1')


@pytest.mark.asyncio
@pytest.mark.parametrize('params', [PARAMS, {}, {**PARAMS, 'statuses': 'reviewed,in_progress', 'report_scope': 'dashboard'}])
async def test_retired_endpoint_never_calls_campaign_service(route_app, monkeypatch, params):
    async def forbidden(*args, **kwargs):
        raise AssertionError('disabled product linking reached campaign service')
    monkeypatch.setattr(estimates, 'build_product_promotion_estimates', forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=route_app[0]), base_url='http://test') as client:
        result = await client.get('/dashboard-v2/product-promotion-estimates', params=params)
    assert result.status_code == 410
    assert result.json()['detail']['code'] == 'product_campaign_linking_disabled'


@pytest.mark.asyncio
async def test_non_owner_denied_before_service(route_app, monkeypatch):
    async def employee():
        return {'id': 'employee', 'role': 'employee'}
    route_app[0].dependency_overrides[route_app[1]] = employee
    async def forbidden(*args, **kwargs):
        raise AssertionError('unauthorized call')
    monkeypatch.setattr(estimates, 'build_product_promotion_estimates', forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=route_app[0]), base_url='http://test') as client:
        result = await client.get('/dashboard-v2/product-promotion-estimates', params=PARAMS)
    assert result.status_code == 403
