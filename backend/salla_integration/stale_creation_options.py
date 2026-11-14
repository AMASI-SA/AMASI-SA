"""Supplement omitted choices from a delayed, verified order.created event.

The full snapshot remains stale. The ordinary raw merge already preserves
known same-line choices across later sparse events; this omitted-only supplement
restores that arrival-order equivalence without accepting older mutable facts.
All qualification and the existing option-only writer share the owner fence.
No provider request, scheduler, full upsert or fulfillment execution belongs here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from . import cost_option_recovery as recovery
from .order_items_snapshot import validate_order_items


CHOICE_FIELDS = frozenset({
    "options", "options_raw", "options_normalized", "choices", "attributes",
    "product_options", "selected_options", "customer_options", "custom_fields",
    "customizations", "personalization", "fields", "questions", "attachments", "files",
    "option", "variant_options", "option_values", "size", "color", "material", "attribute",
    "variant_name",
})
CANCELLED = frozenset({"cancel", "cancelled", "canceled", "refunded", "ملغي", "ملغى"})


def _result(reason: str, *, applied: bool = False, line_count: int = 0) -> dict[str, Any]:
    return {"attempted": True, "applied": applied, "line_count": line_count, "reason": reason}


def _identity(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("source_identity_unproven")
    text = str(value).strip()
    if not text or text == "0":
        raise ValueError("source_identity_unproven")
    return text


def _aliases(values: list[Any], *, required: bool = False) -> str | None:
    identities = {_identity(value) for value in values if value not in (None, "")}
    if len(identities) > 1 or required and not identities:
        raise ValueError("source_identity_unproven")
    return next(iter(identities), None)


def _object(row: dict, key: str) -> dict:
    value = row.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("source_identity_unproven")
    return value


def _product(row: dict) -> str:
    product = _object(row, "product")
    identity = _aliases([row.get("product_id"), product.get("id")], required=True)
    parent = _aliases([row.get("parent_product_id"), product.get("parent_id")])
    if parent is not None and parent != identity:
        raise ValueError("source_identity_unproven")
    return identity


def _variant(row: dict) -> str | None:
    product = _object(row, "product")
    variants = [_object(row, "variant"), _object(product, "variant")]
    # Label-only variants contain selection evidence without provable identity.
    variant_ids = [_aliases([variant.get("id")], required=bool(variant)) for variant in variants]
    return _aliases([
        row.get("product_sku_id"), row.get("variant_id"),
        product.get("variant_id"), *variant_ids,
    ])


def _number(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("source_contract_unproven")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("source_contract_unproven") from exc
    if not number.is_finite() or number < 0 or positive and number == 0:
        raise ValueError("source_contract_unproven")
    return number


def _currency(value: Any) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str) or len(value.strip()) != 3
            or not value.strip().isascii() or not value.strip().isalpha()):
        raise ValueError("source_contract_unproven")
    return value.strip().upper()


def _money(value: Any, inherited_currency: str | None) -> tuple[Decimal, str]:
    currencies = {inherited_currency} - {None}
    for _ in range(3):
        if not isinstance(value, dict):
            break
        currency = _currency(value.get("currency"))
        if currency:
            currencies.add(currency)
        if "amount" not in value:
            raise ValueError("source_contract_unproven")
        value = value["amount"]
    if len(currencies) != 1:
        raise ValueError("source_contract_unproven")
    return _number(value), next(iter(currencies))


def _commercial(row: dict) -> tuple:
    amounts = _object(row, "amounts")
    currencies = {_currency(row.get("currency")), _currency(amounts.get("currency"))} - {None}
    if len(currencies) > 1:
        raise ValueError("source_contract_unproven")
    currency = next(iter(currencies), None)
    tax = _object(amounts, "tax")
    # These are explicit native amounts, never inferred zero/default values.
    values = tuple(_money(amounts.get(key), currency)
                   for key in ("price_without_tax", "total_discount", "total"))
    tax_value = _money(tax.get("amount"), _currency(tax.get("currency")) or currency)
    if len({value[1] for value in (*values, tax_value)}) != 1:
        raise ValueError("source_contract_unproven")
    return values, _number(tax.get("percent")), tax_value


def _financial_equal(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_financial_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_financial_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool) or left is None or right is None:
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float, Decimal)) or isinstance(right, (int, float, Decimal)):
        return _number(left) == _number(right)
    return type(left) is type(right) and left == right


def _shared_commerce_agrees(current: dict, incoming: dict) -> bool:
    if "currency" in current and "currency" in incoming:
        if not _currency(current["currency"]) or _currency(current["currency"]) != _currency(incoming["currency"]):
            return False
    for snapshot in (current, incoming):
        if "amounts" in snapshot and not isinstance(snapshot["amounts"], dict):
            return False
    current_amounts, incoming_amounts = current.get("amounts", {}), incoming.get("amounts", {})
    # A creation-only original_price is supplemental. If either snapshot shares
    # that or any other amount field, contradictory evidence must refuse the fill.
    return all(_financial_equal(current_amounts[key], incoming_amounts[key])
               for key in set(current_amounts) & set(incoming_amounts))


def _selection_containers(row: dict) -> list[dict]:
    product = _object(row, "product")
    return [row, product, _object(row, "variant"), _object(product, "variant"),
            _object(row, "raw_item")]


def _has_choice_field(row: dict, *, presence: bool) -> bool:
    return any(key in container if presence else bool(container.get(key))
               for container in _selection_containers(row) for key in CHOICE_FIELDS)


def _index(rows: Any, identity_key: str) -> dict[str, dict]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("source_identity_unproven")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("source_identity_unproven")
        identity = _identity(row.get(identity_key))
        if identity_key == "id":
            _aliases([row.get("id"), row.get("item_id"), row.get("order_item_id")], required=True)
        if identity in indexed:
            raise ValueError("source_identity_unproven")
        indexed[identity] = row
    return indexed


def _cancelled(order: dict, raw: dict, payload: dict) -> bool:
    if any(snapshot.get(key) is True for snapshot in (order, raw, payload)
           for key in ("cancelled", "canceled", "is_cancelled", "is_canceled")):
        return True
    values = [order.get("order_status"), order.get("order_status_slug"),
              raw.get("status"), raw.get("status_slug"), payload.get("status"), payload.get("status_slug")]
    for value in values:
        candidates = [value.get(key) for key in ("slug", "name")] if isinstance(value, dict) else [value]
        if any(str(candidate or "").strip().casefold() in CANCELLED for candidate in candidates):
            return True
    return False


def _prove(order: dict, *, user_id: str, order_number: str, payload: dict) -> int:
    from fulfillment_v2_routes import component_provider_version, component_source_time

    if (order.get("user_id") != user_id
            or _aliases([order.get("order_number"), order.get("reference_id")], required=True) != order_number):
        raise ValueError("source_identity_unproven")
    raw = recovery.raw_order(order)
    native_id = _aliases([raw.get("id"), raw.get("order_id")], required=True)
    if _aliases([payload.get("id"), payload.get("order_id")], required=True) != native_id:
        raise ValueError("source_identity_unproven")
    root_native = _aliases([order.get("order_id"), order.get("salla_order_id")])
    if root_native is not None and root_native != native_id:
        raise ValueError("source_identity_unproven")
    for snapshot in (raw, payload):
        if _aliases([snapshot.get("reference_id"), snapshot.get("order_number")], required=True) != order_number:
            raise ValueError("source_identity_unproven")
    watermark = order.get("g47_salla_snapshot")
    if (not isinstance(watermark, dict) or type(watermark.get("revision")) is not int
            or watermark["revision"] < 1 or watermark.get("cancelled")
            or watermark.get("requires_authoritative_refresh") or _cancelled(order, raw, payload)):
        raise ValueError("source_state_blocked")
    # The current raw clock must still be accepted by the unchanged writer.
    version = component_provider_version(raw, created_event=True)
    incoming_version = component_provider_version(payload, created_event=True)
    watermark_version = component_source_time(watermark.get("source_updated_at"))
    status_value = order.get("last_salla_direct_status_source_updated_at")
    status_version = component_source_time(status_value)
    clocks = [watermark_version, status_version]
    if (not version or not watermark_version or watermark_version != version
            or status_value is not None and not status_version
            or not incoming_version or incoming_version >= version
            or any(clock and clock > version for clock in clocks)):
        raise ValueError("source_contract_unproven")
    if not _shared_commerce_agrees(raw, payload):
        raise ValueError("source_contract_unproven")

    validate_order_items(raw.get("items"), order_id=native_id)
    validate_order_items(payload.get("items"), order_id=native_id)
    current = _index(raw.get("items"), "id")
    incoming = _index(payload.get("items"), "id")
    products = _index(order.get("products"), "order_item_id")
    if set(current) != set(incoming) or set(current) != set(products):
        raise ValueError("source_identity_unproven")
    count = 0
    for identity, row in current.items():
        old, product = incoming[identity], products[identity]
        if (_product(row) != _product(old) or _product(row) != _product(product)
                or _variant(row) != _variant(old) or _variant(row) != _variant(product)
                or _number(row.get("quantity"), positive=True) != _number(old.get("quantity"), positive=True)
                or _number(row.get("quantity"), positive=True) != _number(product.get("quantity"), positive=True)):
            raise ValueError("source_identity_unproven")
        if _commercial(row) != _commercial(old) or not _shared_commerce_agrees(row, old):
            raise ValueError("source_contract_unproven")
        # An explicit empty/null raw field is later evidence, not an omission.
        if _has_choice_field(row, presence=True) or _has_choice_field(product, presence=False):
            raise ValueError("current_choice_evidence_present")
        options = old.get("options")
        if options is not None:
            if not isinstance(options, list) or len(options) > 100 or any(not isinstance(option, dict) for option in options):
                raise ValueError("creation_options_unproven")
            count += bool(options)
    if not count:
        raise ValueError("creation_options_missing")
    return count


def _complete_patch(patch: dict | None, payload: dict, expected_count: int) -> bool:
    if not patch:
        return False
    try:
        incoming = _index(payload["items"], "id")
        products = _index(patch["products"], "order_item_id")
        raw = _index(patch["raw_by_source.salla_direct"]["items"], "id")
        targets = {identity: row["options"] for identity, row in incoming.items() if row.get("options")}
        return len(targets) == expected_count and all(
            products[identity].get("options_raw") == options and raw[identity].get("options") == options
            for identity, options in targets.items()
        )
    except (KeyError, TypeError, ValueError):
        return False


async def enrich_stale_creation_options(
    db: Any, *, user_id: str, order_number: str, payload: dict,
) -> dict[str, Any]:
    """Called only for verified stale creations; never accepts their full state."""
    from operational_atomic import operational_owner

    async def enrich(scoped):
        current = await scoped.unified_orders.find_one({"user_id": user_id, "order_number": order_number})
        if not current:
            return _result("order_not_found")
        try:
            count = _prove(current, user_id=user_id, order_number=order_number, payload=payload)
        except (TypeError, ValueError):
            return _result("source_evidence_unproven")
        # Confirm the existing writer can express the complete proposed fill.
        patch = recovery.option_patch(current, payload["items"])
        if not _complete_patch(patch, payload, count):
            return _result("source_evidence_unproven")
        if not await recovery.apply_options(scoped, current, payload["items"]):
            return _result("source_evidence_unproven")
        return _result("customer_options_enriched", applied=True, line_count=count)

    try:
        return await operational_owner(db, user_id, enrich)
    except recovery.SourceChanged:
        return _result("source_changed")
    except Exception:
        # Never expose provider/customer data or exception messages in captures.
        return _result("customer_options_unavailable")
