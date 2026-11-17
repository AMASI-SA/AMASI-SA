"""Connection reuse must not share job state or weaken provider write guards."""
import asyncio

import httpx
import pytest

from integrations.qoyod_manual import client as module


@pytest.fixture
def http_boundary(monkeypatch):
    """Use real HTTPX request/response handling with no network transport."""
    real_client = httpx.AsyncClient
    clients, requests, options = [], [], []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, headers={
            "Set-Cookie": "provider_session=must-not-cross-requests; Path=/",
        })

    handler = {"call": respond}

    async def dispatch(request):
        return await handler["call"](request)

    def make_client(**kwargs):
        options.append(kwargs)
        client = real_client(transport=httpx.MockTransport(dispatch), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(module.httpx, "AsyncClient", make_client)
    return clients, requests, options, handler


def api_client(**kwargs):
    return module.ManualQoyodClient(
        **{"api_key": "synthetic-key", "base_url": "https://qoyod.invalid",
           **kwargs})


@pytest.mark.asyncio
async def test_job_reuses_one_connection_pool_without_headers_or_cookies_leaking(http_boundary):
    clients, requests, options, _ = http_boundary
    async with module.manual_qoyod_http_scope():
        assert clients == []  # Merely entering a job does not open a client.
        first = api_client()
        assert await first._request("POST", "/invoices", idem="invoice-one") == {"ok": True}
        await first._request("GET", "/invoices/1")
        await api_client()._request("POST", "/invoice_payments", idem="payment-one")
        assert len(clients) == 1
        assert not clients[0].is_closed
    assert clients[0].is_closed
    assert options == [{"timeout": 25.0, "follow_redirects": True}]
    assert [request.headers.get("Idempotency-Key") for request in requests] == [
        "invoice-one", None, "payment-one",
    ]
    assert all(request.headers["API-KEY"] == "synthetic-key" for request in requests)
    assert all("Cookie" not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_jobs_and_non_job_calls_get_independent_closed_clients(http_boundary):
    clients, _, _, _ = http_boundary
    client = api_client()
    for _ in range(2):
        async with module.manual_qoyod_http_scope():
            await client._request("GET", "/invoices")
            await client._request("GET", "/products")
        assert clients[-1].is_closed
    assert len(clients) == 2
    # Existing callers which do not opt into a job scope retain their lifecycle.
    await client._request("GET", "/invoices")
    await client._request("GET", "/products")
    assert len(clients) == 4
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_different_credentials_origins_and_timeouts_do_not_share_pool(http_boundary):
    clients, requests, options, _ = http_boundary
    async with module.manual_qoyod_http_scope():
        await api_client()._request("GET", "/invoices")
        await api_client(api_key="other-synthetic-key")._request("GET", "/invoices")
        await api_client(base_url="https://other.invalid")._request("GET", "/invoices")
        await api_client(timeout=7)._request("GET", "/invoices")
    assert len(clients) == 4
    assert all(client.is_closed for client in clients)
    assert [request.headers["API-KEY"] for request in requests] == [
        "synthetic-key", "other-synthetic-key", "synthetic-key", "synthetic-key",
    ]
    assert [request.url.host for request in requests] == [
        "qoyod.invalid", "qoyod.invalid", "other.invalid", "qoyod.invalid",
    ]
    assert [option["timeout"] for option in options] == [25.0, 25.0, 25.0, 7]


@pytest.mark.asyncio
async def test_nested_job_scope_restores_outer_pool(http_boundary):
    clients, _, _, _ = http_boundary
    client = api_client()
    async with module.manual_qoyod_http_scope():
        await client._request("GET", "/invoices")
        outer = clients[0]
        async with module.manual_qoyod_http_scope():
            await client._request("GET", "/products")
        assert clients[1].is_closed
        assert not outer.is_closed
        await client._request("GET", "/customers")
        assert len(clients) == 2
    assert outer.is_closed


@pytest.mark.asyncio
async def test_parallel_jobs_do_not_inherit_each_others_pool(http_boundary):
    clients, requests, _, handler = http_boundary
    both_entered, release = asyncio.Event(), asyncio.Event()

    async def block(request):
        requests.append(request)
        if len(requests) == 2:
            both_entered.set()
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    handler["call"] = block

    async def execute():
        async with module.manual_qoyod_http_scope():
            await api_client()._request("GET", "/invoices")

    tasks = [asyncio.create_task(execute()) for _ in range(2)]
    try:
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        assert len(clients) == 2
        assert all(not client.is_closed for client in clients)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "network", "caller"])
