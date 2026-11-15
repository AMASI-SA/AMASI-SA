"""Read-only proof of option-price representation at approved native precision.

This authorizes operational source revalidation only. It does not recover an
exact historic SAR price or change provider, accounting, receipt or stock data.
"""
from copy import deepcopy
from decimal import Decimal, DecimalException, localcontext
import re

from fastapi import HTTPException

from component_catalog_label_recovery import _array, _object, _string
from order_currency import currency_code, money_decimal, salla_order_currency_fields
from order_review_business_snapshot import (
    KNOWN_NESTED, _business_tree, _hash, _tree, build_snapshot, verify_snapshot,
)


REASON = "approved_order_option_currency_representation"
PROOF_KIND = "native_minor_unit_equivalence"
_FILS = Decimal("0.001")
_HALALAH = Decimal("0.01")
_HALF_FILS = Decimal("0.0005")
_IDENTITY_FIELDS = ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")


def _number(node):
    if not (isinstance(node, list) and len(node) == 2 and node[0] == "number"
            and isinstance(node[1], str)):
        return None
    value = Decimal(node[1])
    return value if value.is_finite() else None


def _json_number(node):
    """Restore JSON numeric facts without Decimal becoming JSON strings."""
    value = _number(node)
    if value is None:
        return None
    restored = int(value) if value == value.to_integral_value() else float(value)
    return restored if _tree(restored) == node else None


def _native_equivalent(approved, current, rate):
    # Both amounts must already respect their declared currency precision. No
    # data is rounded away to manufacture equality. Strict bounds reject ties
    # without assuming Salla's policy for exactly half of one native minor unit.
    with localcontext() as context:
        context.prec = 64
        return bool(
            approved > 0 and current > 0
            and approved == approved.quantize(_FILS)
            and current == current.quantize(_HALALAH)
            and abs(current - approved * rate) < rate * _HALF_FILS
        )


def _source_unknown(snapshot):
    return {key: value for key, value in snapshot["unknown_field_fingerprints"].items()
            if key.startswith("/source/")}


def _restoration_evidence(raw, operation, *, order=None, previous=None):
    if not isinstance(raw, dict) or not isinstance(operation, dict):
        return None
    snapshot = operation.get("business_snapshot")
    if not verify_snapshot(snapshot):
        return None
    if any(not isinstance(key, str) for key in snapshot["unknown_field_fingerprints"]):
        return None
    identity = {key: operation.get(key) for key in _IDENTITY_FIELDS}
    if snapshot["facts"]["identity"] != _tree(identity):
        return None
    source = _object(snapshot["facts"]["source"])
    exchange = raw.get("exchange_rate")
    if not isinstance(exchange, dict):
        return None
    exchange_hash = _hash(_tree(exchange))
    # exchange_rate is an unknown source field in existing approval schemas.
    # Bind its complete envelope (including scalar types) to that frozen proof.
    if (source.get("exchange_rate") != ["unknown", exchange_hash]
            or snapshot["unknown_field_fingerprints"].get("/source/exchange_rate") != exchange_hash):
        return None
    fields = salla_order_currency_fields(raw)
    rate = money_decimal(fields.get("exchange_rate_to_sar"))
    amounts = raw.get("amounts") if isinstance(raw.get("amounts"), dict) else {}
    declared_currencies = [currency_code(value) for value in (raw.get("currency"), amounts.get("currency"))
                           if value is not None]
    if (fields.get("original_currency") != "KWD"
            or fields.get("currency_conversion_status") != "verified"
            or any(currency != "KWD" for currency in declared_currencies)
            or rate is None or rate <= 0):
        return None
    approved = _array(source.get("items"))
    current = raw.get("items")
    if not isinstance(current, list) or not current or len(approved) != len(current):
        return None
    restored = deepcopy(raw)
    changed_nonzero = False
    for item, prior_node in zip(restored["items"], approved):
        prior = _object(prior_node)
        if not isinstance(item, dict) or not item.get("id") or _tree(item["id"]) != prior.get("id"):
            return None
        options = item.get("options")
        old_options = _array(prior.get("options"))
        if options is None and not old_options:
            continue
        if not isinstance(options, list) or len(options) != len(old_options):
            return None
        for option, old_node in zip(options, old_options):
            if not isinstance(option, dict):
                return None
            if _business_tree(option, KNOWN_NESTED) == old_node:
                continue
            old = _object(old_node)
            value = option.get("value")
            price = value.get("price") if isinstance(value, dict) else None
            old_price = _object(_object(old.get("value")).get("price"))
            old_currency = _string(old_price.get("currency"))
            if (not option.get("id") or _tree(option["id"]) != old.get("id")
                    or not isinstance(price, dict)
                    or not isinstance(price.get("currency"), str)
                    or not re.fullmatch(r"[A-Z]{3}", price["currency"])
                    or not old_currency or not re.fullmatch(r"[A-Z]{3}", old_currency)):
                return None
            old_amount = _number(old_price.get("amount"))
            current_amount = _number(_tree(price.get("amount")))
            if old_amount is None or current_amount is None:
                return None
            if old_amount == 0 and current_amount == 0:
                # Preserve the existing zero proof: currency only, strict
                # numeric types, and all extra metadata still checked below.
                price["currency"] = old_currency
            else:
                if (old_currency != "KWD" or price["currency"] != "SAR"
                        or not _native_equivalent(old_amount, current_amount, rate)):
                    return None
                restored_amount = _json_number(old_price["amount"])
                if restored_amount is None:
                    return None
                price.update(amount=restored_amount, currency=old_currency)
                changed_nonzero = True
            if _business_tree(option, KNOWN_NESTED) != old_node:
                return None
    # The component fingerprint alone omits financial facts. First retain the
    # original exact-source proof. Detail hydration has a separate, bounded
    # contract requiring the actual canonical DTO and accepted lifecycle.
    candidate = build_snapshot(
        {"raw_by_source": {"salla_direct": restored}}, {}, {}, identity=identity,
        schema_version=snapshot["schema_version"],
        normalization_version=snapshot["normalization_version"],
    )
    detail_enrichment = None
    if (candidate["facts"]["source"] != snapshot["facts"]["source"]
            or _source_unknown(candidate) != _source_unknown(snapshot)):
        if order is None or not isinstance(previous, dict):
            return None
        from component_source_detail_recovery import restore_settled_detail_source
        detail = restore_settled_detail_source(restored, operation, order, previous)
        if detail is None:
            return None
        candidate = build_snapshot(
            {"raw_by_source": {"salla_direct": detail["raw"]}}, {}, {}, identity=identity,
            schema_version=snapshot["schema_version"],
            normalization_version=snapshot["normalization_version"],
        )
        if (candidate["facts"]["source"] != detail["approved_source"]
                or _source_unknown(candidate) != detail["approved_unknown"]):
            return None
        restored = detail["raw"]
        detail_enrichment = detail["audit"]
    return restored, format(rate, "f"), exchange_hash, changed_nonzero, detail_enrichment


