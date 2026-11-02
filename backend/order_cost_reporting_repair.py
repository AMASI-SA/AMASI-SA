"""Bounded, auditable corrections to historical *reporting* cost projections.

The default operation only plans. Historical amounts always come from frozen
order evidence. Current catalog prices are a separately labelled comparison,
never evidence for a repair. The only optional change is an explicitly selected
physical parent's missing base cost, recovered from that sale's frozen catalog.

Apply requires the exact plan manifest and an atomic Mongo transaction. Its
capability permits inserts into CORRECTIONS and one owner cost-revision bump;
orders, fulfillment plans/units, inventory and journals cannot be written here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from itertools import islice
import json
import re
from typing import Any, Iterable

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from order_cost_authority import (
    CORRECTIONS, LIFECYCLES, SNAPSHOTS, _sar, calculate_catalog_line_cost,
    decimal, economic_item, line_aliases, line_fingerprint, line_id,
    load_order_cost_authority, order_items, resolve_order_line_cost,
)
from order_option_cost_snapshot_routes import resolve_base_unit_cost
from product_cost_revision import PRODUCT_COST_REVISIONS, bump_product_cost_revision

REPAIR_VERSION = "order-cost-reporting-repair-v1"
MAX_ORDERS = 250
MAX_LINES = 5000
MAX_TOTAL_QUANTITY = 50000
BASES = ("before_catalog_report", "before_frozen_authority", "after_reporting_correction")


class ReportingRepairRefused(RuntimeError):
    """A safe code and bounded identifiers, never a connection/driver message."""

    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code, self.details = code, details


def _refuse(code: str, **details: Any) -> None:
    raise ReportingRepairRefused(code, **details)


def _hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def _amount(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def source_authority_evidence_hash(record: dict | None) -> str:
    """Compact source fence used by repair and report readers.

    Large frozen definitions are deliberately excluded. The frozen leaf values,
    original identity/quantity/options and resolved expected/actual cost form
    the reporting authority. The specific frozen base-price proof is hashed
    separately in a correction's evidence_hash. Never synthesize a fingerprint
    from the current order when constructing this source digest.
    """
    if not record:
        return _hash(None)
    snapshot = record.get("bundle_snapshot") or {}
    snapshot_keys = ("product_id", "revision", "schema_version", "estimated_cost", "known_cost",
                     "cost_complete", "selected_options", "sale_quantity", "line_fingerprint")
    leaf_keys = ("kind", "product_id", "salla_product_id", "resource_id", "binding_id", "variant_id",
                 "slot_id", "quantity", "unit_cost", "cost_source", "direct_unit_cost",
                 "current_product_reference", "leaf_id", "path", "paths", "required", "cost_only", "track_inventory")
    return _hash({
        "product_id": str(record.get("product_id") or record.get("salla_product_id") or ""),
        "quantity": str(decimal(record.get("quantity")).normalize()) if decimal(record.get("quantity")) is not None else None,
        "line_fingerprint": record.get("line_fingerprint"),
        "selected_options": record.get("selected_options"),
        "source_fingerprint": record.get("source_fingerprint"),
        "cost": record.get("cost"),
        "bundle_snapshot": {**{key: snapshot.get(key) for key in snapshot_keys},
                            **({"reporting_cost": snapshot["reporting_cost"]} if "reporting_cost" in snapshot else {}),
                            "leaves": [{key: leaf.get(key) for key in leaf_keys} for leaf in snapshot.get("leaves") or []]},
    })


class _RepairCollection:
    def __init__(self, collection: Any, name: str, owner: str, session: Any, writable: bool):
        self._collection, self._name = collection, name
        self._owner, self._session, self._writable = owner, session, writable

    def _options(self, options: dict) -> dict:
        if "session" in options:
            _refuse("repair_session_override_forbidden")
        return {**options, **({"session": self._session} if self._session is not None else {})}

    def find(self, query: dict, *args: Any, **kwargs: Any):
        if query.get("user_id") != self._owner:
            _refuse("repair_owner_scope_required")
        return self._collection.find(query, *args, **self._options(kwargs))

    async def find_one(self, query: dict, *args: Any, **kwargs: Any):
        if query.get("user_id") != self._owner:
            _refuse("repair_owner_scope_required")
        return await self._collection.find_one(query, *args, **self._options(kwargs))

    async def insert_one(self, document: dict, **kwargs: Any):
        if not self._writable or self._name != CORRECTIONS or document.get("user_id") != self._owner:
            _refuse("repair_write_forbidden")
        return await self._collection.insert_one(document, **self._options(kwargs))

    async def update_one(self, query: dict, update: dict, **kwargs: Any):
        if (not self._writable or self._name != PRODUCT_COST_REVISIONS
                or query != {"_id": "product-cost:" + self._owner}
                or update.get("$inc") != {"revision": 1}
                or (update.get("$setOnInsert") or {}).get("user_id") != self._owner):
            _refuse("repair_write_forbidden")
        return await self._collection.update_one(query, update, **self._options(kwargs))

    def __getattr__(self, name: str):
        _refuse("repair_operation_forbidden", operation=name)


class _RepairDB:
    def __init__(self, db: Any, owner: str, *, session: Any = None, writable: bool = False):
        self._db, self._owner, self._session, self._writable = db, owner, session, writable

    def __getitem__(self, name: str) -> _RepairCollection:
        return _RepairCollection(self._db[name], name, self._owner, self._session, self._writable)


def _identifiers(values: Iterable[Any], name: str, *, limit: int, required: bool = True) -> list[str]:
    if isinstance(values, (str, bytes)):
        _refuse("explicit_identifier_list_required", field=name)
    try:
        result = list(islice(iter(values), limit + 1))
    except TypeError:
        _refuse("explicit_identifier_list_required", field=name)
    if (required and not result) or len(result) > limit:
        _refuse("repair_scope_out_of_bounds", field=name, limit=limit)
    if any(not isinstance(value, str) or not value.strip() or len(value) > 256 or "\x00" in value for value in result):
        _refuse("invalid_identifier", field=name)
    result = [value.strip() for value in result]
    if len(set(result)) != len(result):
        _refuse("duplicate_identity", field=name)
    return sorted(result)


async def _read(db: Any, name: str, query: dict, projection: dict, limit: int) -> list[dict]:
    rows = await db[name].find(query, projection).to_list(length=limit + 1)
    if len(rows) > limit:
        _refuse("bounded_read_limit_exceeded", collection=name)
    return rows


async def _orders(db: Any, owner: str, numbers: list[str]) -> tuple[list[dict], dict]:
    from order_currency import SALLA_RAW_CURRENCY_PROJECTION
    fields = {key: 1 for key in (
        "user_id", "order_number", "products", "items", "order_status", "total_amount",
        "total", "currency", "original_currency", "original_total_amount", "amounts",
        "accounting_currency", "total_amount_sar", "exchange_rate_to_sar", "exchange_rate",
        "currency_conversion_status", "actual_refund_amount", "actual_partial_refund_amount",
    )}
    fields.update(SALLA_RAW_CURRENCY_PROJECTION)
    rows = await _read(db, "unified_orders", {"user_id": owner, "order_number": {"$in": numbers}}, fields, len(numbers) + 1)
    by_number = defaultdict(list)
    for row in rows:
        by_number[str(row.get("order_number"))].append(row)
    if any(len(values) != 1 for values in by_number.values()):
        _refuse("duplicate_identity", field="canonical_order")
    if set(by_number) != set(numbers):
        _refuse("order_not_found_for_owner", missing_count=len(set(numbers) - set(by_number)))
    orders, aliases, count, quantity = [], {}, 0, Decimal(0)
    for number in numbers:
        order = by_number[number][0]
        items = order_items(order)
        if not items:
            _refuse("order_has_no_canonical_lines", order_number=number)
        known = {}
        for item in items:
            key, q = line_id(item), decimal(item.get("quantity", 1))
            if not key or q is None or q <= 0 or isinstance(item.get("quantity"), bool):
                _refuse("invalid_canonical_line", order_number=number)
            for alias in line_aliases(item):
                if alias in known:
                    _refuse("duplicate_identity", field="canonical_order_line", order_number=number)
                known[alias] = key
            quantity += q
            count += 1
        aliases[number] = known
        orders.append(order)
    if count > MAX_LINES or quantity > MAX_TOTAL_QUANTITY:
        _refuse("repair_scope_out_of_bounds", field="canonical_lines_or_quantity")
    return orders, aliases


async def _validate_sources(db: Any, owner: str, numbers: list[str], aliases: dict, line_count: int) -> dict:
    """Do not let a last-row-wins dictionary hide duplicate source identities."""
    from stock_component_consumption_service import PLANS
    scope = {"user_id": owner, "order_number": {"$in": numbers}}
    stored = await _read(db, SNAPSHOTS, scope, {"_id": 0, "order_number": 1, "order_item_id": 1}, max(1, line_count * 4))
    lifecycles = await _read(db, LIFECYCLES, scope, {"_id": 0, "order_number": 1,
        "bundle_intake_snapshots": 1, "reporting_cost_snapshots": 1}, len(numbers) + 1)
    plans = await _read(db, PLANS, {"user_id": owner, "order_id": {"$in": numbers},
        "bundle_snapshots": {"$exists": True}}, {"_id": 0, "order_id": 1, "bundle_snapshots": 1}, len(numbers) + 1)
    corrections = await _read(db, CORRECTIONS, scope, {}, max(1, line_count * 4))
    for collection, rows, number_field, line_field in (
        (SNAPSHOTS, stored, "order_number", "order_item_id"),
        (CORRECTIONS, corrections, "order_number", "order_item_id"),
        (LIFECYCLES, lifecycles, "order_number", None),
        (PLANS, plans, "order_id", None),
    ):
        seen = set()
        for row in rows:
            number = str(row.get(number_field))
            key = aliases[number].get(str(row.get(line_field))) if line_field else "order"
            if line_field and key is None:
                continue
            identity = (number, key)
            if identity in seen:
                _refuse("duplicate_identity", collection=collection, order_number=number)
            seen.add(identity)
            for field in ("bundle_intake_snapshots", "reporting_cost_snapshots", "bundle_snapshots"):
                mapped = [aliases[number].get(str(key)) for key, value in (row.get(field) or {}).items() if value is not None]
                mapped = [key for key in mapped if key is not None]
                if len(set(mapped)) != len(mapped):
                    _refuse("duplicate_identity", collection=collection, order_number=number)
    return {(str(row["order_number"]), aliases[str(row["order_number"])][str(row["order_item_id"])]): row
            for row in corrections if str(row.get("order_item_id")) in aliases[str(row["order_number"])]}


async def _catalog_context(db: Any, owner: str, orders: list[dict]) -> dict:
    from product_catalog_cost_resolution import index_current_catalog_products
    from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
    from product_option_cost_routes import BINDINGS, RESOURCES
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS
    items = [item for order in orders for item in order_items(order)]
    ids = sorted({str(item.get(field)) for item in items for field in
                  ("parent_product_id", "product_id", "salla_product_id") if item.get(field) not in (None, "")})
    variants = sorted({str(item["variant_id"]) for item in items if item.get("variant_id")})
    skus = sorted({str(item["sku"]) for item in items if item.get("sku")})
    query = {"user_id": owner, "$or": [{key: {"$in": ids}} for key in ("id", "mezan_product_id", "salla_product_id")]
             + [{"variants.id": {"$in": variants}}, {"sku": {"$in": skus}}, {"variants.sku": {"$in": skus}}]}
    products = await _read(db, PRODUCTS, query, {"_id": 0}, 10000)
    ids = sorted(set(ids) | {str(row["salla_product_id"]) for row in products if row.get("salla_product_id")})
    scope = {"user_id": owner, "salla_product_id": {"$in": ids}}
    profiles = await _read(db, COST_PROFILES, scope, {"_id": 0}, max(1, len(ids)))
    product_bindings = await _read(db, PRODUCT_RESOURCE_BINDINGS, scope, {"_id": 0}, 20000)
    option_bindings = await _read(db, BINDINGS, scope, {"_id": 0}, 20000)
    resource_ids = sorted({str(row["resource_id"]) for row in product_bindings + option_bindings if row.get("resource_id")})
    resources = await _read(db, RESOURCES, {"user_id": owner, "id": {"$in": resource_ids}}, {"_id": 0}, max(1, len(resource_ids)))
    pb, ob = defaultdict(list), defaultdict(list)
    for row in product_bindings:
        pb[str(row.get("salla_product_id"))].append(row)
    for row in option_bindings:
        ob[str(row.get("salla_product_id"))].append(row)
    return {"indexes": index_current_catalog_products(products), "profiles": {str(row.get("salla_product_id")): row for row in profiles},
            "product_bindings": pb, "option_bindings": ob, "resources": {str(row["id"]): row for row in resources}}


def _catalog_cost(item: dict, context: dict) -> tuple[dict, str]:
    from product_catalog_cost_resolution import resolve_current_catalog_line_product
    by_id, by_variant, by_sku = context["indexes"]
    product = resolve_current_catalog_line_product(item, products_by_id=by_id,
        products_by_variant=by_variant, products_by_sku=by_sku)
    identity = str((product or {}).get("salla_product_id") or item.get("parent_product_id") or item.get("product_id") or "")
    return calculate_catalog_line_cost(item, product=product, profile=context["profiles"].get(identity),
        product_bindings=context["product_bindings"][identity], option_bindings=context["option_bindings"][identity],
        resources=context["resources"]), identity


def _historical_identity_proven(item: dict, record: dict) -> bool:
    if record.get("line_fingerprint"):
        return record["line_fingerprint"] == line_fingerprint(item)
    snapshot = record.get("bundle_snapshot") or {}
    if decimal(snapshot.get("sale_quantity")) != decimal(item.get("quantity", 1)):
        return False
    if not isinstance(snapshot.get("selected_options"), list):
        return False
    from bundle_fulfillment_v2 import option_answers, selected_ids
    current = economic_item(item)
    # Old recipe snapshots did not freeze every root variant/custom field.
    # Matching dropdown IDs alone cannot prove a newly personalized/variant
    # line was the original sale. Require explicit legacy evidence or a new
    # fingerprint (handled above), rather than borrowing current catalog data.
    variant = str(item.get("variant_id") or "")
    if variant and str(snapshot.get("sale_variant_id") or "") != variant:
        return False
    if any(field.get("value", field.get("text", field.get("answer"))) not in (None, "", [], {})
           for field in current.get("custom_fields") or []):
        return False
    selected = lambda rows: {(str(row.get("option_id")), str(row.get("value_id"))) for row in rows}
    return (selected(snapshot["selected_options"]) == selected(selected_ids(current))
            and (snapshot.get("option_answers") or {}) == option_answers(current))


def _with_missing_base(item: dict, record: dict, cost: dict, owner: str) -> tuple[dict, dict | None, str]:
    snapshot = record.get("bundle_snapshot") or {}
    root = str(snapshot.get("product_id") or "")
    if not snapshot or cost.get("cost_authority") != "bundle_snapshot":
        _refuse("historical_bundle_evidence_required")
    if "reporting_cost" in snapshot:
        from bundle_reporting_cost import verified_reporting_cost
        report = verified_reporting_cost(snapshot)
        if report is None:
            _refuse("historical_bundle_cost_incomplete")
        if any(str(row.get("product_id")) == root for row in report["added_bases"]):
            return deepcopy(cost), None, "financial_base_already_included"
    leaves = snapshot.get("leaves")
    if not isinstance(leaves, list):
        _refuse("historical_bundle_leaves_missing")
    if any(str(leaf.get("product_id") or leaf.get("salla_product_id") or "") == root
           and (leaf.get("current_product_reference") or leaf.get("kind") in {"product", "current_product"}) for leaf in leaves):
        return deepcopy(cost), None, "physical_base_already_included"
    if cost.get("cost_stage") != "expected":
        # A receipt-derived actual cost cannot acquire an estimated parent cost
        # without separate historical receipt provenance for that missing unit.
        _refuse("actual_cost_missing_base_provenance_required")
    if snapshot.get("cost_complete") is False or snapshot.get("needs_selection") or snapshot.get("unresolved_components"):
        _refuse("historical_bundle_cost_incomplete")
    frozen_leaf_total = Decimal(0)
    for leaf in leaves:
        q, amount = decimal(leaf.get("quantity")), decimal(leaf.get("unit_cost"))
        if q is None or q <= 0 or amount is None:
            _refuse("historical_bundle_cost_incomplete")
        frozen_leaf_total += q * amount
    estimate = decimal(snapshot.get("estimated_cost"))
    if estimate is None or _amount(frozen_leaf_total) != _amount(estimate):
        _refuse("historical_bundle_total_mismatch")
    definition = (snapshot.get("frozen_definitions") or {}).get(root)
    if not isinstance(definition, dict) or not isinstance(definition.get("catalog_metadata"), dict):
        _refuse("frozen_base_metadata_missing")
    metadata = definition["catalog_metadata"]
    product = definition.get("catalog_product")
    if "profile" not in metadata or not isinstance(product, dict) or not product:
        _refuse("frozen_base_metadata_missing")
    profile = metadata.get("profile")
    if profile is not None and not isinstance(profile, dict):
        _refuse("frozen_base_metadata_invalid")
    for row in (definition, product, profile or {}):
        if row.get("user_id") is not None and str(row["user_id"]) != owner:
            _refuse("frozen_base_foreign_owner")
    product_ids = {str(product[field]) for field in ("salla_product_id", "mezan_product_id", "id") if product.get(field) is not None}
    if str(definition.get("product_id")) != root or root not in product_ids:
        _refuse("frozen_base_identity_mismatch")
    unit, source = resolve_base_unit_cost(item, profile, product)
    evidence = profile if source.startswith("mezan_") else product
    unit_sar = _sar(unit, evidence)
    if unit_sar is None:
        _refuse("frozen_base_cost_or_fx_missing")
    quantity = decimal(item.get("quantity", 1))
    addition = unit_sar * quantity
    repaired = deepcopy(cost)
    repaired["base_total"] = _amount(Decimal(str(cost.get("base_total", 0))) + addition)
    repaired["line_total"] = _amount(Decimal(str(cost["line_total"])) + addition)
    repaired["uses_salla_fallback"] = bool(cost.get("uses_salla_fallback") or source.startswith("salla_"))
    repaired["mezan_cost_complete"] = bool(cost.get("mezan_cost_complete") and not source.startswith("salla_"))
    repaired["mezan_cost_missing"] = not repaired["mezan_cost_complete"]
    repaired["repair_version"] = REPAIR_VERSION
    proof = {"product_id": root, "variant_id": str(item.get("variant_id") or ""),
             "source": source, "unit_cost_sar": str(unit_sar), "quantity": str(quantity.normalize()),
             "frozen_base_metadata_hash": _hash({"profile": profile, "catalog_product": product}),
             "added_line_cost_sar": _amount(addition)}
    return repaired, proof, "include_missing_physical_base"


def _immutable_correction(record: dict) -> dict:
    return {key: record.get(key) for key in (
        "_id", "user_id", "order_number", "order_item_id", "quantity", "product_id",
        "line_fingerprint", "cost", "repair_version", "source_authority_evidence_hash",
        "source_cost_stage", "evidence_hash", "base_provenance",
    )}


def _correction_line_identity(item: dict) -> str:
    # The canonical wrapper may be introduced after an older raw Salla line.
    # Both representations must contend on the same unique first-write _id.
    raw = item.get("raw_item") if isinstance(item.get("raw_item"), dict) else {}
    identity = str(item.get("source_item_id") or raw.get("id") or line_id(item))
    if identity.startswith("salla:") and identity.count(":") == 2:
        identity = identity.split(":", 2)[2]
    return identity


def _known_total(rows: list[dict], field: str) -> float | None:
    if any(row.get(field) is None for row in rows):
        return None
    return _amount(sum((Decimal(str(row[field])) for row in rows), Decimal(0)))


def _difference(after: float | None, before: float | None) -> float | None:
    return _amount(Decimal(str(after)) - Decimal(str(before))) if after is not None and before is not None else None


async def build_reporting_repair_plan(db: Any, user_id: str, order_numbers: Iterable[str], *,
                                      include_base_product_ids: Iterable[str] = ()) -> dict:
    """Read-only, explicit bounded plan; any unresolved line prevents apply."""
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 256 or "\x00" in user_id:
        _refuse("owner_required")
    owner = user_id.strip()
    numbers = _identifiers(order_numbers, "order_numbers", limit=MAX_ORDERS)
    include_base = _identifiers(include_base_product_ids, "include_base_product_ids", limit=MAX_LINES, required=False)
    scoped = db if isinstance(db, _RepairDB) else _RepairDB(db, owner)
    orders, aliases = await _orders(scoped, owner, numbers)
    line_count = sum(len(order_items(order)) for order in orders)
    previous = await _validate_sources(scoped, owner, numbers, aliases, line_count)
    authority = await load_order_cost_authority(scoped, owner, orders,
        include_corrections=False, include_frozen_metadata=True)
    context = await _catalog_context(scoped, owner, orders)
    policy_rows = await _read(scoped, "order_status_policy", {"user_id": owner},
                              {"_id": 0, "status": 1, "category": 1}, 1000)
    policy = {str(row["status"]): row["category"] for row in policy_rows if row.get("status") and row.get("category") in {"confirmed", "pending", "cancelled", "refunded"}}
    rows, corrections, manifest_rows = [], [], []
    requested_found = set()
    for order in orders:
        number = str(order["order_number"])
        for item in order_items(order):
            key = line_id(item)
            catalog_cost, identity = _catalog_cost(item, context)
            record = authority["lines"].get((number, key))
            frozen = resolve_order_line_cost(item, order_number=number, catalog_cost=catalog_cost,
                                              authority=authority, product_id=identity)
            source_hash = source_authority_evidence_hash(record)
            row = {"order_number": number, "order_item_id": key, "product_id": identity,
                   "quantity": float(decimal(item.get("quantity", 1))),
                   "line_fingerprint": line_fingerprint(item), "currency": "SAR",
                   "before_catalog_report": catalog_cost["line_total"] if catalog_cost.get("calculation_cost_available") else None,
                   "before_frozen_authority": frozen["line_total"] if frozen.get("calculation_cost_available") and frozen.get("historical_cost_verified") else None,
                   "after_reporting_correction": None, "action": "unresolved", "reasons": []}
            frozen_product = str((record or {}).get("product_id") or identity)
            include = frozen_product in include_base
            if include:
                requested_found.add(frozen_product)
            repaired, proof = None, None
            try:
                if record is None or not frozen.get("historical_cost_verified") or not frozen.get("calculation_cost_available"):
                    _refuse("historical_cost_unresolved", reasons=frozen.get("missing_cost_reasons") or ["historical_snapshot_missing"])
                if not _historical_identity_proven(item, record):
                    _refuse("historical_line_identity_unproven")
                repaired, row["action"] = deepcopy(frozen), "use_frozen_authority"
                if include:
                    repaired, proof, row["action"] = _with_missing_base(item, record, frozen, owner)
                row["after_reporting_correction"] = repaired["line_total"]
            except ReportingRepairRefused as error:
                row["reasons"] = [error.code, *(error.details.get("reasons") or [])]
                row["action"] = "unresolved"
            row["delta_cost_from_catalog"] = _difference(row["after_reporting_correction"], row["before_catalog_report"])
            row["delta_cost_from_frozen"] = _difference(row["after_reporting_correction"], row["before_frozen_authority"])
            evidence_hash = _hash({"source_authority_evidence_hash": source_hash, "base_provenance": proof,
                                   "line_fingerprint": row["line_fingerprint"], "repair_version": REPAIR_VERSION})
            manifest_row = {key: row[key] for key in ("order_number", "order_item_id", "quantity", "line_fingerprint", "action", "reasons")}
            manifest_row.update(source_authority_evidence_hash=source_hash, evidence_hash=evidence_hash,
                                cost=repaired if row["action"] != "unresolved" else None)
            if proof is not None and row["action"] != "unresolved":
                correction = {"_id": "cost-correction:" + _hash([owner, number, _correction_line_identity(item)]), "user_id": owner,
                    "order_number": number, "order_item_id": key, "product_id": str(record.get("product_id") or identity),
                    "quantity": row["quantity"], "line_fingerprint": row["line_fingerprint"],
                    "cost": repaired, "repair_version": REPAIR_VERSION,
                    "source_authority_evidence_hash": source_hash, "source_cost_stage": frozen.get("cost_stage"),
                    "evidence_hash": evidence_hash, "base_provenance": proof,
                    "before_after": {key: row[key] for key in (*BASES, "delta_cost_from_catalog", "delta_cost_from_frozen")},
                    "state": "applied"}
                correction["correction_fingerprint"] = _hash(_immutable_correction(correction))
                old = previous.get((number, key))
                if old is not None:
                    if old.get("state") != "applied" or _immutable_correction(old) != _immutable_correction(correction):
                        row["action"], row["reasons"] = "unresolved", ["existing_correction_conflict"]
                        manifest_row.update(action="unresolved", reasons=row["reasons"])
                    else:
                        row["application_state"] = "already_applied"
                else:
                    row["application_state"] = "pending"
                corrections.append(correction)
            elif previous.get((number, key)) is not None:
                # A different previous correction cannot be ignored by changing
                # the caller's explicit base decision on a subsequent run.
                row["action"], row["reasons"] = "unresolved", ["existing_correction_conflict"]
                manifest_row.update(action="unresolved", reasons=row["reasons"])
            if row["action"] == "unresolved":
                row["after_reporting_correction"] = None
                row["delta_cost_from_catalog"] = None
                row["delta_cost_from_frozen"] = None
            rows.append(row)
            manifest_rows.append(manifest_row)
    unused = sorted(set(include_base) - requested_found)
    if unused:
        _refuse("requested_base_product_not_in_scope", count=len(unused))
    from order_status_policy import effective_product_cost
    report_totals = {key: Decimal(0) for key in BASES}
    incomplete = set()
    for order in orders:
        selected = [row for row in rows if row["order_number"] == str(order["order_number"])]
        for basis in BASES:
            total = _known_total(selected, basis)
            if total is None:
                incomplete.add(basis)
            else:
                report_totals[basis] += Decimal(str(effective_product_cost({**order, "total_product_cost": total}, policy)))
    report = {key: None if key in incomplete else _amount(value) for key, value in report_totals.items()}
    delta_catalog = _difference(report["after_reporting_correction"], report["before_catalog_report"])
    delta_frozen = _difference(report["after_reporting_correction"], report["before_frozen_authority"])
    manifest = {"repair_version": REPAIR_VERSION, "user_id": owner, "order_numbers": numbers,
                "include_base_product_ids": include_base, "lines": manifest_rows}
    ready = all(row["action"] != "unresolved" for row in rows)
    return {"mode": "dry_run", "repair_version": REPAIR_VERSION, "manifest_sha": _hash(manifest),
            "user_id": owner, "order_numbers": numbers, "include_base_product_ids": include_base,
            "ready": ready, "lines": rows, "corrections": corrections,
            "comparison_basis": {"before_catalog_report": "as_of_dry_run_catalog_estimate_not_historical_evidence",
                "before_frozen_authority": "original_frozen_authority_before_reporting_correction",
                "after_reporting_correction": "frozen_authority_plus_explicit_frozen_missing_base_only"},
            "totals": {"currency": "SAR", "gross_line_cost_sar": {basis: _known_total(rows, basis) for basis in BASES},
                "report_cost_sar": report, "return_policy": "existing_policy_once_per_order",
                "cost_change_from_catalog": delta_catalog, "cost_change_from_frozen": delta_frozen,
                "profit_change_from_catalog": -delta_catalog if delta_catalog is not None else None,
                "profit_change_from_frozen": -delta_frozen if delta_frozen is not None else None},
            "pending_corrections": sum(row.get("application_state") == "pending" for row in rows),
            "already_applied_corrections": sum(row.get("application_state") == "already_applied" for row in rows),
            "unresolved_lines": sum(row["action"] == "unresolved" for row in rows)}


async def apply_reporting_repair(db: Any, user_id: str, order_numbers: Iterable[str], *,
                                 include_base_product_ids: Iterable[str] = (), expected_manifest_sha: str) -> dict:
    """First-write-only, atomic correction batch, with a single cache revision."""
    if not isinstance(expected_manifest_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha):
        _refuse("expected_manifest_sha_required")
    # Preserve generators across transaction retries; the planner validates all
    # owner/identifier bounds again inside the transaction's snapshot.
    numbers = _identifiers(order_numbers, "order_numbers", limit=MAX_ORDERS)
    base_ids = _identifiers(include_base_product_ids, "include_base_product_ids", limit=MAX_LINES, required=False)
    for attempt in range(3):
        try:
            async with await db.client.start_session() as session:
                async def transaction(active_session):
                    scoped = _RepairDB(db, str(user_id).strip(), session=active_session, writable=True)
                    plan = await build_reporting_repair_plan(scoped, user_id, numbers, include_base_product_ids=base_ids)
                    if plan["manifest_sha"] != expected_manifest_sha:
                        _refuse("repair_manifest_changed")
                    if not plan["ready"]:
                        _refuse("repair_has_unresolved_lines", count=plan["unresolved_lines"])
                    pending = {(row["order_number"], row["order_item_id"]) for row in plan["lines"]
                               if row.get("application_state") == "pending"}
                    now = datetime.now(timezone.utc)
                    for record in plan["corrections"]:
                        if (record["order_number"], record["order_item_id"]) in pending:
                            await scoped[CORRECTIONS].insert_one({**deepcopy(record), "manifest_sha": expected_manifest_sha,
                                                                 "applied_at": now})
                    if pending:
                        await bump_product_cost_revision(scoped, str(user_id).strip())
                    return {**plan, "mode": "applied" if pending else "no_op", "applied_corrections": len(pending),
                            "product_cost_revision_bumped": bool(pending)}
                return await session.with_transaction(transaction, read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"), read_preference=ReadPreference.PRIMARY)
        except DuplicateKeyError:
            # Another identical repair may win the first write. A fresh snapshot
            # must prove an identical immutable record before treating it as no-op.
            if attempt == 2:
                _refuse("concurrent_correction_conflict")
    _refuse("concurrent_correction_conflict")