async def test_failed_job_closes_pool_and_never_retries_request(http_boundary, failure):
    clients, requests, _, handler = http_boundary

    async def fail(request):
        requests.append(request)
        if failure == "network":
            raise httpx.ConnectError("synthetic network loss", request=request)
        return httpx.Response(429 if failure == "http" else 200, json={"error": "synthetic"})

    handler["call"] = fail
    with pytest.raises(module.ManualQoyodError if failure != "caller" else ValueError) as error:
        async with module.manual_qoyod_http_scope():
            await api_client()._request("POST", "/invoices", idem="one-attempt")
            raise ValueError("synthetic caller failure")
    assert len(requests) == len(clients) == 1
    assert clients[0].is_closed
    if failure != "caller":
        assert error.value.status_code == (429 if failure == "http" else 0)
        assert error.value.endpoint == "POST /invoices"


@pytest.mark.asyncio
async def test_cancellation_closes_in_flight_job_pool(http_boundary):
    clients, requests, _, handler = http_boundary
    entered = asyncio.Event()

    async def block(request):
        requests.append(request)
        entered.set()
        await asyncio.Event().wait()

    handler["call"] = block

    async def execute():
        async with module.manual_qoyod_http_scope():
            await api_client()._request("POST", "/invoices", idem="interrupted")

    task = asyncio.create_task(execute())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == len(clients) == 1
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_each_write_rechecks_guard_and_denied_write_never_reaches_transport(http_boundary):
    clients, requests, _, _ = http_boundary
    checks = []

    async def guard():
        checks.append(len(requests))
        if len(checks) == 3:
            raise RuntimeError("manual_job_authority_changed")

    token = module.begin_job_write_guard(guard)
    try:
        async with module.manual_qoyod_http_scope():
            client = api_client()
            await client._request("GET", "/invoices")
            await client._request("POST", "/customers", idem="customer")
            await client._request("POST", "/invoices", idem="invoice")
            with pytest.raises(RuntimeError, match="manual_job_authority_changed"):
                await client._request("POST", "/invoice_payments", idem="payment")
    finally:
        module.end_job_write_guard(token)
    assert checks == [1, 2, 3]
    assert [request.url.path for request in requests] == ["/invoices", "/customers", "/invoices"]
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.asyncio
async def test_waiting_write_checks_revoked_authority_after_pool_is_released(http_boundary):
    clients, requests, _, handler = http_boundary
    reading, release = asyncio.Event(), asyncio.Event()
    permitted = True
    checks = []

    async def guard():
        checks.append(permitted)
        if not permitted:
            raise RuntimeError("manual_job_authority_changed")

    async def block(request):
        requests.append(request)
        reading.set()
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    handler["call"] = block
    token = module.begin_job_write_guard(guard)
    try:
        async with module.manual_qoyod_http_scope():
            client = api_client()
            read = asyncio.create_task(client._request("GET", "/invoices"))
            await asyncio.wait_for(reading.wait(), timeout=1)
            write = asyncio.create_task(client._request("POST", "/invoices", idem="denied"))
            await asyncio.sleep(0)
            assert checks == []
            permitted = False
            release.set()
            await read
            with pytest.raises(RuntimeError, match="manual_job_authority_changed"):
                await write
    finally:
        release.set()
        module.end_job_write_guard(token)
    assert checks == [False]
    assert len(requests) == 1
    assert len(clients) == 1 and clients[0].is_closed
