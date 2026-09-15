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
async def test_invoice_filter_404_uses_complete_unfiltered_snapshot(
    monkeypatch,
):
    """Qoyod can reject the reference filter while its list API still works."""
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )
    calls = []

    async def filter_404_then_list(http_method, path, **kwargs):
        params = kwargs.get("params") or {}
        calls.append((http_method, path, params))
        if "q[reference_eq]" in params:
            raise ManualQoyodError(
                status_code=404,
                endpoint=f"{http_method} {path}",
                response_excerpt="unsupported reference filter",
            )
        assert params == {"page": 1, "limit": 50}
        return {
            "invoices": [
                {"id": 700, "reference": "283500099"},
                {"id": 701, "reference": "283500007"},
            ]
        }

    monkeypatch.setattr(client, "_request", filter_404_then_list)

    assert await client.find_invoice_by_reference("283500007") == {
        "id": 701,
        "reference": "283500007",
    }
    assert calls == [
        (
            "GET",
            "/invoices",
            {"q[reference_eq]": "283500007", "limit": 3},
        ),
        ("GET", "/invoices", {"page": 1, "limit": 50}),
    ]


@pytest.mark.asyncio
async def test_complete_invoice_snapshot_is_reused_for_later_lookup(
    monkeypatch,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )
    calls = []

    async def filter_404_then_list(http_method, path, **kwargs):
        params = kwargs.get("params") or {}
        calls.append((http_method, path, params))
        if "q[reference_eq]" in params:
            raise ManualQoyodError(
                status_code=404,
                endpoint=f"{http_method} {path}",
                response_excerpt="unsupported reference filter",
            )
        return {
            "data": {
                "invoices": [
                    {"id": 801, "reference": "283500008"},
                    {"id": 802, "reference": "283500009"},
                ]
            }
        }

    monkeypatch.setattr(client, "_request", filter_404_then_list)

    assert (await client.find_invoice_by_reference("283500008"))["id"] == 801
    assert (await client.find_invoice_by_reference("283500009"))["id"] == 802
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_status", [0, 401, 403, 429, 500])
async def test_invoice_filter_404_keeps_snapshot_failures_closed(
    monkeypatch,
    fallback_status,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )

    async def fail_snapshot(http_method, path, **kwargs):
        params = kwargs.get("params") or {}
        status_code = (
            404 if "q[reference_eq]" in params else fallback_status
        )
        raise ManualQoyodError(
            status_code=status_code,
            endpoint=f"{http_method} {path}",
            response_excerpt="safe synthetic failure",
        )

    monkeypatch.setattr(client, "_request", fail_snapshot)

    with pytest.raises(ManualQoyodError) as captured:
        await client.find_invoice_by_reference("283500010")

    assert captured.value.status_code == fallback_status


@pytest.mark.asyncio
async def test_invoice_filter_and_first_unfiltered_page_404_fail_closed(
    monkeypatch,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )

    async def all_404(http_method, path, **kwargs):
        raise ManualQoyodError(
            status_code=404,
            endpoint=f"{http_method} {path}",
            response_excerpt="not found",
        )

    monkeypatch.setattr(client, "_request", all_404)

    with pytest.raises(ManualQoyodError) as captured:
        await client.find_invoice_by_reference("283500014")

    assert captured.value.status_code == 404


@pytest.mark.asyncio
async def test_invoice_snapshot_refuses_max_page_truncation(monkeypatch):
    from integrations.qoyod_manual import client as client_module

    monkeypatch.setattr(client_module, "_INVOICE_SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(client_module, "_INVOICE_SCAN_MAX_PAGES", 2)
    monkeypatch.setattr(client_module, "_INVOICE_SCAN_DELAY_SECONDS", 0)
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )

    async def never_finishes(http_method, path, **kwargs):
        params = kwargs.get("params") or {}
        if "q[reference_eq]" in params:
            raise ManualQoyodError(
                status_code=404,
                endpoint=f"{http_method} {path}",
                response_excerpt="unsupported reference filter",
            )
        page = params["page"]
        return {
            "invoices": [
                {"id": page * 10 + offset, "reference": f"ref-{page}-{offset}"}
                for offset in range(2)
            ]
        }

    monkeypatch.setattr(client, "_request", never_finishes)

    with pytest.raises(ManualQoyodError) as captured:
        await client.find_invoice_by_reference("283500011")

    assert captured.value.status_code == 0
    assert "incomplete" in captured.value.response_excerpt


@pytest.mark.asyncio
async def test_invoice_snapshot_refuses_unknown_or_malformed_page(monkeypatch):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )

    async def unknown_page(http_method, path, **kwargs):
        params = kwargs.get("params") or {}
        if "q[reference_eq]" in params:
            raise ManualQoyodError(
                status_code=404,
                endpoint=f"{http_method} {path}",
                response_excerpt="unsupported reference filter",
            )
        return {"invoices": [{"id": 1}, "not-an-invoice-row"]}

    monkeypatch.setattr(client, "_request", unknown_page)

    with pytest.raises(ManualQoyodError) as captured:
        await client.find_invoice_by_reference("283500012")

    assert captured.value.status_code == 0
    assert "shape unknown" in captured.value.response_excerpt


@pytest.mark.asyncio
async def test_successful_create_updates_cached_reference_snapshot(
    monkeypatch,
):
    client = ManualQoyodClient(
        api_key="synthetic-key",
        base_url="https://qoyod.invalid",
    )
    client._invoice_reference_snapshot = {}
    calls = []

    async def create(http_method, path, **kwargs):
        calls.append((http_method, path))
        assert http_method == "POST"
        assert path == "/invoices"
        return {"invoice": {"id": 901}}

    monkeypatch.setattr(client, "_request", create)
    await client.create_invoice(
        {"invoice": {"reference": "283500013"}},
        idem="inv-283500013",
    )

    assert await client.find_invoice_by_reference("283500013") == {
        "id": 901,
        "reference": "283500013",
    }
    assert calls == [("POST", "/invoices")]


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
