"""Semantic approval inputs; never freeze live stock or provider status."""
import hashlib
import json

from fastapi import HTTPException


def canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def acceptance_snapshot(db, *, user_id, order):
    from fulfillment_v2_routes import _product_id, component_source_time
    from order_review_export_controls import ASSIGNMENT_DEFAULTS, preparation_assignment_product_key
    from stock_component_consumption_service import PRODUCTS, PRODUCT_BINDINGS, OPTION_BINDINGS, RESOURCES
    import product_fulfillment_rules as rules

    product_ids = sorted({_product_id(item) for item in order.items} - {""})
    query = {"user_id": user_id, "salla_product_id": {"$in": product_ids}}

    async def rows(collection, selector, fields):
        # Preserve explicit revision/version fields when supplied by a writer.
        # None is not a fabricated monotonic version: the digest remains required.
        names = set(fields.split()) | {"revision", "version"}
        docs = await db[collection].find(selector, {"_id": 0, **dict.fromkeys(names, 1)}).to_list(10001)
        if len(docs) > 10000:
            raise HTTPException(409, detail={"code": "review_acceptance_snapshot_too_large"})
        return sorted([canonical(row) for row in docs], key=lambda row: json.dumps(row, sort_keys=True))

    settings = await db.settings.find_one({"user_id": user_id}, {"g47_inventory": 1}) or {}
    config = settings.get("g47_inventory")
    if config is None:
        config = {}
    if isinstance(config, dict):
        config = {key: config[key] for key in ("component_lifecycle_starts_at", "revision", "version") if key in config}
        raw = config.get("component_lifecycle_starts_at")
        normalized = component_source_time(raw)
        if normalized:
            config["component_lifecycle_starts_at"] = normalized
    # Invalid configuration remains distinct and will fail authoritative guards.
    products = await rows(PRODUCTS, {"user_id": user_id, "$or": [
        {key: {"$in": product_ids}} for key in ("id", "salla_product_id", "mezan_product_id")
    ]}, "id salla_product_id mezan_product_id sku")
    # The component recipe resolves aliases to the product's canonical Salla
    # identity before loading bindings; freeze those same bindings/resources.
    resolved_ids = set(product_ids) | {
        str(row.get("salla_product_id") or row.get("mezan_product_id") or row.get("id"))
        for row in products
    }
    query = {"user_id": user_id, "salla_product_id": {"$in": sorted(resolved_ids)}}
    links = await rows(PRODUCT_BINDINGS, query, "id salla_product_id resource_id quantity")
    options = await rows(OPTION_BINDINGS, {**query, "mode": "resource"},
                         "id salla_product_id mode resource_id quantity option_id value_id option_name value_name")
    resource_ids = sorted({str(row["resource_id"]) for row in links + options if row.get("resource_id")})
    return canonical({
        "schema_version": 1, "g47_inventory": config,
        "product_rules": {key: getattr(rules, key) for key in (
            "PRODUCT_OPERATION_CHOICES_FROZEN", "FROZEN_FULFILLMENT_TYPE", "FROZEN_INVENTORY_POLICY")},
        "products": products,
        "profiles": await rows(rules.PRODUCT_OPERATION_PROFILES, query,
            "id salla_product_id fulfillment_type inventory_policy stockout_policy low_stock_threshold"),
        "product_bindings": links, "option_bindings": options,
        "resources": await rows(RESOURCES, {"user_id": user_id, "id": {"$in": resource_ids}},
                                "id kind track_inventory requires_preparation"),
        "assignment_defaults": await rows(ASSIGNMENT_DEFAULTS, {"user_id": user_id, "product_key": {
            "$in": sorted({preparation_assignment_product_key(item) for item in order.items})}},
            "product_key preparation_route assigned_employee_id"),
    })
