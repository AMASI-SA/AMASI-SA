"""Pure, versioned approval evidence. No I/O and no recovery authorization.

Unknown paths are retained and guarded, not silently treated as transport data.
Hashes are integrity checks, not authentication or substitutes for source evidence.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from fastapi import HTTPException

SCHEMA_VERSION = 1
NORMALIZATION_VERSION = 2
REVIEW_SCOPE_VERSION = 2
# Only named delivery-operation paths are outside approval. Financial fields,
# shipping method/fulfillment inputs and unfamiliar siblings remain guarded.
DELIVERY_FIELDS = frozenset({
    "address", "company", "company_name", "company_code", "shipment_id",
    "tracking_number", "tracking_url", "tracking_link", "label_url",
    "shipped_at", "delivered_at",
})


def review_approval_projection(value):
    projected = deepcopy(value)
    projected.pop("shipping_address", None)
    shipping = projected.get("shipping")
    if isinstance(shipping, dict):
        for key in DELIVERY_FIELDS:
            shipping.pop(key, None)
        if not shipping:
            projected.pop("shipping")
    customer = projected.get("customer")
    if isinstance(customer, dict):
        customer.pop("shipping_address", None)
        customer.pop("address", None)
    return projected
# Status/cancellation have their own live guards; provider status changes during
# completion. updated_at is an ingestion clock, not the order creation instant.
TRANSPORT = {"status", "updated_at", "fetched_at", "received_at", "event_type", "event"}
ROOT = set("id reference_id items amounts payment_method payment_status customer notes options customer_notes staff_notes is_gift shipping shipping_address paid_amount remaining_amount has_remaining_amount payment_collection_status created_at date currency".split())
ORDER = set("order_id order_number created_at items payment totals shipping customer customer_notes staff_notes is_gift".split())
# mapper.py derives these from provider status or ingestion timestamps, and
# Cancellation remains a live guard.
DTO_PROJECTION = {"status", "status_native", "updated_at", "engine_updated_at", "is_new", "timeline", "completed_at"}
# OrderSourceDTO declares these as traceability/marketing attribution, not review
# approval. Keep source identity and any *new* source fields guarded.
DTO_SOURCE_METADATA = set("source_event fetched_at received_at source channel platform source_native utm_source utm_medium utm_campaign utm_content utm_term utm_raw utm_normalized click_ids campaign_id campaign_name ad_squad_id ad_squad_name ad_id ad_name match_status match_method match_confidence unmatched_reason attribution_window order_created_at_riyadh order_created_at_account account_timezone entity_url device".split())
ORDER |= {"schema_version", "source", "total_weight", "total_weight_unit", "tags", "cancelled_at", "refunded_at"}
# Classification only: every other key remains present in the canonical tree.
# Unknown nested fields receive independent fingerprints and fail closed.
KNOWN_NESTED = set("id product_id source_item_id order_item_id product variant variant_id sku quantity name full_name mobile phone email options customer_options customer_selections selections selected value values label type price amount total subtotal tax discount discounts currency weight unit url title description company company_name method shipping_method address recipient city country country_code street district postal_code zip latitude longitude building_number additional_number national_address number code payment_method payment_status paid_amount remaining_amount has_remaining_amount payment_collection_status status sale_price regular_price unit_price tax_amount discount_amount total_discount total_tax first_name last_name metadata".split())
# Explicit current OrderDTO business field inventory (models.py). This changes
# classification only; all fields, including unfamiliar ones, remain guarded.
KNOWN_NESTED |= set("provider source_order_id source_reference customer_id avatar_url gender is_guest shipping_address billing_address short_address formatted method_native collection_status checkout_url receiving_bank_code receiving_bank_name receipt_url transaction_reference paid_at card_brand card_last_four company_code shipment_id tracking_number tracking_url label_url shipped_at delivered_at accounting_currency total_sar exchange_rate_to_sar conversion_status conversion_source shipping cod_fee cod_fee_total cod_fee_tax cod_fee_source tax_percent tax_reported_by_source discounted_shipping parent_product_id barcode image_url image_urls product_url weight_unit options_raw options_normalized color size material custom_fields preparation_status availability_status fulfillment_source".split())
KNOWN_NESTED.discard("metadata")


def _reject(path="/source/invalid_representation"):
    raise HTTPException(409, detail={"code": "review_completion_source_changed",
                                    "differing_fields": [path]})


def _tree(value):
    """Tagged JSON tree: exact Decimal facts, no bool/number/string collisions."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            _reject()
        # normalize() is context-dependent and can round long Decimals.
        spelling = format(number, "f")
        if "." in spelling:
            spelling = spelling.rstrip("0").rstrip(".")
        return ["number", "0" if number == 0 else spelling]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, datetime):
        return _tree(value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat())
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            _reject()
        return ["object", {key: _tree(item) for key, item in sorted(value.items())}]
    if isinstance(value, (list, tuple)):
        return ["array", [_tree(item) for item in value]]
    _reject()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def raw_source_hash(source):
    """Same diagnostic hash contract for valid and conflicting-alias sources."""
    try:
        return _hash(_tree((source.get("raw_by_source") or {}).get("salla_direct")))
    except (HTTPException, ValueError, TypeError):
        return None  # Malformed values do not acquire a made-up hash contract.


