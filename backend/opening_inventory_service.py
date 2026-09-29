"""Explicit, one-time cutover inventory initialization from preserved evidence.

The already approved V2 opening is verified, never reposted. Quantity remains
warehouse occupancy; valuation authority is the existing G47 MWA cost state.
Import has no stock effect. Approval and all projections share atomic_owner.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from accounting_atomic import atomic_owner
from accounting_ledger_v2 import read_reporting_entries_v2, verify_active_opening_v2
from accounting_module_contract import require_accounting_permission, require_owner
from accounting_module_readiness import build_accounting_module_status
from accounting_periods import assert_open_journal_periods
from accounting_source_files import MAX_BYTES, preserve_original
from accounting_write_control import fresh_actor
from accounting_writer_transition import assert_writer_allowed
from component_edit_policy import component_cost_metadata
from component_status_policy import component_is_active
from product_cost_revision import bump_product_cost_revision
from product_inventory_rules import build_inventory_configuration_key, canonical_specifications
from purchase_receiving_service import COST_POLICY, COST_STATES, LOCATIONS, PRODUCTS, RECEIPTS, RESOURCES, approved_account_mappings, identity_key, number, resolve_line, same_identity, stable_id, stored_number

IMPORTS = "mz2_opening_inventory_imports"
INITIALIZATIONS = "mz2_opening_inventory_initializations"
SCHEMA = "g47-opening-inventory-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Allocation(StrictModel):
    location_id: str = Field(min_length=1, max_length=120)
    quantity: str
    scanned_location_barcode: str = Field(min_length=1, max_length=200)
    preparation_state: Literal["requires_preparation", "ready_complete"]
    specifications: dict = Field(default_factory=dict)


class OpeningRow(StrictModel):
    item_type: Literal["PRODUCT", "STOCK_COMPONENT"]
    product_id: str | None = None
    variant_id: str | None = None
    resource_id: str | None = None
    category_id: str | None = None
    inventory_account_id: str = Field(min_length=1, max_length=120)
    opening_quantity: str
    opening_unit_cost: str
    opening_total_cost: str
    allocations: list[Allocation] = Field(min_length=1, max_length=1000)


class OpeningDocument(StrictModel):
    schema_version: Literal["g47-opening-inventory-v1"]
    cost_policy_version: Literal["moving-weighted-average-v1"]
    cutover_at: str
    opening_txn_group_id: str = Field(min_length=1, max_length=200)
    evidence_ref: str = Field(min_length=1, max_length=500)
    rows: list[OpeningRow] = Field(min_length=1, max_length=1000)


def fail(code, status=409, **details):
    raise HTTPException(status, detail={"code": "opening_inventory_" + code, **details})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError):
        fail("cutover_invalid", 422)


def exact(value, *, money=False):
    result = number(value)
    precision = Decimal("0.01") if money else Decimal("0.000001")
    if result <= 0 or result != result.quantize(precision) or (not money and result > Decimal("1000000000")):
        fail("positive_explicit_quantity_and_cost_required", 422)
    stored_number(result)
    return result


def parse_document(content):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        if not content or len(content) > MAX_BYTES:
            raise ValueError("size")
        raw = json.loads(content.decode("utf-8-sig"), object_pairs_hook=unique_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
        return OpeningDocument.model_validate(raw)
    except (ValueError, UnicodeError, ValidationError):
        fail("evidence_document_invalid", 422)


async def owner_actor(db, user, expected_owner=None):
    actor = await fresh_actor(db, user)
    require_owner(actor)
    require_accounting_permission(actor, "accounting.opening_balances.approve")
    owner = actor["id"]
    if expected_owner is not None and owner != expected_owner:
        fail("actor_scope_changed", 403)
    return actor, owner


async def snapshot(db, owner):
    locations = await db[LOCATIONS].find({"user_id": owner}, {"_id": 0}).sort("id", 1).to_list(20001)
    if len(locations) > 20000 or len({row.get("id") for row in locations}) != len(locations):
        fail("location_scope_requires_reconciliation")
    return locations


async def opening_inventory_context(db, user):
    """Owner-scoped selectors; no indexes, initialization or financial mutation."""
    _, owner = await owner_actor(db, user)
    async def rows(collection, query, projection, maximum=10000):
        result = await db[collection].find({"user_id": owner, **query}, {"_id": 0, **projection}).to_list(maximum + 1)
        if len(result) > maximum:
            fail("catalog_requires_reconciliation")
        return result
    products = await rows(PRODUCTS, {"archived": {"$ne": True}}, {
        "mezan_product_id": 1, "name": 1, "sku": 1, "variants": 1, "variants_count": 1})
    product_choices = [{"id": p["mezan_product_id"], "name": p.get("name"), "sku": p.get("sku"),
        "variants_required": bool(p.get("variants") or p.get("variants_count")),
        "variants": [{"id": str(v["id"]), "name": v.get("name") or v.get("sku") or str(v["id"]), "sku": v.get("sku")}
                     for v in p.get("variants") or [] if isinstance(v, dict) and v.get("id")]}
        for p in products if p.get("mezan_product_id")]
    components = await rows(RESOURCES, {"track_inventory": True}, {
        "id": 1, "name": 1, "code": 1, "category_ids": 1, "unit": 1, "kind": 1, "status": 1, "is_active": 1, "archived": 1})
    components = [{"id": r["id"], "name": r.get("name"), "code": r.get("code"),
        "category_ids": r.get("category_ids") or [], "unit": r.get("unit")}
        for r in components if r.get("id") and r.get("kind") != "service" and component_is_active(r)]
    categories = await rows("mezan_component_categories_v2", {}, {"id": 1, "name": 1})
    cabinets = {r["id"]: r for r in await rows("warehouse_locations_cabinets", {}, {"id": 1, "purpose": 1})}
    locations = await rows(LOCATIONS, {"state": {"$ne": "disabled"}}, {
        "id": 1, "code": 1, "warehouse_id": 1, "cabinet_id": 1, "purpose": 1, "max_items": 1}, maximum=20000)
    locations = [{"id": r["id"], "code": r.get("code"), "warehouse_id": r["warehouse_id"], "max_items": r.get("max_items")}
        for r in locations if r.get("id") and r.get("warehouse_id")
        and (r.get("purpose") or cabinets.get(r.get("cabinet_id"), {}).get("purpose")) == "permanent_storage"]
    cutover, mappings = await approved_account_mappings(db, owner)
    section = (cutover.get("evidence_sections") or {}).get("inventory")
    evidence_ref = section if isinstance(section, str) else next((str((section or {}).get(k)) for k in ("ref", "evidence_ref", "source_file_id") if (section or {}).get(k)), "")
    imports = await db[IMPORTS].find({"user_id": owner}, {"_id": 0, "id": 1, "state": 1, "created_at": 1}).sort("created_at", -1).to_list(20)
    initialization = await db[INITIALIZATIONS].find_one({"_id": owner, "user_id": owner}, {"_id": 0, "state": 1, "import_id": 1})
    return {"products": product_choices, "components": components, "categories": categories, "locations": locations,
        "inventory_accounts": mappings["inventory"], "imports": imports, "initialization": initialization,
        "cutover": {"cutover_at": cutover.get("cutover_at"), "opening_txn_group_id": cutover.get("opening_active_txn_group_id"),
                    "evidence_ref": evidence_ref, "status": cutover.get("status")},
        "approval_requires_verified_opening_and_v2_writer": True}


async def ready(db, owner, document):
    await assert_writer_allowed(db._db, owner, "v2", mongo_session=db._session)
    settings = await db.settings.find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    if (document.opening_txn_group_id != cutover.get("opening_active_txn_group_id")
            or timestamp(document.cutover_at) != timestamp(cutover.get("cutover_at"))):
        fail("opening_changed")
    verified = await verify_active_opening_v2(db._db, user_id=owner, cutover=cutover, mongo_session=db._session)
    if not build_accounting_module_status(cutover, opening_posted_verified=verified)["cutover"]["safe_active"]:
        fail("verified_safe_active_opening_required")
    section = (cutover.get("evidence_sections") or {}).get("inventory")
    refs = {str(section)} if isinstance(section, str) else {str((section or {}).get(key) or "") for key in ("ref", "evidence_ref", "source_file_id")}
    if document.evidence_ref not in refs:
        fail("cutover_evidence_mismatch")
    # Any operational activity makes a retroactive opening initialization unsafe.
    entries = await read_reporting_entries_v2(db._db, user_id=owner,
        effective_before="9999-12-31T23:59:59.999999Z", limit=10000, mongo_session=db._session)
    if any(entry["entry_type"] not in {"opening_balance", "opening_replacement", "reversal"} for entry in entries):
        fail("operational_activity_present")
    for collection in (COST_STATES, "mezan_component_consumption_plans_v1", "mezan_component_consumption_units_v1", "mezan_component_order_lifecycle_v1"):
        if await db[collection].find_one({"user_id": owner}):
            fail("operational_activity_present")
    if await db[RECEIPTS].find_one({"user_id": owner, "$or": [{"schema_version": "g47-v1"}, {"source_type": "opening_inventory"}]}):
        fail("operational_activity_present")
    # The active replacement, if any, is a full reviewed replacement opening.
    legs = [entry for entry in entries if entry["txn_group_id"] == document.opening_txn_group_id
            and entry["entity_type"] == "asset" and entry["sub_account"] == "inventory"]
    balances = {}
    for leg in legs:
        account = leg["entity_id"]
        balances[account] = balances.get(account, 0) + int(leg["amount_minor"]) * (1 if leg["side"] == "debit" else -1)
    if not balances or any(value <= 0 for value in balances.values()):
        fail("positive_inventory_opening_required")
    await assert_open_journal_periods(db, owner, [{"metadata": {"accounting_at": document.cutover_at}}])
    return balances


async def compile_plan(db, owner, document, locations):
    ledger = await ready(db, owner, document)
    by_location = {row["id"]: row for row in locations}
    seen, target_locations, totals, plans = set(), set(), {}, []
    for row_model in document.rows:
        row = row_model.model_dump()
        quantity = exact(row["opening_quantity"])
        cost = exact(row["opening_unit_cost"])
        value = exact(row["opening_total_cost"], money=True)
        if (quantity * cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != value:
            fail("valuation_mismatch")
        identity, catalog = await resolve_line(db, owner, {**row, "quantity": row["opening_quantity"]})
        if identity["item_type"] == "stock_component":
            # Snapshot the actual catalog unit, without inventing a conversion
            # or allowing the import document to override it.
            identity = {**identity, "unit": catalog.get("unit")}
        key = identity_key(owner, identity)
        if key in seen:
            fail("duplicate_inventory_identity")
        seen.add(key)
        allocations, allocated = [], Decimal(0)
        for allocation in row["allocations"]:
            qty = exact(allocation["quantity"])
            if identity["item_type"] == "product" and qty != qty.to_integral_value():
                fail("product_quantity_must_be_integral", 422)
            allocated += qty
            location = by_location.get(allocation["location_id"])
            if not location or location.get("state") == "disabled" or not location.get("warehouse_id"):
                fail("location_unavailable")
            if location["id"] in target_locations:
                fail("duplicate_or_mixed_location")
            target_locations.add(location["id"])
            cabinet = await db.warehouse_locations_cabinets.find_one({"user_id": owner, "id": location.get("cabinet_id")}) or {}
            if (location.get("purpose") or cabinet.get("purpose")) != "permanent_storage":
                fail("permanent_location_required")
            barcode = str(location.get("barcode_value") or location.get("code") or "").strip().upper()
            if not barcode or barcode != allocation["scanned_location_barcode"].strip().upper():
                fail("location_barcode_mismatch")
            if location.get("max_items") is not None and qty > number(location["max_items"]):
                fail("location_capacity_exceeded")
            existing = [item for item in (location.get("occupancy") or {}).get("items") or [] if number(item.get("quantity", 0)) > 0]
            if identity["item_type"] == "stock_component" and any(item.get("unit") is not None and item.get("unit") != identity["unit"] for item in existing):
                fail("existing_unit_requires_reconciliation")
            if existing and (any(not same_identity(item, identity) for item in existing) or sum((number(item["quantity"]) for item in existing), Decimal(0)) != qty):
                fail("existing_quantity_requires_reconciliation")
            specs = canonical_specifications(allocation["specifications"])
            configuration = (stable_id("component-configuration", identity["resource_id"]) if identity["item_type"] == "stock_component"
                else build_inventory_configuration_key(sku=identity.get("sku") or identity["product_id"], preparation_state=allocation["preparation_state"], specifications=specs))
            if existing and any(item.get("configuration_key") != configuration
                    or item.get("preparation_state") != allocation["preparation_state"]
                    or canonical_specifications(item.get("specifications") or {}) != specs
                    or not (item.get("receipt_id") or item.get("lot_id")) for item in existing):
                fail("existing_configuration_requires_reconciliation")
            for item in existing:
                prior_receipt = await db[RECEIPTS].find_one({"user_id": owner, "id": item.get("receipt_id") or item.get("lot_id")}) or {}
                if any(record.get("source_type") == "stock_preparation_order"
                       or any(record.get(key) for key in ("component_provenance", "components_consumed", "manufacturing_provenance"))
                       for record in (item, prior_receipt)):
                    fail("existing_provenance_requires_reconciliation")
            # Preserve every pre-existing lot/source/provenance field. Opening
            # evidence adds valuation authority; it cannot rewrite history.
            adopted_items = [{**item, **identity} for item in existing]
            allocations.append({**allocation, "quantity": format(qty, "f"), "warehouse_id": location["warehouse_id"],
                "specifications": specs, "configuration_key": configuration, "adopted_items": adopted_items})
        if allocated != quantity:
            fail("allocation_quantity_mismatch")
        apportioned = [(value * number(item["quantity"]) / quantity).quantize(Decimal("0.01"), rounding=ROUND_DOWN) for item in allocations]
        remainder = int((value - sum(apportioned, Decimal(0))) * 100)
        ranked = sorted(range(len(allocations)), key=lambda i: (-(value * number(allocations[i]["quantity"]) / quantity - apportioned[i]), allocations[i]["location_id"]))
        for index in ranked[:remainder]:
            apportioned[index] += Decimal("0.01")
        for allocation, allocated_value in zip(allocations, apportioned):
            allocation["opening_total_cost"] = format(allocated_value, ".2f")
        account = row["inventory_account_id"]
        totals[account] = totals.get(account, 0) + int(value * 100)
        plans.append({"identity": identity, "cost_key": key, "inventory_account_id": account,
            "opening_quantity": format(quantity, "f"), "opening_unit_cost": format(cost, ".6f"), "opening_total_cost": format(value, ".2f"),
            "initial_unit_cost": catalog.get("initial_unit_cost") if identity["item_type"] == "stock_component" else None,
            "allocations": allocations})
    for location in locations:
        occupancy = location.get("occupancy") or {}
        quantity = sum((number(item.get("quantity", 0)) for item in occupancy.get("items") or []), Decimal(0))
        if number(occupancy.get("total_quantity", 0)) != quantity:
            fail("existing_quantity_requires_reconciliation")
        if quantity and location["id"] not in target_locations:
            fail("positive_stock_omitted")
    if totals != ledger:
        fail("ledger_valuation_mismatch", imported_minor=totals, ledger_minor=ledger)
    return sorted(plans, key=lambda row: row["cost_key"])


def public(row):
    return {key: value for key, value in row.items() if key != "_id"}


async def import_opening_inventory(db, *, user, content):
    document = parse_document(content)
    _, owner = await owner_actor(db, user)
    sha = hashlib.sha256(content).hexdigest()
    import_id = stable_id("opening-inventory", owner, document.opening_txn_group_id, sha)
    async def create(scoped):
        actor, _ = await owner_actor(scoped, user, owner)
        previous = await scoped[IMPORTS].find_one({"_id": import_id, "user_id": owner})
        if previous:
            return public(previous)
        if await scoped[INITIALIZATIONS].find_one({"_id": owner}):
            fail("already_initialized")
        locations = await snapshot(scoped, owner)
        plans = await compile_plan(scoped, owner, document, locations)
        file_id = stable_id("opening-inventory-evidence", owner, sha)
        await preserve_original(scoped, owner, file_id, content)
        row = {"_id": import_id, "id": import_id, "user_id": owner, "schema_version": SCHEMA, "state": "imported",
            "document": document.model_dump(), "evidence_sha256": sha, "source_file_id": file_id,
            "baseline_sha256": digest(locations), "baseline_locations": locations, "plan": plans,
            "created_at": datetime.now(timezone.utc).isoformat(), "created_by": actor["id"]}
        await scoped[IMPORTS].insert_one(row)
        return public(row)
    return await atomic_owner(db, owner, create)


async def approve_opening_inventory(db, *, user, import_id, evidence_sha256, baseline_sha256):
    _, owner = await owner_actor(db, user)
    async def approve(scoped):
        actor, _ = await owner_actor(scoped, user, owner)
        row = await scoped[IMPORTS].find_one({"_id": import_id, "user_id": owner})
        if not row:
            fail("import_not_found", 404)
        if evidence_sha256 != row["evidence_sha256"] or baseline_sha256 != row["baseline_sha256"]:
            fail("approval_identity_mismatch")
        if row["state"] == "approved":
            return public(row)
        if await scoped[INITIALIZATIONS].find_one({"_id": owner}):
            fail("already_initialized")
        source = await scoped.accounting_source_files.find_one({"user_id": owner, "file_id": row["source_file_id"]})
        if not source or hashlib.sha256(bytes(source.get("content") or b"")).hexdigest() != evidence_sha256 or source.get("sha256") != evidence_sha256:
            fail("evidence_changed")
        document = parse_document(bytes(source["content"]))
        locations = await snapshot(scoped, owner)
        if digest(locations) != baseline_sha256:
            fail("inventory_changed_since_import")
        plans = await compile_plan(scoped, owner, document, locations)
        if digest(plans) != digest(row["plan"]) or document.model_dump() != row["document"]:
            fail("catalog_changed_since_import")
        stamp, receipts = datetime.now(timezone.utc).isoformat(), []
        for plan in plans:
            identity = plan["identity"]
            for allocation in plan["allocations"]:
                receipt_id = stable_id("opening-inventory-receipt", import_id, plan["cost_key"], allocation["location_id"])
                item = {**identity, "receipt_id": receipt_id, "lot_id": receipt_id,
                    "quantity": stored_number(allocation["quantity"]), "configuration_key": allocation["configuration_key"],
                    "preparation_state": allocation["preparation_state"], "specifications": allocation["specifications"],
                    "source_type": "opening_inventory", "source_id": import_id, "cost_policy_version": COST_POLICY,
                    "unit_purchase_cost": plan["opening_unit_cost"], "placed_at": stamp}
                await scoped[RECEIPTS].insert_one({"_id": receipt_id, "id": receipt_id, "user_id": owner, "schema_version": SCHEMA,
                    **item, "location_id": allocation["location_id"], "warehouse_id": allocation["warehouse_id"],
                    "opening_total_cost": allocation["opening_total_cost"],
                    "adopted_receipt_ids": [old.get("receipt_id") or old.get("lot_id") for old in allocation["adopted_items"]],
                    "evidence_ref": document.evidence_ref, "source_file_id": row["source_file_id"], "evidence_sha256": evidence_sha256,
                    "opening_txn_group_id": document.opening_txn_group_id, "cutover_at": timestamp(document.cutover_at), "status": "posted"})
                await scoped[LOCATIONS].update_one({"user_id": owner, "id": allocation["location_id"]}, {"$set": {
                    "occupancy": {"items": allocation["adopted_items"] or [item], "total_quantity": item["quantity"]}, "state": "occupied", "updated_at": stamp}})
                receipts.append(receipt_id)
            await scoped[COST_STATES].insert_one({"_id": plan["cost_key"], "user_id": owner, "inventory_identity": identity,
                "average_cost": plan["opening_unit_cost"], "authoritative": True, "cost_policy_version": COST_POLICY,
                "opening_import_id": import_id, "opening_quantity": plan["opening_quantity"], "opening_total_cost": plan["opening_total_cost"],
                "opening_txn_group_id": document.opening_txn_group_id, "cutover_at": timestamp(document.cutover_at),
                "evidence_ref": document.evidence_ref, "source_file_id": row["source_file_id"], "evidence_sha256": evidence_sha256, "updated_at": stamp})
            if identity["item_type"] == "stock_component":
                metadata = component_cost_metadata(track_inventory=True, amount=plan["initial_unit_cost"], purchase_cost=stored_number(plan["opening_unit_cost"]))
                await scoped[RESOURCES].update_one({"user_id": owner, "id": identity["resource_id"]}, {"$set": {
                    **metadata, "cost_source": "approved_opening_inventory", "cost_policy_version": COST_POLICY, "opening_import_id": import_id}})
        await bump_product_cost_revision(scoped, owner)
        approval = {"state": "approved", "approved_by": actor["id"], "approved_at": stamp, "receipt_ids": receipts}
        await scoped[INITIALIZATIONS].insert_one({"_id": owner, "user_id": owner, "import_id": import_id,
            "opening_txn_group_id": document.opening_txn_group_id, "evidence_sha256": evidence_sha256, **approval})
        await scoped[IMPORTS].update_one({"_id": import_id, "state": "imported"}, {"$set": approval})
        return public({**row, **approval})
    return await atomic_owner(db, owner, approve)
