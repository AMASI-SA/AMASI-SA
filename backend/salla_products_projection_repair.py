"""Explicit repair of a stale Salla products projection, never an order resync.

Provider reads prove that the complete current item list matches the stored
source. Apply changes only ``products`` and its source label, under the existing
owner transaction. Source snapshots, fulfillment, costs and financial history
are never written. Plans contain identifiers, quantities and hashes, not PII.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from itertools import islice
import json
import re
from typing import Any, Iterable


REPAIR_VERSION = "salla-products-projection-repair-v1"
MAX_ORDERS = 25
MAX_LINES = 1000
MAX_BATCH_LINES = 5000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_CHOICES = (
    "options", "custom_fields", "customizations", "personalization", "fields",
    "questions", "attachments", "files", "choices", "attributes",
    "product_options", "selected_options", "customer_options",
)
_FENCE_FIELDS = (
    "order_id", "raw_by_source.salla_direct", "g47_salla_snapshot",
    "last_salla_direct_status_source_updated_at", "last_salla_direct_sync_at",
    "updated_at", "order_status", "order_status_slug", "last_make_update_at",
    "last_custom_app_update_at", "field_sources.products",
)
_PROJECTION = {"_id": 1, "user_id": 1, "order_number": 1, "products": 1, "field_sources": 1,
               **{field: 1 for field in _FENCE_FIELDS if field != "field_sources.products"}}


class ProductsProjectionRepairRefused(RuntimeError):
    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code, self.details = code, details


def _refuse(code: str, **details: Any) -> None:
    raise ProductsProjectionRepairRefused(code, **details)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False, default=str).encode()).hexdigest()


def _id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        _refuse("invalid_source_identity")
    value = str(value).strip()
    if not _IDENTIFIER.fullmatch(value):
        _refuse("invalid_source_identity")
    return value


def _scope(owner: str, numbers: Iterable[str]) -> tuple[str, list[str]]:
    if not isinstance(owner, str):
        _refuse("owner_required")
    owner = _id(owner)
    if isinstance(numbers, (str, bytes)):
        _refuse("explicit_order_list_required")
    try:
        values = list(islice(iter(numbers), MAX_ORDERS + 1))
    except TypeError:
        _refuse("explicit_order_list_required")
    if not values or len(values) > MAX_ORDERS:
        _refuse("repair_scope_out_of_bounds", limit=MAX_ORDERS)
    if any(not isinstance(value, str) for value in values):
        _refuse("explicit_order_list_required")
    values = [_id(value) for value in values]
    if len(set(values)) != len(values):
        _refuse("duplicate_order_identity")
    return owner, sorted(values)


def _quantity(value: Any) -> str:
    try:
        number = Decimal(str(value))
        if isinstance(value, bool) or not number.is_finite() or number <= 0:
            raise ValueError
        return str(number.normalize())
    except (InvalidOperation, ValueError, TypeError):
        _refuse("invalid_line_quantity")


def _path(row: dict, path: str) -> tuple[bool, Any]:
    current: Any = row
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _fence(row: dict) -> dict:
    return {path: {"present": present, "value": value}
            for path in _FENCE_FIELDS for present, value in [_path(row, path)]}


def _alias_id(values: list[Any], *, optional: bool = False) -> str:
    identities = {_id(value) for value in values if value not in (None, "")}
    if len(identities) > 1 or not identities and not optional:
        _refuse("conflicting_source_identity")
    return next(iter(identities), "")


def _item_evidence(rows: Any) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > MAX_LINES:
        _refuse("invalid_source_items")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            _refuse("invalid_source_items")
        identity = _id(row.get("id"))
        if identity in seen:
            _refuse("duplicate_source_item_identity")
        seen.add(identity)
        product = row.get("product") or {}
        if not isinstance(product, dict):
            _refuse("invalid_source_items")
        options = row.get("options")
        if options is not None and (not isinstance(options, list)
                                    or any(not isinstance(option, dict) for option in options)):
            _refuse("invalid_source_options")
        result.append({
            "id": identity, "quantity": _quantity(row.get("quantity")),
            "product_id": _alias_id([row.get("product_id"), product.get("id")]),
            "parent_product_id": _alias_id([row.get("parent_product_id"), product.get("parent_id")], optional=True),
            "variant_id": _alias_id([row.get("product_sku_id"), row.get("variant_id"), product.get("variant_id")], optional=True),
            "choices": {key: row[key] for key in _CHOICES if key in row},
            # Rebuilding a projection also maps line amounts. Never silently
            # use stale economics when the provider changed them in-place.
            "amounts": {key: row[key] for key in ("amounts", "price", "total", "discount", "tax") if key in row},
        })
    return sorted(result, key=lambda row: row["id"])


def _root_lines(rows: Any) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > MAX_LINES:
        _refuse("invalid_root_products")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            _refuse("invalid_root_products")
        identity = _id(row.get("order_item_id"))
        if identity in seen:
            _refuse("duplicate_root_item_identity")
        seen.add(identity)
        result.append({"order_item_id": identity, "product_id": _id(row.get("product_id")),
                       "quantity": _quantity(row.get("quantity"))})
    return result


def _version(payload: dict) -> str:
    from fulfillment_v2_routes import component_source_time
    date = payload.get("date") if isinstance(payload.get("date"), dict) else {}
    inputs = [value for value in (payload.get("updated_at"), date.get("updated")) if value not in (None, "")]
    versions = [component_source_time(value) for value in inputs]
    if not versions or any(not value for value in versions) or len(set(versions)) != 1:
        _refuse("source_version_missing_or_conflicting")
    return versions[0]


def _validate_source(row: dict, owner: str) -> tuple[str, str, dict, str]:
    from fulfillment_v2_routes import component_source_time
    if row.get("user_id") != owner:
        _refuse("foreign_order_owner")
    if "field_sources" in row and not isinstance(row["field_sources"], dict):
        _refuse("invalid_root_field_sources")
    number, internal = _id(row.get("order_number")), _id(row.get("order_id"))
    present, raw = _path(row, "raw_by_source.salla_direct")
    if not present or not isinstance(raw, dict):
        _refuse("stored_salla_source_required")
    if _id(raw.get("id")) != internal or _id(raw.get("reference_id")) != number:
        _refuse("stored_order_identity_mismatch")
    version = _version(raw)
    watermark = row.get("g47_salla_snapshot") or {}
    if not isinstance(watermark, dict) or watermark.get("requires_authoritative_refresh"):
        _refuse("source_requires_authoritative_refresh")
    revision = watermark.get("revision")
    if revision is not None and (type(revision) is not int or revision < 0):
        _refuse("invalid_source_revision")
    for value in (watermark.get("source_updated_at"), row.get("last_salla_direct_status_source_updated_at")):
        if value not in (None, ""):
            observed = component_source_time(value)
            if not observed or observed > version:
                _refuse("stored_source_is_stale")
    status = raw.get("status") or {}
    slug = status.get("slug") if isinstance(status, dict) else status
    cancelled = str(slug or "").strip().casefold() in {"cancel", "cancelled", "canceled", "refunded", "ملغي", "ملغى"}
    if watermark.get("cancelled") and not cancelled:
        _refuse("source_cancellation_conflict")
    return number, internal, raw, version


class _ProviderReadCollection:
    """Do not let GET authentication rotate tokens or write error metadata."""
    def __init__(self, collection: Any, owner: str):
        self.__collection, self.__owner = collection, owner

    async def find_one(self, query: dict, projection: dict | None = None, **kwargs):
        if query.get("user_id") != self.__owner or kwargs:
            _refuse("provider_read_scope_required")
        return await self.__collection.find_one(query, projection, max_time_ms=5000)

    def __getattr__(self, name: str):
        _refuse("provider_credentials_write_forbidden")


class _ProviderReadDB:
    def __init__(self, db: Any, owner: str):
        self.__db, self.__owner = db, owner

    def __getitem__(self, name: str):
        if name != "salla_integrations":
            _refuse("provider_read_scope_required")
        return _ProviderReadCollection(self.__db[name], self.__owner)

    def __getattr__(self, name: str):
        return self[name]


@dataclass(frozen=True)
class _ProviderProof:
    order_number: str
    order_id: str
    version: str
    items: Any


async def _provider_proof(db: Any, owner: str, row: dict, request: Any) -> _ProviderProof:
    from salla_integration.order_items_snapshot import fetch_complete_order_items
    if request is None:
        from salla_integration.service import call_salla
        request = call_salla
    number, internal, raw, version = _validate_source(row, owner)
    scoped = _ProviderReadDB(db, owner)

    async def read(db, user_id, method, path, **kwargs):
        if (db is not scoped or user_id != owner or method != "GET"
                or path not in {f"/orders/{internal}", "/orders/items"}
                or path == "/orders/items" and str((kwargs.get("params") or {}).get("order_id")) != internal):
            _refuse("provider_request_scope_required")
        return await request(db, user_id, method, path, **kwargs)

    async def details() -> dict:
        response = await read(scoped, owner, "GET", f"/orders/{internal}")
        data = response.get("data") if isinstance(response, dict) and response.get("success") is not False else None
        if (not isinstance(data, dict) or _id(data.get("id")) != internal
                or _id(data.get("reference_id")) != number):
            _refuse("provider_order_identity_mismatch")
        if _version(data) != version:
            _refuse("provider_source_version_changed")
        return data

    before = await details()
    if _hash(before.get("status")) != _hash(raw.get("status")):
        _refuse("provider_status_differs_from_stored_source")
    items = await fetch_complete_order_items(scoped, owner, internal, request=read)
    after = await details()
    if _hash(before.get("status")) != _hash(after.get("status")):
        _refuse("provider_source_changed_during_read")
    try:
        items.validate_for(internal, raw.get("items"))
    except (TypeError, ValueError):
        _refuse("provider_items_differ_from_stored_source")
    if _hash(_item_evidence(items)) != _hash(_item_evidence(raw.get("items"))):
        _refuse("provider_items_differ_from_stored_source")
    return _ProviderProof(number, internal, version, items)


async def _orders(db: Any, owner: str, numbers: list[str]) -> list[dict]:
    rows = await db.unified_orders.find({"user_id": owner, "order_number": {"$in": numbers}},
        _PROJECTION, max_time_ms=5000).limit(len(numbers) + 1).to_list(length=len(numbers) + 1)
    seen = {}
    for row in rows:
        number = str(row.get("order_number"))
        if number in seen:
            _refuse("duplicate_order_identity")
        seen[number] = row
    if set(seen) != set(numbers):
        _refuse("order_not_found_for_owner", missing_count=len(set(numbers) - set(seen)))
    return [seen[number] for number in numbers]


def _plan_row(row: dict, owner: str, proof: _ProviderProof) -> tuple[dict, list[dict]]:
    from orders_db import _current_salla_products
    number, internal, raw, version = _validate_source(row, owner)
    if (number, internal, version) != (proof.order_number, proof.order_id, proof.version):
        _refuse("repair_source_changed")
    try:
        proof.items.validate_for(internal, raw.get("items"))
    except (ValueError, TypeError):
        _refuse("repair_source_changed")
    if _hash(_item_evidence(raw.get("items"))) != _hash(_item_evidence(proof.items)):
        _refuse("repair_source_changed")
    before = _root_lines(row.get("products"))
    # Preserve the normal legacy projection and historical option fingerprints.
    # The full-read proof is also required to remove the last remaining line.
    products = _current_salla_products(deepcopy(raw), number, complete_items=proof.items)
    if products is None:
        _refuse("source_products_projection_unavailable")
    after = _root_lines(products)
    before_hash, after_hash = _hash(row["products"]), _hash(products)
    before_quantities = {line["order_item_id"]: line["quantity"] for line in before}
    summary = {"order_number": number, "order_id": internal, "source_version": version,
        "source_revision": (row.get("g47_salla_snapshot") or {}).get("revision"),
        "before": before, "after": after,
        "removed_item_ids": sorted({line["order_item_id"] for line in before} - {line["order_item_id"] for line in after}),
        "reduced_quantities": [{"order_item_id": line["order_item_id"],
            "before": before_quantities[line["order_item_id"]], "after": line["quantity"]}
            for line in after if line["order_item_id"] in before_quantities
            and Decimal(line["quantity"]) < Decimal(before_quantities[line["order_item_id"]])],
        "before_products_hash": before_hash, "after_products_hash": after_hash,
        "source_fence_hash": _hash(_fence(row)),
        "provider_items_hash": _hash(_item_evidence(proof.items)),
        "action": "replace_products" if before_hash != after_hash else "no_op", "reasons": []}
    return summary, products


def _public_plan(owner: str, numbers: list[str], rows: list[dict]) -> dict:
    manifest = {"repair_version": REPAIR_VERSION, "user_id": owner, "order_numbers": numbers, "orders": rows}
    return {**manifest, "mode": "dry_run", "ready": all(not row["reasons"] for row in rows),
        "manifest_sha": _hash(manifest),
        "pending_orders": sum(row.get("action") == "replace_products" for row in rows)}


async def _prepare(db: Any, owner: str, numbers: list[str], request: Any):
    source_rows = await _orders(db, owner, numbers)
    if sum(len(row.get("products") or []) for row in source_rows) > MAX_BATCH_LINES:
        _refuse("repair_scope_out_of_bounds", limit=MAX_BATCH_LINES)
    summaries, prepared = [], []
    for row in source_rows:
        try:
            _root_lines(row.get("products"))
            proof = await _provider_proof(db, owner, row, request)
            summary, products = _plan_row(row, owner, proof)
            prepared.append((row, proof, products))
        except ProductsProjectionRepairRefused as exc:
            summary = {"order_number": row["order_number"], "action": "refused", "reasons": [exc.code]}
        except Exception as exc:
            # Driver/provider messages can contain credentials or PII.
            summary = {"order_number": row["order_number"], "action": "refused",
                       "reasons": ["provider_or_mapping_failed"], "error_type": type(exc).__name__}
        summaries.append(summary)
    if sum(len(products) for _, _, products in prepared) > MAX_BATCH_LINES:
        _refuse("repair_scope_out_of_bounds", limit=MAX_BATCH_LINES)
    return _public_plan(owner, numbers, summaries), prepared


async def build_products_projection_repair_plan(db: Any, user_id: str,
        order_numbers: Iterable[str], *, request: Any = None) -> dict:
    """Read-only explicit plan, with fresh provider proof and no credential writes."""
    owner, numbers = _scope(user_id, order_numbers)
    plan, _ = await _prepare(db, owner, numbers, request)
    return plan


async def apply_products_projection_repair(db: Any, user_id: str,
        order_numbers: Iterable[str], *, expected_manifest_sha: str, request: Any = None) -> dict:
    """Revalidate a reviewed plan, then atomically replace only its products arrays."""
    from operational_atomic import operational_owner
    owner, numbers = _scope(user_id, order_numbers)
    if not isinstance(expected_manifest_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha):
        _refuse("reviewed_manifest_sha_required")
    plan, prepared = await _prepare(db, owner, numbers, request)
    if not plan["ready"]:
        _refuse("repair_has_unresolved_orders")
    if plan["manifest_sha"] != expected_manifest_sha:
        _refuse("repair_manifest_changed")
    if not plan["pending_orders"]:
        return {**plan, "mode": "no_op", "applied_orders": 0}

    async def apply(scoped):
        current_rows = await _orders(scoped, owner, numbers)
        changes, summaries = [], []
        for current, (_, proof, _) in zip(current_rows, prepared):
            summary, products = _plan_row(current, owner, proof)
            summaries.append(summary)
            changes.append((current, products, summary["action"]))
        current_plan = _public_plan(owner, numbers, summaries)
        if current_plan["manifest_sha"] != expected_manifest_sha:
            _refuse("repair_manifest_changed")
        for current, products, action in changes:
            if action == "no_op":
                continue
            selector = {"_id": current["_id"], "user_id": owner, "order_number": current["order_number"]}
            for field in ("products", *_FENCE_FIELDS):
                present, value = _path(current, field)
                selector[field] = {"$exists": True, "$eq": value} if present else {"$exists": False}
            # No generic upsert/resync/revalidation callback may run here.
            result = await scoped.unified_orders.update_one(selector, {"$set": {
                "products": deepcopy(products), "field_sources.products": "salla_direct"}})
            if getattr(result, "matched_count", 0) != 1:
                _refuse("repair_compare_and_set_failed")
        return {**current_plan, "mode": "applied", "applied_orders": current_plan["pending_orders"]}

    return await operational_owner(db, owner, apply)