def _normalize(raw):
    # Only salla_refresh.py's two proven enrichment writes are normalized.
    # Do not infer aliases from spelling or erase empty/missing distinctions.
    raw = deepcopy(raw)
    # component_provider_version consumes this precise nested provider clock.
    # Never remove creation facts, malformed clock objects or unknown metadata.
    if isinstance(raw.get("date"), dict) and isinstance(raw["date"].get("updated"), str):
        try:
            datetime.fromisoformat(raw["date"]["updated"].replace("Z", "+00:00"))
        except ValueError:
            pass  # Malformed/unknown clock spellings remain guarded.
        else:
            raw["date"].pop("updated")
    shipping = raw.get("shipping")
    if isinstance(shipping, dict) and "company_name" in shipping:
        company = shipping.get("company")
        name = company.get("name") if isinstance(company, dict) else company
        alias = shipping["company_name"]
        if "company" not in shipping and isinstance(alias, str) and alias:
            # salla_shipping._carrier reads either scalar spelling.
            shipping["company"] = shipping.pop("company_name")
        elif isinstance(name, str) and name and isinstance(alias, str) and alias:
            if name != alias:
                _reject("/source/shipping/company")
            shipping.pop("company_name")
        elif "company" in shipping:
            # Empty aliases have no proven equivalence contract.
            _reject("/source/shipping/company")
    if "shipping_address" in raw:
        address = raw["shipping_address"]
        if isinstance(address, dict) and address:
            if "shipping" not in raw:
                raw["shipping"] = {}
            shipping = raw["shipping"]
            if not isinstance(shipping, dict):
                _reject("/source/shipping/address")
            if "address" in shipping and _tree(shipping["address"]) != _tree(address):
                _reject("/source/shipping/address")
            shipping["address"] = raw.pop("shipping_address")
    # Arrays, customer/options, payment, product IDs and every unknown field
    # retain their original shape and order. Actual DTO is checked separately.
    return raw


def _unknown(value, path, allowed, result):
    if isinstance(value, dict):
        for key, item in value.items():
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in allowed:
                result[child] = _hash(_tree(item))
            else:
                _unknown(item, child, KNOWN_NESTED, result)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _unknown(item, path + "/" + str(index), KNOWN_NESTED, result)


def _business_tree(value, allowed):
    # Retain unknown changes without retaining unknown values in approval data.
    # Access to this internal snapshot must remain as restricted as order data.
    if isinstance(value, dict):
        return ["object", {key: (_business_tree(item, KNOWN_NESTED) if key in allowed
                                else ["unknown", _hash(_tree(item))])
                           for key, item in sorted(value.items())}]
    if isinstance(value, list):
        return ["array", [_business_tree(item, KNOWN_NESTED) for item in value]]
    return _tree(value)


def build_snapshot(source, order, acceptance, *, identity, schema_version=SCHEMA_VERSION):
    """Freeze supplied approval inputs; callers persist once before provider I/O."""
    raw = (source.get("raw_by_source") or {}).get("salla_direct")
    if not isinstance(raw, dict) or not isinstance(acceptance, dict) or not isinstance(identity, dict):
        _reject()
    dto = order.model_dump(mode="json") if hasattr(order, "model_dump") else deepcopy(order)
    if not isinstance(dto, dict):
        _reject()
    if schema_version not in (SCHEMA_VERSION, REVIEW_SCOPE_VERSION):
        _reject("/snapshot/schema_version")
    source_hash = raw_source_hash(source)
    if schema_version == REVIEW_SCOPE_VERSION:
        raw = review_approval_projection(raw)
        dto = review_approval_projection(dto)
    created = getattr(order, "created_at", None)
    if isinstance(created, datetime) and created.tzinfo:
        dto["created_at"] = created.astimezone(timezone.utc).isoformat()
    normalized = _normalize({key: value for key, value in raw.items() if key not in TRANSPORT})
    # DTO non-approval fields are independently guarded by existing completion
    # guards. Unexpected DTO fields are included as unknown rather than dropped.
    dto = {key: value for key, value in dto.items() if key not in DTO_PROJECTION}
    if isinstance(dto.get("source"), dict):
        dto["source"] = {key: value for key, value in dto["source"].items() if key not in DTO_SOURCE_METADATA}
    unknown = {}
    _unknown(normalized, "/source", ROOT, unknown)
    for key in TRANSPORT - {"status"}:
        if key in raw and raw[key] is not None and not isinstance(raw[key], (str, int, float, datetime)):
            unknown["/source/" + key] = _hash(_tree(raw[key]))
    # Only the documented provider status shape is independently guarded.
    # New status metadata must not disappear with the status transition.
    status = raw.get("status")
    if isinstance(status, dict):
        for key, value in status.items():
            if key == "customized" and isinstance(value, dict):
                for nested, item in value.items():
                    if nested not in {"id", "name", "slug"} or isinstance(item, (dict, list)):
                        unknown["/source/status/customized/" + nested] = _hash(_tree(item))
            elif key not in {"id", "name", "slug"} or isinstance(value, (dict, list)):
                unknown["/source/status/" + key] = _hash(_tree(value))
    elif status is not None and not isinstance(status, str):
        unknown["/source/status"] = _hash(_tree(status))
    _unknown(dto, "/order", ORDER, unknown)
    facts = {"source": _business_tree(normalized, ROOT), "order": _business_tree(dto, ORDER),
             "acceptance": _tree(acceptance), "identity": _tree(identity)}
    snapshot = {"schema_version": schema_version, "normalization_version": NORMALIZATION_VERSION,
                "facts": facts, "unknown_field_fingerprints": unknown,
                "source_hash": source_hash,
                "canonical_hash": _hash({"facts": facts, "unknown": unknown})}
    snapshot["integrity_hash"] = snapshot_hash(snapshot)
    return snapshot


