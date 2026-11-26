"""Synthetic-only official Salla read-client tests; no HTTP requests."""

import asyncio
import pytest

from salla_order_creation.readers import (
    SourceUnavailable, read_exact_product, read_shipping_rate_candidates,
)


def integration(scope):
    async def read(db, user_id):
        return {"status": "connected", "scope": scope}
    return read


def product_response(**changes):
    data = {
        "id": 123, "name": "Synthetic gift", "status": "sale",
        "is_available": True, "quantity": "3", "unlimited_quantity": False,
        "taxed_price": {"amount": 90.50, "currency": "SAR"},
        "options": [
            {"id": 500, "name": "Color", "required": True, "values": [
                {"id": 701, "name": "Red", "is_out_of_stock": False,
                 "price": {"amount": 0, "currency": "SAR"}},
            ]},
        ],
    }
    data.update(changes)
    return {"success": True, "data": data}


def shipping_response(**changes):
    row = {
        "id": 5001, "company_id": 6002, "title": "Synthetic carrier",
        "total": {"amount": 20, "currency": "SAR"},
        "services": [{"name": "cod", "amount": {"amount": 3, "currency": "SAR"}}],
    }
    row.update(changes)
    return {"success": True, "data": [row]}


def test_product_reader_uses_only_exact_product_endpoint():
    seen = []
    async def get(*args, **kwargs):
        seen.append((args, kwargs))
        return product_response()
    result = asyncio.run(read_exact_product(
        None, "owner", 123, request=get,
        integration_reader=integration("orders.read_write products.read"),
    ))
    assert seen[0][0][2:] == ("GET", "/products/123")
    assert "params" not in seen[0][1]
    assert result["product_id"] == 123
    assert result["taxed_base_price_sar"] == "90.50"
    assert result["available_quantity"] == 3
    assert result["options"][0]["values"][0]["value_id"] == 701
    assert result["can_use_as_final_quote"] is False


def test_product_scope_missing_denied_before_network():
    async def bad(*args, **kwargs):
        raise AssertionError("network call not allowed")
    with pytest.raises(SourceUnavailable, match="products.read_not_granted"):
        asyncio.run(read_exact_product(
            None, "owner", 123, request=bad,
            integration_reader=integration("orders.read_write"),
        ))


@pytest.mark.parametrize("invalid", [0, -2, "123", True])
def test_product_id_type_is_strict(invalid):
    async def bad(*args, **kwargs):
        raise AssertionError("network call not allowed")
    with pytest.raises(SourceUnavailable):
        asyncio.run(read_exact_product(
            None, "owner", invalid, request=bad,
            integration_reader=integration("products.read"),
        ))


@pytest.mark.parametrize("changed", [
    {"id": 999},
    {"is_available": False},
    {"status": "hidden"},
    {"quantity": "0"},
    {"quantity": None},
    {"taxed_price": {"amount": 90, "currency": "KWD"}},
    {"options": [{"id": 500, "values": [{"name": "No ID"}]}]},
])
def test_bad_product_facts_fail_closed(changed):
    async def get(*args, **kwargs):
        return product_response(**changed)
    with pytest.raises(SourceUnavailable):
        asyncio.run(read_exact_product(
            None, "owner", 123, request=get,
            integration_reader=integration("products.read"),
        ))


def test_shipping_reader_does_not_claim_estimate_as_final_cart_quote():
    seen = []
    async def get(*args, **kwargs):
        seen.append((args, kwargs))
        return shipping_response()
    items = asyncio.run(read_shipping_rate_candidates(
        None, "owner", country_id=1473353380, city_id=1939592358,
        postal_code="12345", request=get,
        integration_reader=integration("shipping.read_write"),
    ))
    assert seen[0][0][2:] == ("GET", "/shipping/companies/estimate-rate")
    assert seen[0][1]["params"] == {
        "country_id": 1473353380, "city_id": 1939592358,
        "postal_code": "12345",
    }
    assert len(items) == 1
    assert items[0]["rate_id"] == 5001
    assert items[0]["shipping_company_id"] == 6002
    assert items[0]["estimated_shipping_sar"] == "20.00"
    assert items[0]["cod_fee_estimate_sar"] == "3.00"
    assert items[0]["is_final_cart_quote"] is False


def test_shipping_missing_scope_denied_before_network():
    async def bad(*args, **kwargs):
        raise AssertionError("no call")
    with pytest.raises(SourceUnavailable):
        asyncio.run(read_shipping_rate_candidates(
            None, "owner", country_id=1, city_id=2, request=bad,
            integration_reader=integration("products.read"),
        ))


@pytest.mark.parametrize("invalid", ["", "../secrets", "12345 678", "1"*50])
def test_invalid_shipping_postcode_rejected(invalid):
    async def bad(*args, **kwargs):
        raise AssertionError("no call")
    with pytest.raises(SourceUnavailable):
        asyncio.run(read_shipping_rate_candidates(
            None, "owner", country_id=1, city_id=2, postal_code=invalid,
            request=bad, integration_reader=integration("shipping.read_write"),
        ))


def test_foreign_currency_carrier_denied():
    async def get(*args, **kwargs):
        return shipping_response(total={"amount": 20, "currency": "KWD"})
    with pytest.raises(SourceUnavailable, match="sar_amount_unavailable"):
        asyncio.run(read_shipping_rate_candidates(
            None, "owner", country_id=1, city_id=2, request=get,
            integration_reader=integration("shipping.read_write"),
        ))


def test_salla_unsuccessful_product_response_is_denied():
    async def get(*args, **kwargs):
        return {"success": False, "data": product_response()["data"]}
    with pytest.raises(SourceUnavailable, match="salla_read_unsuccessful"):
        asyncio.run(read_exact_product(
            None, "owner", 123, request=get,
            integration_reader=integration("products.read"),
        ))



def test_product_details_read_write_scope_is_valid_for_read_only_get():
    async def get(*args, **kwargs):
        return product_response()
    row = asyncio.run(read_exact_product(
        None, "owner", 123, request=get,
        integration_reader=integration("orders.read_write products.read_write"),
    ))
    assert row["product_id"] == 123
    assert row["can_use_as_final_quote"] is False


def test_shipping_read_only_scope_can_read_estimates_without_write():
    async def get(*args, **kwargs):
        return shipping_response()
    quotes = asyncio.run(read_shipping_rate_candidates(
        None, "owner", country_id=1, city_id=2, request=get,
        integration_reader=integration("shipping.read"),
    ))
    assert len(quotes) == 1
    assert quotes[0]["is_final_cart_quote"] is False
