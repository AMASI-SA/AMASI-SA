"""Raw native stock observations, not checkout, preparation or ad authority."""
from decimal import Decimal
import re

from .google_live_category_proof import canonical_id, digest

RELEVANT = ("id", "status", "is_available", "quantity", "stock_quantity", "unlimited_quantity", "is_infinite",
            "options", "product_options", "skus", "variants", "product_variants", "urls")


def _array(raw, keys):
    supplied = [raw[k] for k in keys if k in raw]
    if not supplied:
        return None
    if any(not isinstance(v, list) or v != supplied[0] for v in supplied):
        raise ValueError("ambiguous_source_array")
    return supplied[0]


def stock(raw):
    quantities = [raw[k] for k in ("quantity", "stock_quantity") if k in raw and raw[k] is not None]
    parsed = []
    for value in quantities:
        if type(value) not in (str, int, float) or not re.fullmatch(r"[0-9]{1,18}(?:\.[0-9]{1,6})?", str(value)):
            raise ValueError("stock_quantity_unproved")
        parsed.append(Decimal(str(value)))
    if len(set(parsed)) > 1:
        raise ValueError("stock_quantity_conflict")
    flags = [raw[k] for k in ("unlimited_quantity", "is_infinite") if k in raw and raw[k] is not None]
    if any(type(v) is not bool for v in flags) or len(set(flags)) > 1:
        raise ValueError("stock_flag_unproved")
    quantity, unlimited = (parsed[0] if parsed else None), (flags[0] if flags else None)
    state = "positive" if unlimited is True or quantity is not None and quantity > 0 else "zero_unlimited_unknown" if quantity == 0 and unlimited is None else "zero" if quantity == 0 and unlimited is False else "unknown"
    return {"quantity": str(quantity) if quantity is not None else None, "unlimited": unlimited, "state": state}


def product_observations(before, after, variants, traversal_complete):
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("product_source_unproved")
    identity = canonical_id(before.get("id"))
    relevant = lambda r: {k: r[k] for k in RELEVANT if k in r}
    if digest(relevant(before)) != digest(relevant(after)):
        raise ValueError("product_changed")
    if not isinstance(variants, list) or len(variants) > 120 or type(traversal_complete) is not bool:
        raise ValueError("variant_bound_unproved")
    options = _array(before, ("options", "product_options"))
    embedded = _array(before, ("skus", "variants", "product_variants"))
    labels = {}
    if options is not None:
        if len(options) > 50:
            raise ValueError("option_bound")
        option_ids = set()
        for option in options:
            if not isinstance(option, dict):
                raise ValueError("option_source_unproved")
            option_id = canonical_id(option.get("id"))
            if option_id in option_ids:
                raise ValueError("duplicate_option")
            option_ids.add(option_id)
            values = option.get("values")
            if not isinstance(values, list) or len(values) > 100:
                continue
            for value in values:
                if not isinstance(value, dict):
                    raise ValueError("option_value_source_unproved")
                value_id = canonical_id(value.get("id"))
                if value_id in labels or canonical_id(value.get("option_id")) != option_id:
                    raise ValueError("option_value_identity_unproved")
                labels[value_id] = {"option_id": option_id, "value_id": value_id,
                    "option_label": option.get("name") if isinstance(option.get("name"), str) else None,
                    "value_label": value.get("name") if isinstance(value.get("name"), str) else None}
    observed, ids = [], set()
    for row in variants:
        if not isinstance(row, dict):
            raise ValueError("variant_source_unproved")
        variant_id = canonical_id(row.get("id"))
        if variant_id in ids:
            raise ValueError("duplicate_variant")
        ids.add(variant_id)
        related = row.get("related_option_values")
        matched = None
        if isinstance(related, list):
            related_ids = [canonical_id(v) for v in related]
            if len(set(related_ids)) != len(related_ids):
                raise ValueError("duplicate_related_value")
            if all(v in labels for v in related_ids):
                matched = [labels[v] for v in related_ids]
        observed.append({"variant_id": variant_id, "stock": stock(row), "source_option_labels": matched,
                         "size_coverage_verified": False, "purchasability_verified": False})
    status = before.get("status") if before.get("status") in {"sale", "out", "hidden"} else "unknown"
    availability = before.get("is_available") if type(before.get("is_available")) is bool else None
    blockers = ["global_stock_unproved", "preparation_time_unproved", "size_coverage_unproved", "paid_authority_absent"]
    if status != "sale" or availability is not True:
        blockers.append("native_visibility_or_availability_blocked_or_unknown")
    return {"salla_product_id": identity, "status": status, "is_available": availability,
        "parent_stock": stock(before), "variants": observed, "variant_traversal_complete": traversal_complete,
        "simple_product_observed": options == [] and embedded == [] and traversal_complete and variants == [],
        "source_digest": digest(relevant(before)), "globally_ready": False, "size_coverage_verified": False,
        "preparation_time": None, "merchant_mapping_verified": False, "eligible_for_ad_selection": False,
        "execution_authorized": False, "blocking_reasons": blockers}