def _safe_evidence(raw, operation, *, order=None, previous=None):
    try:
        return _restoration_evidence(raw, operation, order=order, previous=previous)
    except (HTTPException, DecimalException, TypeError, ValueError, OverflowError):
        return None


def restore_option_price_currencies(raw, operation):
    """Return an immutable-proof copy, or None for any unproven difference."""
    evidence = _safe_evidence(raw, operation)
    return evidence[0] if evidence else None


async def prove_option_price_currency_change(db, *, user_id, order_number, raw, order, previous):
    """Read proof only; the caller owns revision fencing and any revalidation."""
    from fulfillment_v2_routes import component_order_fingerprint
    from order_engine.mapper import map_salla_order
    from review_local_policy import load_local_review_workflows

    workflows = await load_local_review_workflows(db, user_id=user_id, order_numbers=[order_number])
    workflow = workflows.get(order_number) or {}
    operation_id = workflow.get("review_completion_operation_id")
    if not operation_id:
        return None
    operation = await db.order_review_completion_operations.find_one({
        "_id": operation_id, "user_id": user_id, "order_number": order_number,
        "state": "completed", "completion_mode": "mezan_local_v1",
    }) or {}
    if (operation.get("_id") != operation_id or operation.get("user_id") != user_id
            or operation.get("order_number") != order_number or operation.get("state") != "completed"
            or operation.get("completion_mode") != "mezan_local_v1" or operation.get("superseded_by")):
        return None
    evidence = _safe_evidence(raw, operation, order=order, previous=previous)
    if not evidence or not isinstance(previous, dict) or not previous.get("source_fingerprint"):
        return None
    restored, rate, exchange_hash, changed_nonzero, detail_enrichment = evidence
    restored_order = order.model_copy(update={"items": map_salla_order(restored).items})
    approved_fingerprint = component_order_fingerprint(restored_order)
    baseline = previous["source_fingerprint"]
    integrity = operation["business_snapshot"]["integrity_hash"]
    audit = None
    if "option_price_currency_revalidation" in previous:
        audit = previous["option_price_currency_revalidation"]
        if (not isinstance(audit, dict) or audit.get("reason") != REASON
                or audit.get("proof_kind") != PROOF_KIND
                or audit.get("exchange_rate_to_sar") != rate
                or audit.get("exchange_rate_fingerprint") != exchange_hash):
            return None
    elif "zero_price_currency_revalidation" in previous:
        audit = previous["zero_price_currency_revalidation"]
        if not isinstance(audit, dict) or audit.get("reason") != "zero_option_price_currency_only":
            return None
    if audit is not None:
        if (audit.get("operation_id") != operation_id or audit.get("approval_integrity_hash") != integrity
                or audit.get("current_fingerprint") != baseline):
            return None
        baseline = audit.get("approved_fingerprint")
    elif not changed_nonzero:
        return None
    if approved_fingerprint != baseline:
        return None
    return {
        "operation_id": operation_id, "approval_integrity_hash": integrity,
        "approved_fingerprint": approved_fingerprint, "previous_fingerprint": previous["source_fingerprint"],
        "reason": REASON, "proof_kind": PROOF_KIND, "exchange_rate_to_sar": rate,
        "exchange_rate_fingerprint": exchange_hash,
        **({"detail_enrichment": detail_enrichment} if detail_enrichment else {}),
    }
