from __future__ import annotations

import pytest

from integrations.qoyod_manual.client import (
    ManualQoyodClient,
    ManualQoyodError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [0, 401, 403, 429, 500])
@pytest.mark.parametrize(
    "method,args",
    [
        ("find_invoice_by_reference", ("283500003",)),
        ("find_customers_by_phone", ("0500000000",)),
        ("find_customers_by_email", ("customer@example.test",)),
        ("find_product_by_sku", ("SKU-1",)),
    ],
)
async def test_lookup_errors_that_make_absence_unknown_fail_closed(
    monkeypatch,
    status_code,
    method,
    args,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )
    calls = []

    async def fail_request(http_method, path, **kwargs):
        calls.append((http_method, path, kwargs.get("params")))
        raise ManualQoyodError(
            status_code=status_code,
            endpoint=f"{http_method} {path}",
            response_excerpt="safe synthetic failure",
        )

    monkeypatch.setattr(client, "_request", fail_request)

    with pytest.raises(ManualQoyodError) as captured:
        await getattr(client, method)(*args)

    assert captured.value.status_code == status_code
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("find_invoice_by_reference", ("283500004",)),
        ("find_customers_by_phone", ("0500000000",)),
        ("find_product_by_sku", ("SKU-2",)),
    ],
)
async def test_all_query_shape_failures_do_not_claim_a_safe_absence(
    monkeypatch,
    method,
    args,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )
    calls = []

    async def reject_shape(http_method, path, **kwargs):
        calls.append((http_method, path, kwargs.get("params")))
        raise ManualQoyodError(
            status_code=422,
            endpoint=f"{http_method} {path}",
            response_excerpt="unsupported query shape",
        )

    monkeypatch.setattr(client, "_request", reject_shape)

    with pytest.raises(ManualQoyodError) as captured:
        await getattr(client, method)(*args)

    assert captured.value.status_code == 422
    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_successful_empty_invoice_lookup_remains_a_confirmed_absence(
    monkeypatch,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )

    async def empty_lookup(http_method, path, **kwargs):
        assert http_method == "GET"
        assert path == "/invoices"
        return {"invoices": []}

    monkeypatch.setattr(client, "_request", empty_lookup)

    assert await client.find_invoice_by_reference("283500005") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args,path,body,expected",
    [
        (
            "find_invoice_by_reference",
            ("283500006",),
            "/invoices",
            {"invoices": [{"id": 77, "reference": "283500006"}]},
            {"id": 77, "reference": "283500006"},
        ),
        (
            "find_customers_by_phone",
            ("0500000000",),
            "/customers",
            {"customers": [{"id": 88, "phone": "0500000000"}]},
            [{"id": 88, "phone": "0500000000"}],
        ),
        (
            "find_product_by_sku",
            ("SKU-3",),
            "/products",
            {"products": [{"id": 99, "sku": "SKU-3"}]},
            {"id": 99, "sku": "SKU-3"},
        ),
    ],
)
async def test_exact_first_query_match_stops_before_later_provider_failure(
    monkeypatch,
    method,
    args,
    path,
    body,
    expected,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )
    calls = []

    async def first_match_then_throttle(http_method, request_path, **kwargs):
        calls.append((http_method, request_path, kwargs.get("params")))
        if len(calls) == 1:
            assert http_method == "GET"
            assert request_path == path
            return body
        raise ManualQoyodError(
            status_code=429,
            endpoint=f"{http_method} {request_path}",
            response_excerpt="synthetic throttle after exact match",
        )

    monkeypatch.setattr(client, "_request", first_match_then_throttle)

    assert await getattr(client, method)(*args) == expected
    assert len(calls) == 1
