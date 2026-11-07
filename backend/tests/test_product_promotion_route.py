"""HTTP boundary: authenticated owner, applied filters, validation and deadline."""
from dataclasses import replace
import asyncio
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
async def test_applied_cohort_and_owner_forwarded(route_app, monkeypatch):
    calls = []
    async def build(db, owner, **kwargs):
        calls.append((owner, kwargs))
        return {'rows': [], 'currency': 'SAR', 'allocation_method': 'product_sales_share'}
    monkeypatch.setattr(estimates, 'build_product_promotion_estimates', build)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=route_app[0]), base_url='http://test') as client:
        params = {**PARAMS, 'statuses': 'reviewed,in_progress', 'report_scope': 'dashboard',
                  'payment_methods': 'mada', 'shipping_companies': 'smsa', 'user_id': 'forged-owner'}
        result = await client.get('/dashboard-v2/product-promotion-estimates', params=params)
    assert result.status_code == 200
    assert calls == [('authenticated-owner', {key: value for key, value in params.items() if key != 'user_id'})]


@pytest.mark.asyncio
@pytest.mark.parametrize('override', [dict(from_date='2026-11-09'), dict(from_date='bad'),
    dict(platform='invalid'), dict(report_scope='invalid'), dict(statuses='invalid')])
async def test_invalid_queries_do_not_call_service(route_app, monkeypatch, override):
    async def forbidden(*args, **kwargs):
        raise AssertionError('invalid query reached service')
    monkeypatch.setattr(estimates, 'build_product_promotion_estimates', forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=route_app[0]), base_url='http://test') as client:
        result = await client.get('/dashboard-v2/product-promotion-estimates', params={**PARAMS, **override})
    assert result.status_code == 422


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


@pytest.mark.asyncio
async def test_timeout_is_incomplete_not_zero(route_app, monkeypatch):
    async def expired(*args, **kwargs):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(estimates, 'build_product_promotion_estimates', expired)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=route_app[0]), base_url='http://test') as client:
        result = await client.get('/dashboard-v2/product-promotion-estimates', params=PARAMS)
    assert result.status_code == 503
    assert result.json()['detail'] == {'code': 'promotion_estimate_timeout', 'data_complete': False}
