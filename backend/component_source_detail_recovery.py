"""Bounded proof projections for an already settled order's detail response.

This module never writes provider, approval, accounting or lifecycle data.
Catalog reference prices are not FX-equivalent order prices: their projection
requires independently unchanged, explicit contract amounts and canonical DTO
facts. The caller still owns source revision fencing and component lineage.

Two named operational projections do not reconstruct opaque historical values:
an inactive payment-link expiry after an already approved full settlement, and
a documented current clock equal to the previously accepted lifecycle clock.
Every other unknown path remains subject to exact immutable-source comparison.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, localcontext
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from component_catalog_label_recovery import _array, _object, _string
from order_review_business_snapshot import (
    KNOWN_NESTED, _business_tree, _hash, _tree, build_snapshot, verify_snapshot,
)
from salla_payment_collection import settled_payment_snapshot


DETAIL_PROJECTION_VERSION = "settled_order_detail_v1"
_IDENTITY_FIELDS = ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")
_PAYMENT_ALIASES = (
    "paid_amount", "remaining_amount", "has_remaining_amount",
    "payment_collection_status", "payment_checkout_url", "checkout_url",
)
_CATALOG_PRICES = ("price", "regular_price", "sale_price")
_EMPTY_ITEM_FIELDS = ("files", "codes", "reservations", "digital_edit_rules")
_DISABLED_ACTIONS = {"cancellable": False, "reorderable": False, "payable": False}
_DISABLED_BULLET = {
    "is_bullet_delivery": False, "bullet_countdown": 0, "on_demand_countdown": 0,
    "on_demand_delivery": False, "on_demand_timer": False,
}
_UNSETTLED_STATES = frozenset({
    "pending", "pending_payment", "awaiting_payment", "waiting_payment",
    "unpaid", "partial", "authorized", "authorised", "failed", "declined",
    "refunded", "restored", "reversed", "voided", "cancelled", "canceled",
    "بانتظار الدفع", "بإنتظار الدفع", "غير مدفوع", "مسترجع", "ملغي", "ملغى",
})
_SETTLED_PAYMENT_STATES = frozenset({"paid", "captured", "مدفوع", "تم الدفع"})


class _Unproven(ValueError):
    pass


def _require(condition):
    if not condition:
        raise _Unproven()


def _same(left, right):
    return _tree(left) == _tree(right)


def _number(value):
    # Native JSON numbers only. In particular, bool is not an amount.
    _require(type(value) in (int, float))
    result = Decimal(str(value))
    _require(result.is_finite() and result >= 0)
    return result


def _typed_number(node):
    _require(isinstance(node, list) and len(node) == 2 and node[0] == "number"
             and isinstance(node[1], str))
    result = Decimal(node[1])
    _require(result.is_finite() and result >= 0)
    return result


def _at_precision(amount, currency):
    _require(currency in {"KWD", "SAR"})
    quantum = Decimal(".001") if currency == "KWD" else Decimal(".01")
    with localcontext() as context:
        context.prec = 64
        _require(amount == amount.quantize(quantum))


def _money(envelope, currency="KWD"):
    _require(isinstance(envelope, dict) and envelope.get("currency") == currency)
    amount = _number(envelope.get("amount"))
    _at_precision(amount, currency)
    return amount


def _native_amount(value):
    amount = _money(value) if isinstance(value, dict) else _number(value)
    _at_precision(amount, "KWD")
    return amount


def _source_unknown(snapshot):
    return {key: value for key, value in snapshot["unknown_field_fingerprints"].items()
            if key.startswith("/source/")}


def _matches(value, approved_node, path, unknown):
    if isinstance(approved_node, list) and approved_node[:1] == ["unknown"]:
        digest = _hash(_tree(value))
        return approved_node == ["unknown", digest] and unknown.get(path) == digest
    return _business_tree(value, KNOWN_NESTED) == approved_node


def _opaque_field_bound(node, path, unknown):
    return bool(isinstance(node, list) and len(node) == 2 and node[0] == "unknown"
                and isinstance(node[1], str) and re.fullmatch(r"[0-9a-f]{64}", node[1])
                and unknown.get(path) == node[1])


def _financial_contract(raw, snapshot, order, identity):
    _require(order is not None)
    candidate = build_snapshot(
        {"raw_by_source": {"salla_direct": raw}}, order, {}, identity=identity,
        schema_version=2, normalization_version=3,
    )
    approved = _object(snapshot["facts"]["order"])
    current = _object(candidate["facts"]["order"])
    for field in ("order_id", "order_number", "created_at", "totals", "payment"):
        _require(field in approved and current.get(field) == approved[field])
    totals, payment = _object(current["totals"]), _object(current["payment"])
    _require(_string(totals.get("currency")) == "KWD")
    gross = _typed_number(totals.get("total"))
    _at_precision(gross, "KWD")
    _require(gross > 0 and _typed_number(payment.get("paid_amount")) == gross
             and _typed_number(payment.get("remaining_amount")) == 0
             and payment.get("has_remaining_amount") == ["bool", False]
             and payment.get("collection_status") == ["string", "paid"]
             and payment.get("checkout_url") in (["null"], ["string", ""]))
    old_items, new_items = _array(approved.get("items")), _array(current.get("items"))
    _require(old_items and len(old_items) == len(new_items))
    for old_node, new_node in zip(old_items, new_items):
        old, new = deepcopy(_object(old_node)), deepcopy(_object(new_node))
        _require(old and new)
        # Source option envelopes are proven separately by the caller; every
        # other canonical item fact, including images/custom fields, is exact.
        old.pop("options_raw", None)
        new.pop("options_raw", None)
        _require(new == old)
    return gross


def _settled_native_payment(raw, gross):
    amounts = raw.get("amounts")
    _require(isinstance(amounts, dict) and _money(amounts.get("total")) == gross)
    actions = raw.get("payment_actions")
    _require(isinstance(actions, dict))
    remaining, refund = actions.get("remaining_action"), actions.get("refund_action")
    payment = raw.get("payment", {})
    _require(isinstance(remaining, dict) and isinstance(refund, dict)
             and isinstance(payment, dict))
    _require(remaining.get("has_remaining_amount") is False
             and _native_amount(remaining.get("paid_amount")) == gross
             and _native_amount(remaining.get("remaining_amount")) == 0
             and remaining.get("checkout_url") == "")
    for container in (raw, amounts, payment, remaining, refund):
        if "currency" in container:
            _require(container["currency"] == "KWD")
    # A promoted root balance must not conceal conflicting native payment or
    # refund evidence, even if that conflict also existed in the old snapshot.
    for container in (raw, payment, remaining, refund):
        for field, expected in (("paid_amount", gross), ("remaining_amount", Decimal(0)),
                                ("refund_amount", Decimal(0)), ("refunded_amount", Decimal(0)),
                                ("pending_refund_amount", Decimal(0))):
            if container.get(field) is not None:
                _require(_native_amount(container[field]) == expected)
        for field in ("has_remaining_amount", "has_refund_amount"):
            if container.get(field) is not None:
                _require(container[field] is False)
        if "refunded_transactions" in container:
            _require(type(container["refunded_transactions"]) is list
                     and not container["refunded_transactions"])
    _require(raw.get("is_pending_payment") is None or raw["is_pending_payment"] is False)
    for value in (raw.get("payment_status"), raw.get("payment_collection_status"),
                  payment.get("status"), payment.get("collection_status")):
        if value is not None:
            _require(isinstance(value, str) and value.strip().casefold() in _SETTLED_PAYMENT_STATES)
    states = []
    status = raw.get("status")
    if isinstance(status, dict):
        states.extend((status.get("slug"), status.get("name")))
        custom = status.get("customized")
        if isinstance(custom, dict):
            states.extend((custom.get("slug"), custom.get("name")))
    elif status is not None:
        states.append(status)
    for value in states:
        if value is not None:
            _require(isinstance(value, str) and value.strip().casefold() not in _UNSETTLED_STATES)


def _explicit_line_contract(item, approved, index, unknown):
    _require(isinstance(item, dict) and item.get("id")
             and _tree(item["id"]) == approved.get("id"))
    quantity = _number(item.get("quantity"))
    _require(quantity > 0 and _tree(item["quantity"]) == approved.get("quantity"))
    amounts = item.get("amounts")
    _require(isinstance(amounts, dict) and _matches(
        amounts, approved.get("amounts"), f"/source/items/{index}/amounts", unknown))
    for field in ("price_without_tax", "total", "total_discount"):
        _money(amounts.get(field))
    tax = amounts.get("tax")
    _require(isinstance(tax, dict))
    _money(tax.get("amount"))
    if "original_price" in amounts:
        _money(amounts["original_price"])


def _restore_catalog(item, approved, categories):
    product, old_product = item.get("product"), _object(approved.get("product"))
    _require(isinstance(product, dict) and old_product)
    for field in _CATALOG_PRICES:
        if field not in product and field not in old_product:
            continue
        current, old_node = product.get(field), old_product.get(field)
        if _business_tree(current, KNOWN_NESTED) == old_node:
            continue
        old = _object(old_node)
        old_amount = _typed_number(old.get("amount"))
        _require(_string(old.get("currency")) == "KWD")
        _at_precision(old_amount, "KWD")
        current_amount = _money(current, "SAR")
        _require((old_amount == 0) == (current_amount == 0))
        # These reference prices can actually differ. This is not an FX proof;
        # explicit transaction amounts and the complete financial DTO are fixed.
        amount = int(old_amount) if old_amount == old_amount.to_integral_value() else float(old_amount)
        _require(_tree(amount) == old["amount"])
        current.update(amount=amount, currency="KWD")
        _require(_business_tree(current, KNOWN_NESTED) == old_node)
        categories.add("catalog_reference_prices")


def _restore_item_defaults(item, approved, index, unknown, categories):
    for field in _EMPTY_ITEM_FIELDS:
        if field in item and field not in approved:
            _require(type(item[field]) is list and not item[field])
            item.pop(field)
            categories.add("empty_item_delivery_metadata")
    if "is_digital_code_product" in item and "is_digital_code_product" not in approved:
        _require(item["is_digital_code_product"] is False)
        item.pop("is_digital_code_product")
        categories.add("empty_item_delivery_metadata")
    if "thumbnail" in item and "thumbnail" not in approved:
        image = item["thumbnail"]
        _require(isinstance(image, str) and image)
        old_product = _object(approved.get("product"))
        witnesses = []
        if "thumbnail" in old_product:
            witnesses.append(_matches(image, old_product["thumbnail"],
                                      f"/source/items/{index}/product/thumbnail", unknown))
        if "product_thumbnail" in approved:
            witnesses.append(_matches(image, approved["product_thumbnail"],
                                      f"/source/items/{index}/product_thumbnail", unknown))
        _require(witnesses and all(witnesses))
        item.pop("thumbnail")
        categories.add("approved_product_thumbnail")


def _restore_payment(raw, approved, unknown, categories):
    current = raw["payment_actions"]
    baseline = deepcopy(current)
    path = "/source/payment_actions"
    changed = False
    if not _matches(baseline, approved.get("payment_actions"), path, unknown):
        remaining = baseline["remaining_action"]
        _require(type(remaining.get("remaining_amount")) in (int, float)
                 and _number(remaining["remaining_amount"]) == 0)
        remaining["remaining_amount"] = None
        _require(_matches(baseline, approved.get("payment_actions"), path, unknown))
        changed = True
    derived = settled_payment_snapshot({"payment_actions": baseline})
    _require(_same(derived.get("payment_actions"), current))
    for field in _PAYMENT_ALIASES:
        _require(field in raw and field in derived and _same(raw[field], derived[field]))
        if field in approved:
            _require(_matches(raw[field], approved[field], "/source/" + field, unknown))
        else:
            raw.pop(field)
            changed = True
    raw["payment_actions"] = baseline
    if changed:
        categories.add("settled_payment_aliases")


def _restore_display(raw, approved, unknown, categories):
    if "features" in raw and not _matches(
            raw["features"], approved.get("features"), "/source/features", unknown):
        features = raw["features"]
        _require(isinstance(features, dict) and features.get("pay_as_company") is False
                 and _same(features.get("bullet_delivery"), _DISABLED_BULLET))
        features.pop("pay_as_company")
        features.pop("bullet_delivery")
        _require(_matches(features, approved.get("features"), "/source/features", unknown))
        categories.add("disabled_detail_features")
    if "actions" in raw and "actions" not in approved:
        _require(_same(raw["actions"], _DISABLED_ACTIONS))
        raw.pop("actions")
        categories.add("disabled_detail_actions")
    if "read" in raw and not _matches(raw["read"], approved.get("read"), "/source/read", unknown):
        _require(raw["read"] is True and _matches(False, approved.get("read"), "/source/read", unknown))
        raw["read"] = False
        categories.add("read_receipt_metadata")


def _project_inactive_expiry(raw, original, approved, unknown, categories):
    field, path = "pending_payment_ends_at", "/source/pending_payment_ends_at"
    if field not in raw or _matches(raw[field], approved.get(field), path, unknown):
        return
    _require(type(raw[field]) in (int, float) and _number(raw[field]) == 0
             and _opaque_field_bound(approved.get(field), path, unknown)
             and _same(original.get("actions"), _DISABLED_ACTIONS)
             and original.get("checkout_url") == "" and original.get("payment_checkout_url") == "")
    # The complete approved/current payment was already proved fully settled.
    # This removes an inactive expiry from comparison, not a recovered old value.
    raw.pop(field)
    approved.pop(field)
    unknown.pop(path)
    categories.add("inactive_payment_link_expiry")


def _project_accepted_clock(raw, approved_unknown, previous, categories):
    field, path = "updated_at", "/source/updated_at"
    value = raw.get(field)
    if path not in approved_unknown or approved_unknown[path] == _hash(_tree(value)):
        return
    _require(isinstance(value, dict) and set(value) == {"date", "timezone", "timezone_type"}
             and type(value["timezone_type"]) is int and value["timezone_type"] == 3
             and isinstance(value["timezone"], str) and value["timezone"]
             and isinstance(value["date"], str)
             and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{6})?", value["date"]))
    zone = ZoneInfo(value["timezone"])
    naive = datetime.fromisoformat(value["date"])
    first, second = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
    _require(first.utcoffset() == second.utcoffset())
    instant = first.astimezone(timezone.utc)
    _require(instant.astimezone(zone).replace(tzinfo=None) == naive)
    previous_clock = previous.get("source_updated_at")
    _require(isinstance(previous_clock, str))
    accepted = datetime.fromisoformat(previous_clock.replace("Z", "+00:00"))
    _require(accepted.tzinfo is not None and instant == accepted.astimezone(timezone.utc))
    # The opaque historical clock is not reconstructed. Authority is the exact
    # already accepted lifecycle instant, with a tightly validated current shape.
    # Only this proof copy omits it; the caller's original source stays intact.
    raw.pop(field)
    approved_unknown.pop(path)
    categories.add("previously_accepted_source_clock")


def _restore_settled_detail_source(restored, operation, order, previous):
    _require(isinstance(restored, dict) and isinstance(operation, dict)
             and isinstance(previous, dict) and previous.get("accepted") is True
             and not previous.get("retry_required") and not previous.get("cancelled")
             and previous.get("source_fingerprint"))
    snapshot = operation.get("business_snapshot")
    _require(verify_snapshot(snapshot) and snapshot["schema_version"] == 2
             and snapshot["normalization_version"] == 3
             and all(isinstance(key, str) for key in snapshot["unknown_field_fingerprints"]))
    identity = {key: operation.get(key) for key in _IDENTITY_FIELDS}
    _require(snapshot["facts"]["identity"] == _tree(identity))
    gross = _financial_contract(restored, snapshot, order, identity)
    _settled_native_payment(restored, gross)
    raw = deepcopy(restored)
    approved_source = deepcopy(snapshot["facts"]["source"])
    approved = _object(approved_source)
    unknown = deepcopy(_source_unknown(snapshot))
    old_items, items = _array(approved.get("items")), raw.get("items")
    _require(isinstance(items, list) and items and len(items) == len(old_items))
    categories = set()
    for index, (item, old_node) in enumerate(zip(items, old_items)):
        old = _object(old_node)
        _explicit_line_contract(item, old, index, unknown)
        _restore_catalog(item, old, categories)
        _restore_item_defaults(item, old, index, unknown, categories)
    _restore_payment(raw, approved, unknown, categories)
    _restore_display(raw, approved, unknown, categories)
    _project_inactive_expiry(raw, restored, approved, unknown, categories)
    _project_accepted_clock(raw, unknown, previous, categories)
    _require(categories)
    candidate = build_snapshot(
        {"raw_by_source": {"salla_direct": raw}}, {}, {}, identity=identity,
        schema_version=2, normalization_version=3,
    )
    _require(candidate["facts"]["source"] == approved_source
             and _source_unknown(candidate) == unknown)
    return {"raw": raw, "approved_source": approved_source, "approved_unknown": unknown,
            "audit": {"version": DETAIL_PROJECTION_VERSION, "categories": sorted(categories)}}


def restore_settled_detail_source(restored, operation, order, previous):
    """Return a bounded comparison projection, or None for unproven evidence."""
    try:
        return _restore_settled_detail_source(restored, operation, order, previous)
    except (HTTPException, DecimalException, TypeError, ValueError, KeyError,
            OverflowError, ZoneInfoNotFoundError):
        return None
