"""Product lookup must confirm a SKU or a complete absence before creation."""
from __future__ import annotations

import pytest

from integrations.qoyod_manual.client import ManualQoyodClient, ManualQoyodError


@pytest.fixture
def client():
    return ManualQoyodClient(api_key="synthetic", base_url="https://qoyod.invalid")


def provider_error(status=404):
    return ManualQoyodError(
        status_code=status, endpoint="GET /products", response_excerpt="synthetic"
    )


@pytest.mark.asyncio
async def test_filtered_404_can_find_exact_sku_in_unfiltered_catalog(client, monkeypatch):
    calls = []

    async def request(method, path, **kwargs):
        assert (method, path) == ("GET", "/products")
        params = kwargs["params"]
        calls.append(params)
        if "q[sku_eq]" in params:
            raise provider_error()
        assert params == {"page": 1, "limit": 50}
        return {"products": [{"id": 41, "sku": "OTHER"}, {"id": 42, "sku": "TARGET"}]}

    monkeypatch.setattr(client, "_request", request)
    assert (await client.find_product_by_sku("TARGET"))["id"] == 42
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_complete_catalog_is_reused_but_created_product_invalidates_it(client, monkeypatch):
    calls = []
    created = False

    async def request(method, path, **kwargs):
        nonlocal created
        calls.append((method, kwargs.get("params")))
        if method == "POST":
            created = True
            return {"product": {"id": 43, "sku": "NEW"}}
        if "q[sku_eq]" in kwargs["params"]:
            raise provider_error()
        return {"products": [{"id": 43, "sku": "NEW"}] if created else []}

    monkeypatch.setattr(client, "_request", request)
    assert await client.find_product_by_sku("NEW") is None
    assert await client.find_product_by_sku("OTHER") is None
    assert len(calls) == 2
    await client.create_product({"product": {"sku": "NEW"}}, idem="synthetic-idem")
    assert (await client.find_product_by_sku("NEW"))["id"] == 43


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0, 401, 403, 404, 429, 500])
async def test_filtered_404_does_not_hide_catalog_failure(client, monkeypatch, status):
    async def request(method, path, **kwargs):
        raise provider_error(404 if "q[sku_eq]" in kwargs["params"] else status)

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(ManualQoyodError) as result:
        await client.find_product_by_sku("TARGET")
    assert result.value.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, "html", {"products": ["bad"]}, {"products": None}])
@pytest.mark.parametrize("filtered_404", [False, True])
async def test_unknown_response_cannot_authorize_product_creation(client, monkeypatch, body, filtered_404):
    async def request(method, path, **kwargs):
        if filtered_404 and "q[sku_eq]" in kwargs["params"]:
            raise provider_error()
        return body

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(ManualQoyodError, match="shape unknown"):
        await client.find_product_by_sku("TARGET")


@pytest.mark.asyncio
async def test_product_on_second_page_is_found_and_absence_requires_end(client, monkeypatch):
    from integrations.qoyod_manual import client as module
    monkeypatch.setattr(module, "_PRODUCT_SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(module, "_PRODUCT_SCAN_DELAY_SECONDS", 0)
    pages = []

    async def request(method, path, **kwargs):
        params = kwargs["params"]
        if "q[sku_eq]" in params:
            raise provider_error()
        pages.append(params["page"])
        if params["page"] == 1:
            return {"products": [{"id": 1, "sku": "A"}, {"id": 2, "sku": "B"}]}
        return {"data": {"products": [{"id": 3, "reference": "TARGET"}]}}

    monkeypatch.setattr(client, "_request", request)
    assert (await client.find_product_by_sku("TARGET"))["id"] == 3
    assert await client.find_product_by_sku("ABSENT") is None
    assert pages == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["cap", "404", "repeated"])
async def test_incomplete_catalog_never_becomes_confirmed_absence(client, monkeypatch, end):
    from integrations.qoyod_manual import client as module
    monkeypatch.setattr(module, "_PRODUCT_SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(module, "_PRODUCT_SCAN_MAX_PAGES", 2)
    monkeypatch.setattr(module, "_PRODUCT_SCAN_DELAY_SECONDS", 0)

    async def request(method, path, **kwargs):
        params = kwargs["params"]
        if "q[sku_eq]" in params:
            raise provider_error()
        page = params["page"]
        if page == 2 and end == "404":
            raise provider_error()
        offset = 0 if end == "repeated" else page * 10
        return {"products": [{"id": offset + i, "sku": f"SKU-{offset + i}"} for i in (1, 2)]}

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(ManualQoyodError):
        await client.find_product_by_sku("ABSENT")


@pytest.mark.asyncio
async def test_exact_filtered_match_preserves_first_match_behavior(client, monkeypatch):
    async def request(method, path, **kwargs):
        assert kwargs["params"] == {"q[sku_eq]": "TARGET", "limit": 5}
        return {"products": [{"id": 1, "sku": "TARGET"}, {"id": 2, "sku": "TARGET"}]}

    monkeypatch.setattr(client, "_request", request)
    assert (await client.find_product_by_sku("TARGET"))["id"] == 1


@pytest.mark.asyncio
async def test_short_page_with_remaining_total_is_not_a_complete_catalog(client, monkeypatch):
    from integrations.qoyod_manual import client as module
    monkeypatch.setattr(module, "_PRODUCT_SCAN_DELAY_SECONDS", 0)
    pages = []

    async def request(method, path, **kwargs):
        params = kwargs["params"]
        if "q[sku_eq]" in params:
            raise provider_error()
        page = params["page"]
        pages.append(page)
        return {"meta": {"total": 2}, "products": [
            {"id": page, "sku": "A" if page == 1 else "TARGET"}
        ]}

    monkeypatch.setattr(client, "_request", request)
    assert (await client.find_product_by_sku("TARGET"))["id"] == 2
    assert pages == [1, 2]


@pytest.mark.asyncio
async def test_uncertain_create_outcome_invalidates_cached_absence(client, monkeypatch):
    posted = False

    async def request(method, path, **kwargs):
        nonlocal posted
        if method == "POST":
            posted = True
            raise provider_error(0)
        if "q[sku_eq]" in kwargs["params"]:
            raise provider_error()
        return {"products": [{"id": 1, "sku": "TARGET"}] if posted else []}

    monkeypatch.setattr(client, "_request", request)
    assert await client.find_product_by_sku("TARGET") is None
    with pytest.raises(ManualQoyodError):
        await client.create_product({"product": {"sku": "TARGET"}}, idem="synthetic")
    assert (await client.find_product_by_sku("TARGET"))["id"] == 1


@pytest.mark.asyncio
async def test_ignored_filters_cannot_hide_product_beyond_lookup_limit(client, monkeypatch):
    async def request(method, path, **kwargs):
        params = kwargs["params"]
        if "page" not in params:
            return {"products": [{"id": 1, "sku": "OTHER"}]}
        return {"products": [{"id": 1, "sku": "OTHER"}, {"id": 2, "sku": "TARGET"}]}

    monkeypatch.setattr(client, "_request", request)
    assert (await client.find_product_by_sku("TARGET"))["id"] == 2
