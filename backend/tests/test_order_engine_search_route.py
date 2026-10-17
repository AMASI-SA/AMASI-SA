from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from order_engine import _original_make_order_engine_router
from tests.test_order_engine_search_bounds import Stream, row


def client(role='owner'):
    async def actor(): return {'id': 'owner', 'role': role}
    app = FastAPI()
    app.include_router(_original_make_order_engine_router(SimpleNamespace(), actor,
        repository_factory=lambda db: Stream([row(str(i)) for i in range(61)])))
    return TestClient(app)


def test_real_route_product_search_and_next_page():
    with client() as api:
        first = api.get('/orders-v2', params={'product_id': '123456', 'limit': 50})
        assert first.status_code == 200
        result = first.json()
        assert len(result['items']) == 50 and result['result_summary']['orders'] == 61
        second = api.get('/orders-v2', params={'product_id': '123456', 'limit': 50, 'cursor': result['next_cursor']})
        assert second.status_code == 200 and len(second.json()['items']) == 11


def test_real_route_invalid_period_and_owner_boundary():
    with client() as api:
        response = api.get('/orders-v2', params={'campaign_id': '12345678901234567890', 'created_from': 'not-a-date'})
        assert response.status_code == 422 and response.json()['detail']['code'] == 'invalid_search_period'
    with client('employee') as api:
        assert api.get('/orders-v2', params={'product_id': '123456'}).status_code == 403
