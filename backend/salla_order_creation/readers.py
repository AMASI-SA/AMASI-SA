"""Read-only Salla source facts for assisted order research and draft selection.

These facts NEVER become a final server-confirmed checkout quote on their own.
The shipping-rate endpoint may not know a new cart's weight/options, and
product option price/inventory can change. Fail closed on missing scopes.

Official endpoints (2026-10-06):
  GET /products/{product} requires products.read
  GET /shipping/companies/estimate-rate with city_id/country_id/etc.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

# Import the full Salla runtime lazily: this pure reader and its synthetic
# tests must not require FastAPI or Mongo connections merely at import time.


def _resolve_salla_readers(request, integration_reader):
    if request is None or integration_reader is None:
        from salla_integration.service import call_salla, get_integration
        request = request or call_salla
        integration_reader = integration_reader or get_integration
    return request, integration_reader


class SourceUnavailable(ValueError):
    pass


def _positive_id(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise SourceUnavailable("positive_provider_id_required")
    return value


def _money_sar(value: Any) -> str:
    if not isinstance(value, dict) or value.get("currency") != "SAR":
        raise SourceUnavailable("sar_amount_unavailable")
    try:
        amount = Decimal(str(value["amount"]))
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise SourceUnavailable("invalid_provider_amount") from exc
    if (not amount.is_finite() or amount < 0 or
            amount.as_tuple().exponent < -2):
        raise SourceUnavailable("untrusted_provider_amount")
    return f"{amount:.2f}"


async def _scope(db: Any, user_id: str, scope: str, read_integration) -> None:
    if not user_id:
        raise SourceUnavailable("merchant_scope_missing")
    record = await read_integration(db, user_id)
    scopes = str((record or {}).get("scope") or "").split()
    if not record or record.get("status") != "connected" or scope not in scopes:
        raise SourceUnavailable(f"{scope}_not_granted")


def _reply_data(response: Any, *, expected_list: bool) -> Any:
    if not isinstance(response, dict) or response.get("success") is False:
        raise SourceUnavailable("salla_read_unsuccessful")
    data = response.get("data")
    valid = isinstance(data, list) if expected_list else isinstance(data, dict)
    if not valid:
        raise SourceUnavailable("salla_read_shape_invalid")
    return data


async def read_exact_product(
    db: Any, user_id: str, product_id: int,
    *, request: Callable[..., Awaitable[dict]] | None = None,
    integration_reader: Callable[..., Awaitable[dict]] | None = None,
) -> dict:
    """Return current catalog facts, never a ready-to-POST final quote."""
    identifier = _positive_id(product_id)
    request, integration_reader = _resolve_salla_readers(request, integration_reader)
    await _scope(db, user_id, "products.read", integration_reader)
    response = await request(db, user_id, "GET", f"/products/{identifier}")
    product = _reply_data(response, expected_list=False)
    if product.get("id") != identifier:
        raise SourceUnavailable("product_id_mismatch")
    if product.get("is_available") is not True or product.get("status") != "sale":
        raise SourceUnavailable("product_not_sellable")

    # Quantity may be a string. An unknown stock field cannot be called "in stock".
    inventory = product.get("quantity")
    unlimited = product.get("unlimited_quantity") is True
    if not unlimited:
        if isinstance(inventory, bool) or not str(inventory).isdigit():
            raise SourceUnavailable("product_quantity_unverified")
        if int(inventory) < 1:
            raise SourceUnavailable("product_out_of_stock")
    amount = _money_sar(product.get("taxed_price"))
    option_facts = []
    for opt in product.get("options") or []:
        if not isinstance(opt, dict) or type(opt.get("id")) is not int:
            raise SourceUnavailable("product_options_incomplete")
        values = []
        for value in opt.get("values") or []:
            if not isinstance(value, dict) or type(value.get("id")) is not int:
                raise SourceUnavailable("option_value_invalid")
            values.append({
                "value_id": value["id"],
                "name": str(value.get("name") or "")[:120],
                "out_of_stock": value.get("is_out_of_stock") is True,
                "price_delta_sar": _money_sar(value.get("price")),
            })
        option_facts.append({
            "option_id": opt["id"],
            "name": str(opt.get("name") or "")[:120],
            "required": opt.get("required") is True,
            "values": values,
        })
    return {
        "product_id": identifier,
        "name": str(product.get("name") or "")[:250],
        "taxed_base_price_sar": amount,
        "available_quantity": None if unlimited else int(inventory),
        "options": option_facts,
        "source": "salla_product_details",
        "can_use_as_final_quote": False,
    }


async def read_shipping_rate_candidates(
    db: Any, user_id: str,
    *, country_id: int, city_id: int, postal_code: str | None = None,
    request: Callable[..., Awaitable[dict]] = call_salla,
    integration_reader: Callable[..., Awaitable[dict]] = get_integration,
) -> list[dict]:
    """Read eligible-looking rate estimates, never certify a final cart price."""
    country_id = _positive_id(country_id)
    city_id = _positive_id(city_id)
    if postal_code is not None:
        if (not isinstance(postal_code, str) or len(postal_code) > 20 or
                not postal_code.isalnum()):
            raise SourceUnavailable("invalid_postal_code")
    request, integration_reader = _resolve_salla_readers(request, integration_reader)
    await _scope(db, user_id, "shipping.read_write", integration_reader)
    params: dict = {"country_id": country_id, "city_id": city_id}
    if postal_code:
        params["postal_code"] = postal_code
    response = await request(
        db, user_id, "GET", "/shipping/companies/estimate-rate", params=params
    )
    rows = _reply_data(response, expected_list=True)
    if len(rows) > 100:
        raise SourceUnavailable("too_many_shipping_rates")
    rates = []
    for row in rows:
        if not isinstance(row, dict):
            raise SourceUnavailable("invalid_shipping_rate_row")
        rate_id = _positive_id(row.get("id"))
        company_id = _positive_id(row.get("company_id"))
        amount = _money_sar(row.get("total"))
        cod_fees = []
        for service in row.get("services") or []:
            if not isinstance(service, dict):
                raise SourceUnavailable("invalid_carrier_service")
            if service.get("name") == "cod":
                cod_fees.append(_money_sar(service.get("amount")))
        rates.append({
            "rate_id": rate_id,
            "shipping_company_id": company_id,
            "name": str(row.get("title") or "")[:180],
            "estimated_shipping_sar": amount,
            "cod_service_found": bool(cod_fees),
            "cod_fee_estimate_sar": cod_fees[0] if len(cod_fees) == 1 else None,
            "source": "salla_shipping_rate_estimate",
            "is_final_cart_quote": False,
        })
    return rates
