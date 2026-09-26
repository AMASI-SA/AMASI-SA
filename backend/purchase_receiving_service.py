"""G47 full purchase approval: one owner transaction, one sealed V2 journal.

Quantity authority remains warehouse_locations. Valuation states contain cost,
never a second on-hand balance. Historical invoices are not migrated implicitly.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from accounting_atomic import atomic_owner
from accounting_ledger_v2 import AccountingLedgerV2Error, post_journal_v2, verify_active_opening_v2
from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_module_readiness import build_accounting_module_status
from accounting_periods import assert_open_journal_periods
from accounting_write_control import fresh_actor
from component_edit_policy import component_cost_metadata
from component_status_policy import component_is_active
from inventory_receipt_service import InventoryLocationCapacityError, place_inventory_receipt
from product_cost_revision import bump_product_cost_revision
from product_inventory_rules import build_inventory_configuration_key, canonical_specifications

SCHEMA = "g47-v1"
COST_POLICY = "moving-weighted-average-v1"
OPERATIONS = "mz2_purchase_receiving_operations"
COST_STATES = "mz2_inventory_cost_states"
RECEIPTS = "mezan_inventory_receipts_v2"
PRODUCTS = "mezan_products_v2"
RESOURCES = "mezan_cost_resources_v2"
LOCATIONS = "warehouse_locations"
MONEY = Decimal("0.01")
PRECISION = Decimal("0.000001")


def fail(code: str, status: int = 409, **details):
    raise HTTPException(status, detail={"code": code, **details})


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(kind: str, *values) -> str:
    data = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return kind + ":" + hashlib.sha256(data.encode()).hexdigest()


def number(value, *, nonnegative=True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        fail("purchase_invalid_number", 422)
    if not result.is_finite() or abs(result) > Decimal("1000000000000") or (nonnegative and result < 0):
        fail("purchase_invalid_number", 422)
    return result


def moving_average(previous_quantity, previous_cost, quantity, unit_cost) -> Decimal:
    previous = number(previous_quantity)
    received = number(quantity)
    cost = number(unit_cost)
    if received <= 0:
        fail("purchase_quantity_invalid", 422)
    if previous and previous_cost is None:
        fail("inventory_cost_reconciliation_required")
    old_cost = number(previous_cost) if previous else Decimal(0)
    return ((previous * old_cost + received * cost) / (previous + received)).quantize(PRECISION, rounding=ROUND_HALF_UP)


def purchase_amounts(lines, tax_amount, treatment):
    if treatment not in {"none", "deductible", "non_deductible"}:
        fail("purchase_tax_treatment_required", 422)
    tax = number(tax_amount).quantize(MONEY, rounding=ROUND_HALF_UP)
    if tax and treatment == "none":
        fail("purchase_tax_treatment_required", 422)
    amounts = {}
    for line in lines:
        quantity = number(line["quantity"])
        if quantity <= 0 or quantity != quantity.quantize(PRECISION):
            fail("purchase_quantity_invalid", 422)
        key = line["id"]
        if key in amounts:
            fail("purchase_duplicate_line", 422)
        amounts[key] = (quantity * number(line["unit_cost"])).quantize(MONEY, rounding=ROUND_HALF_UP)
    subtotal = sum(amounts.values(), Decimal(0))
    if subtotal <= 0:
        fail("purchase_total_invalid", 422)
    costs = dict(amounts)
    if treatment == "non_deductible" and tax:
        shares = {key: tax * amount / subtotal for key, amount in amounts.items()}
        allocated = {key: value.quantize(MONEY, rounding=ROUND_DOWN) for key, value in shares.items()}
        remainder = int((tax - sum(allocated.values())) / MONEY)
        for key in sorted(shares, key=lambda key: (-(shares[key] - allocated[key]), key))[:remainder]:
            allocated[key] += MONEY
        costs = {key: amount + allocated[key] for key, amount in amounts.items()}
    return {"subtotal": subtotal, "tax_amount": tax, "total": subtotal + tax,
            "input_vat": tax if treatment == "deductible" else Decimal(0),
            "inventory_total": sum(costs.values()), "line_costs": costs, "net_line_costs": amounts}


async def actor_scope(db, user, permission="accounting.inventory.view"):
    actor = await fresh_actor(db, user)
    require_accounting_permission(actor, permission)
    return actor, accounting_owner_id(actor)


async def resolve_line(db, owner, line):
    kind = line.get("item_type")
    if kind == "STOCK_COMPONENT":
        if line.get("product_id") or line.get("variant_id"):
            fail("component_product_identity_forbidden", 422)
        resource = await db[RESOURCES].find_one({"user_id": owner, "id": line.get("resource_id")})
        if not resource or resource.get("track_inventory") is not True or resource.get("kind") == "service" or not component_is_active(resource):
            fail("stock_component_unavailable", 422)
        category = str(line.get("category_id") or "")
        if not category or category not in [str(x) for x in resource.get("category_ids") or []]:
            fail("component_category_mismatch", 422)
        return {"item_type": "stock_component", "resource_id": resource["id"],
                "category_id": category, "category": category, "code": resource.get("code"),
                "product_name": resource.get("name"), "sku": None}, resource
    if kind != "PRODUCT" or line.get("resource_id"):
        fail("purchase_inventory_item_type_invalid", 422)
    product = await db[PRODUCTS].find_one({"user_id": owner, "mezan_product_id": line.get("product_id"), "archived": {"$ne": True}})
    if not product:
        fail("mezan_product_not_found", 404)
    variants = [v for v in product.get("variants") or [] if isinstance(v, dict) and v.get("id")]
    if product.get("variants_count") and not variants:
        fail("inventory_variants_not_loaded")
    variant_id = str(line.get("variant_id") or "")
    variant = next((v for v in variants if str(v["id"]) == variant_id), None)
    if variants and not variant_id:
        fail("inventory_variant_required", 422)
    if variant_id and not variant:
        fail("inventory_variant_not_found", 422)
    quantity = number(line["quantity"])
    if quantity != quantity.to_integral_value():
        fail("product_quantity_must_be_integral", 422)
    return {"item_type": "product", "product_id": product["mezan_product_id"],
            "mezan_product_id": product["mezan_product_id"], "salla_product_id": str(product.get("salla_product_id") or ""),
            "variant_id": variant_id or None, "salla_variant_id": variant_id or None,
            "sku": (variant or {}).get("sku") or product.get("sku"), "product_name": product.get("name")}, product


def identity_key(owner, item):
    if item["item_type"] == "stock_component":
        return stable_id("inventory-cost", owner, "stock_component", item["resource_id"])
    return stable_id("inventory-cost", owner, "product", item["product_id"], item.get("variant_id"))


def same_identity(item, identity):
    if identity["item_type"] == "stock_component":
        return item.get("item_type") == "stock_component" and item.get("resource_id") == identity["resource_id"]
    if item.get("item_type") == "stock_component" or item.get("resource_id"):
        return False
    product_ids = {identity["product_id"], identity.get("salla_product_id")}
    product_ids.discard("")
    return (item.get("mezan_product_id") in product_ids or item.get("product_id") in product_ids) and str(item.get("salla_variant_id") or item.get("variant_id") or "") == str(identity.get("variant_id") or "")


async def approved_account_mappings(db, owner):
    settings = await db.settings.find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    active = cutover.get("opening_active_txn_group_id")
    draft = await db.mz2_opening_balance_drafts.find_one({"user_id": owner, "status": "posted", "txn_group_id": active}) if active else None
    mappings = {"inventory": [], "supplier": [], "input_vat": []}
    categories = {"inventory_asset": ("inventory", "asset", "inventory"),
                  "supplier_payable": ("supplier", "supplier", "payable"),
                  "input_vat": ("input_vat", "tax", "input_vat")}
    for line in (draft or {}).get("lines") or []:
        rule = categories.get(line.get("category"))
        if rule and line.get("entity_type") == rule[1] and line.get("sub_account") == rule[2]:
            mappings[rule[0]].append({"entity_id": line["entity_id"], "label": line.get("label") or line["entity_id"]})
    return cutover, mappings


async def _validate_posting(db, owner, actor, invoice):
    require_accounting_permission(actor, "accounting.purchases.post")
    cutover, mappings = await approved_account_mappings(db, owner)
    # The sealed writer verifies the opening in the SAME session before any
    # effects commit. The caller separately enforces all readiness facts.
    verified = await verify_active_opening_v2(db._db, user_id=owner, cutover=cutover, mongo_session=db._session)
    status = build_accounting_module_status(cutover, opening_posted_verified=verified)
    if not status["cutover"]["safe_active"]:
        fail("purchase_accounting_not_safe_active")
    for key, field in [("inventory", "inventory_account_id"), ("supplier", "supplier_account_id")]:
        if invoice.get(field) not in {r["entity_id"] for r in mappings[key]}:
            fail("purchase_account_mapping_required", account=key)
    if invoice.get("supplier_account_id") != invoice.get("supplier_counterparty_id"):
        fail("purchase_supplier_account_identity_mismatch")
    if invoice["tax_treatment"] == "deductible":
        if invoice.get("input_vat_account_id") not in {r["entity_id"] for r in mappings["input_vat"]}:
            fail("purchase_account_mapping_required", account="input_vat")
        evidence = await db.mz2_purchase_tax_evidence.find_one({"user_id": owner, "file_id": invoice.get("tax_evidence_ref"),
            "supplier_counterparty_id": invoice["supplier_counterparty_id"], "invoice_number": invoice.get("invoice_number")})
        source = await db.accounting_source_files.find_one({"user_id": owner, "file_id": invoice.get("tax_evidence_ref")})
        if not invoice.get("tax_evidence_verified") or not evidence or not source or hashlib.sha256(bytes(source.get("content") or b"")).hexdigest() != evidence.get("sha256") or source.get("sha256") != evidence.get("sha256"):
            fail("purchase_tax_invoice_evidence_required")


async def _location(db, owner, receipt, identity, quantity, context):
    location = await db[LOCATIONS].find_one({"user_id": owner, "id": receipt["location_id"]})
    if not location or not location.get("warehouse_id") or location.get("state") == "disabled":
        fail("inventory_location_unavailable")
    from fulfillment_v2_routes import _warehouse_allowed
    if not _warehouse_allowed(context, [location["warehouse_id"]]):
        fail("inventory_warehouse_not_assigned", 403)
    cabinet = await db.warehouse_locations_cabinets.find_one({"user_id": owner, "id": location.get("cabinet_id")}) or {}
    if (location.get("purpose") or cabinet.get("purpose")) != "permanent_storage":
        fail("inventory_location_not_permanent")
    barcode = str(location.get("barcode_value") or location.get("code") or "").upper()
    if not barcode or barcode != str(receipt.get("scanned_location_barcode") or "").strip().upper():
        fail("inventory_location_barcode_mismatch")
    items = (location.get("occupancy") or {}).get("items") or []
    for item in items:
        current = number(item.get("quantity"))
        if current and (not same_identity(item, identity) or item.get("configuration_key") != identity.get("configuration_key")):
            fail("inventory_location_product_mismatch")
    if location.get("max_items") is not None and sum((number(item.get("quantity")) for item in items), Decimal(0)) + quantity > number(location["max_items"]):
        fail("inventory_location_capacity_exceeded")
    return location, barcode


async def invoice_public(db, owner, invoice):
    row = {k: v for k, v in invoice.items() if k != "_id"}
    if row.get("schema_version") == SCHEMA:
        operation = await db[OPERATIONS].find_one({"_id": row["approval_operation_id"], "user_id": owner}, {"_id": 0})
        row["operation"] = operation
        row["status"] = row["state"]
        row["paid_amount"] = 0
        row["remaining_amount"] = row["total"] if row["state"] == "approved" else 0
    else:
        row["legacy_read_only"] = True
    return row


async def approve_and_receive(db, *, user, invoice_id, payload):
    actor, owner = await actor_scope(db, user, "accounting.purchases.post")
    request = dict(payload)
    operation_id = request.get("operation_id")
    request_hash = stable_id("purchase-request", request)

    async def prepare(scoped):
        invoice = await scoped.purchase_invoices.find_one({"user_id": owner, "id": invoice_id})
        if not invoice or invoice.get("schema_version") != SCHEMA:
            fail("legacy_purchase_requires_reconciliation")
        if operation_id != invoice["approval_operation_id"] or request.get("expected_revision") != invoice["revision"]:
            fail("purchase_revision_conflict")
        prior = await scoped[OPERATIONS].find_one({"_id": operation_id, "user_id": owner})
        if prior:
            if prior["request_hash"] != request_hash:
                fail("purchase_approval_payload_conflict")
            return prior
        if invoice["state"] != "draft":
            fail("purchase_approval_state_conflict")
        lines = {line["id"]: line for line in invoice["lines"]}
        receipts = request.get("receipts") or []
        if len(receipts) != len(lines) or {r["line_id"] for r in receipts} != set(lines):
            fail("purchase_full_receive_required", 422)
        for receipt in receipts:
            if number(receipt["quantity"]) != number(lines[receipt["line_id"]]["quantity"]):
                fail("purchase_full_receive_required", 422)
        operation = {"_id": operation_id, "id": operation_id, "user_id": owner, "invoice_id": invoice_id,
            "revision": invoice["revision"], "request_hash": request_hash, "request": request,
            "status": "pending", "occurred_at": now(), "created_by": actor["id"],
            "receipt_ids": [stable_id("purchase-receipt", operation_id, line_id) for line_id in lines]}
        await scoped[OPERATIONS].insert_one(operation)
        await scoped.purchase_invoices.update_one({"user_id": owner, "id": invoice_id, "state": "draft", "revision": invoice["revision"]},
            {"$set": {"state": "approving", "updated_at": operation["occurred_at"]}})
        return operation

    operation = await atomic_owner(db, owner, prepare)
    if operation["status"] == "succeeded":
        return await invoice_public(db, owner, await db.purchase_invoices.find_one({"user_id": owner, "id": invoice_id}))

    async def commit(scoped):
        active_actor, active_owner = await actor_scope(scoped, user, "accounting.purchases.post")
        if active_owner != owner:
            fail("purchase_actor_scope_changed", 403)
        op = await scoped[OPERATIONS].find_one({"_id": operation_id, "user_id": owner})
        if op["status"] == "succeeded":
            return await invoice_public(scoped, owner, await scoped.purchase_invoices.find_one({"user_id": owner, "id": invoice_id}))
        invoice = await scoped.purchase_invoices.find_one({"user_id": owner, "id": invoice_id, "state": "approving"})
        if not invoice or invoice["revision"] != op["revision"]:
            fail("purchase_approval_state_conflict")
        await _validate_posting(scoped, owner, active_actor, invoice)
        from fulfillment_v2_routes import _actor_context, _require_permission
        context = await _actor_context(scoped, await scoped.users.find_one({"id": active_actor["id"]}))
        if context["merchant_id"] != owner:
            fail("purchase_inventory_owner_mismatch", 403)
        _require_permission(context, "inventory.receipts.write")
        amounts = purchase_amounts(invoice["lines"], invoice["tax_amount"], invoice["tax_treatment"])
        receipts_by_line = {r["line_id"]: r for r in op["request"]["receipts"]}
        locations = await scoped[LOCATIONS].find({"user_id": owner}).to_list(20001)
        if len(locations) > 20000:
            fail("purchase_inventory_scope_requires_reconciliation")
        written_receipts = []
        for line in invoice["lines"]:
            identity, catalog = await resolve_line(scoped, owner, line)
            receipt = receipts_by_line[line["id"]]
            quantity = number(line["quantity"])
            specs = canonical_specifications(receipt.get("specifications") or {})
            state = receipt.get("preparation_state")
            if state not in {"ready_complete", "requires_preparation"}:
                fail("inventory_preparation_state_required", 422)
            identity["configuration_key"] = (stable_id("component-configuration", identity["resource_id"]) if identity["item_type"] == "stock_component"
                else build_inventory_configuration_key(sku=identity.get("sku") or identity["product_id"], preparation_state=state, specifications=specs))
            location, barcode = await _location(scoped, owner, receipt, identity, quantity, context)
            # Re-read inside the transaction after every line: two invoice
            # lines for one identity must see the first line's receipt.
            all_locations = await scoped[LOCATIONS].find({"user_id": owner}).to_list(20001)
            on_hand = sum((number(item.get("quantity")) for loc in all_locations for item in (loc.get("occupancy") or {}).get("items") or []
                           if same_identity(item, identity)), Decimal(0))
            key = identity_key(owner, identity)
            cost_state = await scoped[COST_STATES].find_one({"_id": key, "user_id": owner})
            old_cost = cost_state.get("average_cost") if cost_state and cost_state.get("authoritative") is True and cost_state.get("cost_policy_version") == COST_POLICY else None
            capitalized_total = amounts["line_costs"][line["id"]]
            unit_purchase_cost = capitalized_total / quantity
            average = moving_average(on_hand, old_cost, quantity, unit_purchase_cost)
            receipt_id = stable_id("purchase-receipt", operation_id, line["id"])
            immutable = {"_id": receipt_id, "id": receipt_id, "receipt_id": receipt_id, "user_id": owner,
                "schema_version": SCHEMA, "status": "posted", "invoice_id": invoice_id, "line_id": line["id"],
                "purchase_invoice_id": invoice_id, "purchase_invoice_line_id": line["id"],
                "operation_id": operation_id, "quantity": float(quantity), "unit_purchase_cost": format(unit_purchase_cost, "f"),
                "total_purchase_cost": format(capitalized_total, "f"), "net_purchase_cost": format(amounts["net_line_costs"][line["id"]], "f"),
                "tax_treatment": invoice["tax_treatment"], "occurred_at": op["occurred_at"], "posted_at": op["occurred_at"],
                "currency": "SAR", "cost_policy_version": COST_POLICY, "inventory_identity": identity,
                "average_cost_before": old_cost, "average_cost_after": format(average, "f"), "on_hand_before": float(on_hand),
                "location_id": location["id"], "warehouse_id": location["warehouse_id"], **identity}
            await scoped[RECEIPTS].insert_one(immutable)
            item = {**identity, "quantity": float(quantity), "receipt_id": receipt_id, "lot_id": receipt_id,
                "preparation_state": state, "specifications": specs, "source_type": "purchase_invoice", "source_id": invoice_id,
                "source_line_id": line["id"], "placed_at": op["occurred_at"], "cost_policy_version": COST_POLICY,
                "unit_purchase_cost": format(unit_purchase_cost, "f"), "total_purchase_cost": format(capitalized_total, "f")}
            # A purchased ready unit has no evidence that OUR components were
            # consumed. Only manufacturing provenance may exempt future demand.
            await place_inventory_receipt(scoped, merchant_id=owner, location_id=location["id"], receipt_id=receipt_id,
                inventory_item=item, quantity=float(quantity), scanned_barcode=barcode, occurred_at=op["occurred_at"])
            await scoped[COST_STATES].update_one({"_id": key}, {"$set": {"user_id": owner, "inventory_identity": identity,
                "average_cost": format(average, "f"), "authoritative": True, "cost_policy_version": COST_POLICY,
                "last_receipt_id": receipt_id, "updated_at": op["occurred_at"]}}, upsert=True)
            if identity["item_type"] == "stock_component":
                metadata = component_cost_metadata(track_inventory=True, amount=catalog.get("initial_unit_cost"), purchase_cost=float(average))
                await scoped[RESOURCES].update_one({"user_id": owner, "id": identity["resource_id"]}, {"$set": {
                    **metadata, "cost_policy_version": COST_POLICY, "cost_receipt_id": receipt_id, "updated_at": op["occurred_at"]}})
            written_receipts.append(receipt_id)
        timestamp = op["occurred_at"]
        entries = [{"leg_key": "inventory", "entity_type": "asset", "entity_id": invoice["inventory_account_id"],
            "sub_account": "inventory", "entry_type": "purchase_inventory", "side": "debit", "amount": format(amounts["inventory_total"], "f"),
            "metadata": {"accounting_at": timestamp, "invoice_id": invoice_id}}]
        if amounts["input_vat"]:
            entries.append({"leg_key": "input_vat", "entity_type": "tax", "entity_id": invoice["input_vat_account_id"],
                "sub_account": "input_vat", "entry_type": "purchase_input_vat", "side": "debit", "amount": format(amounts["input_vat"], "f"),
                "metadata": {"accounting_at": timestamp, "evidence_ref": invoice["tax_evidence_ref"]}})
        entries.append({"leg_key": "payable", "entity_type": "supplier", "entity_id": invoice["supplier_account_id"],
            "sub_account": "payable", "entry_type": "purchase_payable", "side": "credit", "amount": format(amounts["total"], "f"),
            "metadata": {"accounting_at": timestamp, "invoice_id": invoice_id}})
        await assert_open_journal_periods(scoped, owner, entries)
        journal = await post_journal_v2(scoped._db, user_id=owner, actor_id=active_actor["id"], actor_name=active_actor["id"],
            idempotency_key=operation_id, txn_type="purchase_inventory", source="purchase_invoice", effective_at=timestamp,
            entries=entries, metadata={"invoice_id": invoice_id, "receipt_ids": written_receipts, "cost_policy_version": COST_POLICY}, mongo_session=scoped._session)
        group_id = journal["group"]["txn_group_id"]
        liability_id = stable_id("purchase-liability", operation_id)
        await scoped.liabilities.insert_one({"_id": liability_id, "id": liability_id, "user_id": owner, "kind": "supplier",
            "counterparty_id": invoice["supplier_counterparty_id"], "supplier_name": invoice["supplier_name"],
            "expected_amount": float(amounts["total"]), "paid_amount": 0, "status": "unpaid", "source": "purchase_invoice",
            "schema_version": SCHEMA, "purchase_invoice_id": invoice_id, "accounting_txn_group_id": group_id,
            "due_date": invoice.get("due_date") or invoice["invoice_date"], "created_at": timestamp, "updated_at": timestamp})
        await bump_product_cost_revision(scoped, owner)
        await scoped[OPERATIONS].update_one({"_id": operation_id}, {"$set": {"status": "succeeded", "receipt_ids": written_receipts,
            "txn_group_id": group_id, "completed_at": timestamp}, "$unset": {"error_code": ""}})
        await scoped.purchase_invoices.update_one({"id": invoice_id, "user_id": owner, "state": "approving"}, {"$set": {
            "state": "approved", "liability_id": liability_id, "txn_group_id": group_id, "receipt_ids": written_receipts,
            "approved_at": timestamp, "approved_by": active_actor["id"], "updated_at": timestamp}})
        return await invoice_public(scoped, owner, await scoped.purchase_invoices.find_one({"id": invoice_id, "user_id": owner}))
    try:
        return await atomic_owner(db, owner, commit)
    except (HTTPException, AccountingLedgerV2Error, InventoryLocationCapacityError) as exc:
        code = (exc.detail.get("code") if isinstance(exc.detail, dict) else str(exc.detail)) if isinstance(exc, HTTPException) else getattr(exc, "code", "purchase_approval_failed")
        await db[OPERATIONS].update_one({"_id": operation_id, "user_id": owner, "status": {"$ne": "succeeded"}},
            {"$set": {"status": "failed", "error_code": code, "updated_at": now()}})
        if isinstance(exc, HTTPException):
            raise
        fail(code)
    except PyMongoError:
        # Do not label an unknown commit as a proved abort. A retry reads the
        # operation first; the CAS cannot overwrite a committed success.
        await db[OPERATIONS].update_one({"_id": operation_id, "user_id": owner, "status": {"$ne": "succeeded"}},
            {"$set": {"status": "recovery_required", "error_code": "purchase_commit_outcome_requires_retry", "updated_at": now()}})
        fail("purchase_commit_outcome_requires_retry", 503)
