"""Bounded inventory evidence minted only from a product-details response.

The light catalog writer intentionally does not mint this proof. Its later
revision or stock changes invalidate retained details evidence at consumption.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import re

INVENTORY_EVIDENCE = "inventory_evidence_v1"
MAX_INVENTORY_VARIANTS = 50
_KEYS = {"version", "source_revision", "stock_digest", "observed_at", "variant_count", "state"}


def _instant(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Mongo stores UTC datetimes and Motor commonly decodes them without tz.
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _quantity(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _stock_snapshot(product):
    count, variants = product.get("variants_count"), product.get("variants")
    options_count = product.get("options_count")
    if (type(count) is not int or not 0 <= count <= MAX_INVENTORY_VARIANTS
            or not isinstance(variants, list) or len(variants) != count
            or type(options_count) is not int or not 0 <= options_count <= 1000):
        raise ValueError("incomplete inventory")
    rows, identities = [], set()
    for variant in variants:
        if not isinstance(variant, dict):
            raise ValueError("invalid variant")
        identity = variant.get("id")
        if (not isinstance(identity, str) or not re.fullmatch(r"[1-9][0-9]{0,29}", identity)
                or identity in identities):
            raise ValueError("unproved variant identity")
        identities.add(identity)
        rows.append({key: variant.get(key) for key in ("id", "quantity", "unlimited_quantity")})
    for row in [product, *rows]:
        if type(row.get("unlimited_quantity")) is not bool or (row.get("quantity") is not None and not _quantity(row["quantity"])):
            raise ValueError("invalid stock")
    return {"quantity": product.get("quantity"), "unlimited_quantity": product.get("unlimited_quantity"),
            "variants_count": count, "options_count": options_count, "variants": rows}


def _digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _stock_state(snapshot):
    def available(row):
        return row["unlimited_quantity"] is True or _quantity(row["quantity"]) and row["quantity"] > 0
    def known(row):
        return row["unlimited_quantity"] is True or _quantity(row["quantity"])
    if not known(snapshot):
        return "unproved"
    if not available(snapshot):
        return "unavailable"
    if not snapshot["variants_count"]:
        return "unproved" if snapshot["options_count"] else "available"
    if not all(known(row) for row in snapshot["variants"]):
        return "unproved"
    return "available" if any(available(row) for row in snapshot["variants"]) else "unavailable"


def _raw_stock_valid(raw):
    if not isinstance(raw, dict):
        return False
    # Salla documents numeric stock strings. Accept their bounded decimal
    # grammar, never truthiness, whitespace, exponents or boolean quantities.
    quantities = [raw[key] for key in ("quantity", "stock_quantity") if raw.get(key) is not None]
    flags = [raw[key] for key in ("unlimited_quantity", "is_infinite") if key in raw and raw[key] is not None]
    valid = lambda value: (type(value) in {int, float, str}
                           and re.fullmatch(r"[0-9]{1,18}(?:\.[0-9]{1,6})?", str(value)) is not None)
    return (all(valid(value) for value in quantities)
            and len({Decimal(str(value)) for value in quantities}) <= 1
            and all(type(value) is bool for value in flags)
            and len(set(flags)) <= 1)


def _explicit_array(raw, keys):
    supplied = [raw[key] for key in keys if key in raw]
    if not supplied or any(not isinstance(rows, list) for rows in supplied) or any(rows != supplied[0] for rows in supplied[1:]):
        raise ValueError("missing or conflicting inventory arrays")
    return supplied[0]


def build_inventory_evidence(raw, product):
    """Invalid source facts remain explicitly unproved; details refresh still works."""
    proof = {"version": 1, "source_revision": product.get("source_revision"), "stock_digest": None,
             "observed_at": product.get("details_synced_at"), "variant_count": product.get("variants_count"), "state": "unproved"}
    try:
        snapshot = _stock_snapshot(product)
        proof["stock_digest"] = _digest(snapshot)
        if not _raw_stock_valid(raw):
            return proof
        variants = _explicit_array(raw, ("variants", "skus", "product_variants"))
        options = _explicit_array(raw, ("options", "product_options"))
        if (len(variants) != snapshot["variants_count"] or len(options) != snapshot["options_count"]
                or any(not isinstance(option, dict) for option in options)):
            return proof
        for raw_variant, canonical in zip(variants, snapshot["variants"]):
            if (not _raw_stock_valid(raw_variant) or type(raw_variant.get("id")) not in {str, int}
                    or not re.fullmatch(r"[1-9][0-9]{0,29}", str(raw_variant["id"]))
                    or str(raw_variant["id"]) != canonical["id"]):
                return proof
        proof["state"] = _stock_state(snapshot)
    except (TypeError, ValueError, OverflowError):
        pass
    return proof


def inventory_readiness(product, current, maximum_age):
    """Return state and immutable content binding, never a guessed availability."""
    proof = product.get(INVENTORY_EVIDENCE)
    if (not isinstance(proof, dict) or set(proof) != _KEYS or type(proof.get("version")) is not int or proof["version"] != 1
            or proof.get("state") not in {"available", "unavailable", "unproved"}
            or product.get("details_loaded") is not True
            or not isinstance(proof.get("source_revision"), str) or not re.fullmatch(r"[a-f0-9]{64}", proof["source_revision"])
            or proof["source_revision"] != product.get("source_revision")):
        return "unproved", None
    observed, details, clock = _instant(proof.get("observed_at")), _instant(product.get("details_synced_at")), _instant(current)
    if (observed is None or details != observed or clock is None or type(maximum_age) is not int
            or not 0 <= (clock - observed).total_seconds() < maximum_age):
        return "unproved", None
    try:
        snapshot = _stock_snapshot(product)
        digest = _digest(snapshot)
        if (type(proof.get("variant_count")) is not int or proof["variant_count"] != snapshot["variants_count"]
                or proof.get("stock_digest") != digest or proof["state"] == "unproved"
                or proof["state"] != _stock_state(snapshot)):
            return "unproved", None
        return proof["state"], digest
    except (TypeError, ValueError, OverflowError):
        return "unproved", None
