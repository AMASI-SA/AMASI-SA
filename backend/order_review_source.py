"""Versioned, conservative semantic inputs for durable review approval.

Only proven aliases are folded. Conflicting aliases are rejected, never resolved
by precedence. Unknown fields inside business sections remain guarded; this is
not a permissive provider-payload scrubber. No I/O or approval decisions here.
"""
from copy import deepcopy
from decimal import Decimal

from fastapi import HTTPException

FINGERPRINT_VERSION = 2


def _changed():
    raise HTTPException(409, detail={"code": "review_completion_source_changed"})


def _present(value):
    return value is not None and value != "" and value != {} and value != []


def _aliases(row, canonical, *aliases):
    values = [row[key] for key in (canonical, *aliases) if _present(row.get(key))]
    if values and any(value != values[0] for value in values[1:]):
        _changed()
    for key in aliases:
        row.pop(key, None)
    row[canonical] = values[0] if values else None


def _numbers(value):
    # JSON 1 and 1.0 carry the same numeric fact. Strings (including SKU,
    # option answers and telephone numbers) are deliberately NOT coerced.
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            _changed()
        return int(number) if number == number.to_integral_value() else float(number)
    if isinstance(value, dict):
        return {key: _numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_numbers(item) for item in value]
    return value


def _shipping(raw):
    shipping = deepcopy(raw.get("shipping") or {})
    if not isinstance(shipping, dict):
        return shipping  # malformed/new representations cannot equal a valid one
    # These exact scalar aliases are read by salla_shipping._carrier and
    # OrderDTO's mapper; refresh copies company into company_name.
    if not isinstance(shipping.get("company"), dict):
        _aliases(shipping, "company", "company_name")
    elif _present(shipping.get("company_name")):
        if shipping["company"].get("name") != shipping["company_name"]:
            _changed()
        shipping.pop("company_name")
    _aliases(shipping, "method", "shipping_method")
    address = raw.get("shipping_address")
    if _present(address):
        if _present(shipping.get("address")) and shipping["address"] != address:
            _changed()
        shipping["address"] = deepcopy(address)
    return shipping


def canonical_source(snapshot):
    raw = (snapshot.get("raw_by_source") or {}).get("salla_direct") or {}
    # Explicit approval schema, not the whole source envelope. Event clocks,
    # ingestion metadata and order status are not approval inputs. Status and
    # cancellation are independently checked on every validation/commit.
    facts = {key: deepcopy(raw.get(key)) for key in (
        "id", "reference_id", "items", "amounts", "payment_method", "payment_status",
        "customer", "notes", "options", "customer_notes", "staff_notes", "is_gift",
    )}
    facts["shipping"] = _shipping(raw)
    # Root payment facts can supplement amounts and affect payment eligibility.
    facts["payment"] = {key: deepcopy(raw.get(key)) for key in (
        "paid_amount", "remaining_amount", "has_remaining_amount", "payment_collection_status",
    )}
    # Preserve all raw item/options/customer content. Only duplicate aliases
    # with demonstrated identical values are removed, not customer selections.
    customer = facts.get("customer")
    if isinstance(customer, dict):
        _aliases(customer, "full_name", "name")
        _aliases(customer, "mobile", "phone")
    for item in facts.get("items") or []:
        if not isinstance(item, dict):
            continue
        product = item.get("product")
        if isinstance(product, dict) and _present(product.get("id")) and _present(item.get("product_id")):
            if str(product["id"]) != str(item["product_id"]):
                _changed()
            item.pop("product_id")
    return _numbers(facts)


def canonical_order(order):
    row = order.model_dump(mode="json")
    # Keep every field of these DTO business sections, including options,
    # customer selections, recipient/address, quantities, price and eligibility.
    return _numbers({key: row.get(key) for key in (
        "order_id", "order_number", "items", "payment", "totals", "shipping",
        "customer", "customer_notes", "staff_notes", "is_gift",
    )})
