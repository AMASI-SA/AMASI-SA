"""Qoyod API Endpoint Contract — locks in the verified paths.

Per the 2026-06-26 endpoint audit, Qoyod's legacy.qoyod.com domain
uses a specific set of paths for the 6 operations the pipeline cares
about. This test inspects the API client's internal source so that
any silent rename in the future is caught BEFORE going live.

We do this via white-box source inspection (no live network call) so
the test stays deterministic in CI.
"""
from __future__ import annotations

import inspect
import re
import httpx
import pytest

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError


# (method_name, expected_http_verb, expected_path)
LOCKED_ENDPOINTS = [
    # Customer side
    ("list_contacts",  "GET",  "/customers"),
    ("create_contact", "POST", "/customers"),
    # Product side
    ("list_products",  "GET",  "/products"),
    ("create_product", "POST", "/products"),
    # Invoice & Receipt
    ("create_invoice", "POST", "/invoices"),
    ("create_receipt", "POST", "/receipts"),
    # Test connection probe (Qoyod retired /me)
    ("me",             "GET",  "/products"),
]


@pytest.mark.parametrize("method_name,verb,path", LOCKED_ENDPOINTS)
def test_qoyod_client_uses_locked_endpoint(method_name, verb, path):
    fn = getattr(QoyodAPIClient, method_name)
    src = inspect.getsource(fn)
    # Look for `self._request("VERB", "PATH"` (allowing for keyword args after).
    pattern = re.compile(
        rf'self\._request\(\s*["\']{re.escape(verb)}["\']\s*,\s*["\']{re.escape(path)}["\']')
    assert pattern.search(src), (
        f"{method_name} does NOT call self._request({verb!r}, {path!r}, ...). "
        f"This breaks the Go-Live endpoint contract.\n"
        f"Source:\n{src}"
    )


def test_no_residual_contacts_post_in_api_client_source():
    """Defence in depth — the legacy POST /contacts shape (which Qoyod
    keeps alive but doesn't auth-check) MUST NOT come back without a
    code review."""
    import integrations.qoyod.api_client as mod
    source = inspect.getsource(mod)
    # Permit the docstring reference to /contacts but block any actual
    # _request("POST", "/contacts" call.
    bad = re.search(r'_request\(\s*["\']POST["\']\s*,\s*["\']/contacts["\']', source)
    assert bad is None, "POST /contacts is forbidden — use POST /customers."


@pytest.mark.asyncio
async def test_product_read_promotes_legacy_client_to_canonical_api(monkeypatch):
    calls = []

    class FakeHTTP:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            calls.append(url)
            request = httpx.Request(method, url)
            if url.startswith("https://legacy.qoyod.com/"):
                return httpx.Response(404, json={"error": "not found"}, request=request)
            return httpx.Response(
                200,
                json={"products": [{"id": 42, "sku": "TARGET"}]},
                request=request,
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeHTTP)
    client = QoyodAPIClient(
        "synthetic",
        base_url="https://legacy.qoyod.com/api/2.0",
    )

    body = await client.list_products(page=1, limit=1)

    assert body["products"][0]["id"] == 42
    assert calls == [
        "https://legacy.qoyod.com/api/2.0/products",
        "https://api.qoyod.com/2.0/products",
    ]
    assert client._base_url == "https://api.qoyod.com/2.0"


@pytest.mark.asyncio
async def test_canonical_product_failure_stays_fail_closed(monkeypatch):
    class FakeHTTP:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            request = httpx.Request(method, url)
            status = 404 if url.startswith("https://legacy.qoyod.com/") else 401
            return httpx.Response(status, json={"error": "refused"}, request=request)

    monkeypatch.setattr(httpx, "AsyncClient", FakeHTTP)
    client = QoyodAPIClient(
        "synthetic",
        base_url="https://legacy.qoyod.com/api/2.0",
    )

    with pytest.raises(QoyodAPIError) as result:
        await client.list_products(page=1, limit=1)

    assert result.value.status_code == 401
    assert client._base_url == "https://legacy.qoyod.com/api/2.0"