def snapshot_hash(snapshot):
    return _hash({key: value for key, value in snapshot.items() if key != "integrity_hash"})


def verify_snapshot(snapshot):
    try:
        return (isinstance(snapshot, dict) and snapshot.get("schema_version") in (SCHEMA_VERSION, REVIEW_SCOPE_VERSION)
                and snapshot.get("normalization_version") == NORMALIZATION_VERSION
                and set(snapshot.get("facts", {})) == {"source", "order", "acceptance", "identity"}
                and isinstance(snapshot.get("unknown_field_fingerprints"), dict)
                and snapshot.get("canonical_hash") == _hash({"facts": snapshot["facts"],
                                                             "unknown": snapshot["unknown_field_fingerprints"]})
                and snapshot.get("integrity_hash") == snapshot_hash(snapshot))
    except (TypeError, ValueError):
        return False


def _diff(a, b, path=""):
    if a == b:
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for key in sorted(set(a) | set(b)):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            result.extend([child] if key not in a or key not in b else _diff(a[key], b[key], child))
        return result
    # Unwrap typed objects/arrays to report semantic paths, never values.
    if isinstance(a, list) and isinstance(b, list) and a and b and a[0] == b[0]:
        if a[0] == "object":
            return _diff(a[1], b[1], path)
        if a[0] == "array" and len(a[1]) == len(b[1]):
            return [p for i, (x, y) in enumerate(zip(a[1], b[1])) for p in _diff(x, y, path + "/" + str(i))]
    return [path]


def compare_snapshots(approved, current):
    if not verify_snapshot(approved) or not verify_snapshot(current):
        return {"equal": False, "code": "unknown_source_change", "classification": "unknown", "differing_fields": ["/snapshot_integrity"]}
    if approved["schema_version"] != current["schema_version"]:
        return {"equal": False, "code": "unknown_source_change", "classification": "unknown", "differing_fields": ["/snapshot/schema_version"]}
    fields = _diff(approved["facts"], current["facts"])
    before_unknown, after_unknown = approved["unknown_field_fingerprints"], current["unknown_field_fingerprints"]
    unknown = [key for key in sorted(set(before_unknown) | set(after_unknown)) if before_unknown.get(key) != after_unknown.get(key)]
    fields = sorted(set(fields + unknown))
    if approved["facts"]["acceptance"] != current["facts"]["acceptance"]:
        code, classification = "component_acceptance_changed", "business"
    elif unknown:
        code, classification = "unknown_source_change", "unknown"
    elif fields:
        code, classification = "review_completion_source_changed", "business"
    else:
        code, classification = None, "representation"
    return {"equal": code is None, "code": code, "classification": classification, "differing_fields": fields}


def diagnostic(approved, current):
    result = compare_snapshots(approved, current)
    unknown_paths = set(approved.get("unknown_field_fingerprints", {})) | set(current.get("unknown_field_fingerprints", {}))
    result["differing_fields"] = ["/unknown-path-sha256/" + _hash(path) if any(path == item or path.startswith(item + "/") for item in unknown_paths) else path[:512] for path in result["differing_fields"]]
    return {**result, "schema_version": SCHEMA_VERSION, "normalization_version": NORMALIZATION_VERSION,
            "approved_source_hash": approved.get("source_hash"), "current_source_hash": current.get("source_hash"),
            "approved_canonical_hash": approved.get("canonical_hash"), "current_canonical_hash": current.get("canonical_hash")}
