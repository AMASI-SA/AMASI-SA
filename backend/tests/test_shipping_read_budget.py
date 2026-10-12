from unittest.mock import AsyncMock
import httpx
import pytest
import shipping_read_budget as reads
from salla_integration import service


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 302, 401, 403, 429, 500])
async def test_transport_never_posts_refreshes_or_replays(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"data": {}}, headers={"Location": "https://unexpected.test"})
    client = httpx.AsyncClient
    monkeypatch.setattr(reads.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(service, "get_integration", AsyncMock(return_value={"synthetic": True}))
    monkeypatch.setattr(service, "_decrypt_access", lambda row: "synthetic-token")
    refresh = AsyncMock(side_effect=AssertionError("OAuth POST forbidden"))
    monkeypatch.setattr(service, "ensure_fresh_access_token", refresh)
    with reads.read_budget(1):
        if status == 200:
            assert await reads.call_salla(None, "owner", "GET", "/orders/1") == {"data": {}}
        else:
            with pytest.raises(service.SallaError):
                await reads.call_salla(None, "owner", "GET", "/orders/1")
        with pytest.raises(service.SallaError):
            await reads.call_salla(None, "owner", "GET", "/orders/1")
    assert len(calls) == 1 and calls[0].method == "GET"
    refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_mutation_rejected_before_credentials(monkeypatch, method):
    credentials = AsyncMock(side_effect=AssertionError("must fail before IO"))
    monkeypatch.setattr(service, "get_integration", credentials)
    with pytest.raises(service.SallaError):
        await reads.call_salla(None, "owner", method, "/shipments")
    credentials.assert_not_awaited()
