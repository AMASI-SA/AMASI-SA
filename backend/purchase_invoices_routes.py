"""G47 purchase drafts and full atomic approval. Historical invoices are read-only."""
from __future__ import annotations
import hashlib
import uuid
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from accounting_atomic import atomic_owner
from accounting_source_files import MAX_BYTES, preserve_original
from auth import get_current_user_from_db
from component_status_policy import component_is_active
from purchase_receiving_service import (
    SCHEMA, PRODUCTS, RESOURCES, OPERATIONS, actor_scope, approve_and_receive,
    approved_account_mappings, fail, invoice_public, now, purchase_amounts, resolve_line, stable_id, stored_number,
)

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class InvoiceLine(StrictModel):
    id: str | None = Field(None, max_length=120)
    item_type: Literal["PRODUCT", "STOCK_COMPONENT"]
    product_id: str | None = None
    variant_id: str | None = None
    resource_id: str | None = None
    category_id: str | None = None
    product_name: str | None = Field(None, max_length=200)
    sku: str | None = Field(None, max_length=120)
    quantity: float = Field(gt=0, le=1e12)
    unit_cost: float = Field(ge=0, le=1e12)

class PurchaseInvoiceCreate(StrictModel):
    supplier_counterparty_id: str = Field(min_length=1, max_length=120)
    invoice_number: str | None = Field(None, max_length=80)
    invoice_date: date
    due_date: date | None = None
    lines: list[InvoiceLine] = Field(min_length=1, max_length=100)
    tax_amount: float = Field(0, ge=0, le=1e12)
    tax_treatment: Literal["none", "deductible", "non_deductible"]
    tax_evidence_ref: str | None = None
    tax_evidence_verified: bool = False
    inventory_account_id: str | None = None
    input_vat_account_id: str | None = None
    supplier_account_id: str | None = None
    notes: str = Field("", max_length=2000)

class PurchaseInvoiceUpdate(PurchaseInvoiceCreate):
    expected_revision: int = Field(ge=1)

class ReceiptLine(StrictModel):
    line_id: str
    quantity: float = Field(gt=0, le=1e12)
    location_id: str
    scanned_location_barcode: str = Field(min_length=1, max_length=200)
    preparation_state: Literal["ready_complete", "requires_preparation"]
    specifications: dict = Field(default_factory=dict)

class PurchaseApproval(StrictModel):
    expected_revision: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=150)
    receipts: list[ReceiptLine] = Field(min_length=1, max_length=100)

async def _supplier(db, owner, supplier_id):
    row = await db.counterparties.find_one({"user_id": owner, "id": supplier_id, "kind": {"$in": ["supplier", "general"]}})
    if not row:
        fail("purchase_supplier_not_found", 404)
    return row

async def _write_actor(db, user, owner):
    actor, current_owner = await actor_scope(db, user, "accounting.purchases.post")
    if current_owner != owner:
        fail("purchase_actor_scope_changed", 403)
    return actor

async def _draft_values(db, owner, payload):
    values = payload.model_dump(mode="json", exclude={"expected_revision"})
    supplier = await _supplier(db, owner, values["supplier_counterparty_id"])
    lines = []
    for submitted in values["lines"]:
        line = dict(submitted)
        line["id"] = line.get("id") or str(uuid.uuid4())
        identity, _ = await resolve_line(db, owner, line)
        line.update({key: value for key, value in identity.items() if key in {"product_name", "sku", "code", "category"}})
        lines.append(line)
    amounts = purchase_amounts(lines, values["tax_amount"], values["tax_treatment"])
    for line in lines:
        line["line_total"] = stored_number(amounts["net_line_costs"][line["id"]])
    values.update(lines=lines, supplier_name=supplier["name"], subtotal=stored_number(amounts["subtotal"]),
                  tax_amount=stored_number(amounts["tax_amount"]), total=stored_number(amounts["total"]), currency="SAR")
    return values

async def _public(db, owner, doc):
    row = await invoice_public(db, owner, doc)
    if doc.get("liability_id"):
        liab = await db.liabilities.find_one({"user_id": owner, "id": doc["liability_id"]})
        if liab:
            row["paid_amount"] = float(liab.get("paid_amount") or 0)
            row["remaining_amount"] = max(0, round(float(liab["expected_amount"]) - row["paid_amount"], 2))
            row["payment_status"] = liab.get("status")
            if row.get("legacy_read_only"):
                row["status"] = liab.get("status", "unpaid")
    row.setdefault("paid_amount", 0)
    row.setdefault("remaining_amount", 0)
    return row

async def ensure_purchase_invoices_indexes(db):
    await db.purchase_invoices.create_index([("user_id", 1), ("id", 1)], unique=True, name="pinv_pk")
    await db.purchase_invoices.create_index([("user_id", 1), ("supplier_counterparty_id", 1), ("invoice_date", -1)], name="pinv_supplier_date")

def attach_purchase_invoice_routes(parent_router: APIRouter, db):
    async def current_user(request: Request):
        return await get_current_user_from_db(request, db)
    router = APIRouter(prefix="/purchase-invoices", tags=["purchase-invoices"])

    @router.get("/catalog")
    async def catalog(user=Depends(current_user)):
        actor, owner = await actor_scope(db, user)
        from fulfillment_v2_routes import _actor_context, _warehouse_allowed
        context = await _actor_context(db, await db.users.find_one({"id": actor["id"]}))
        if context["merchant_id"] != owner:
            fail("purchase_actor_scope_changed", 403)
        products = []
        for row in await db[PRODUCTS].find({"user_id": owner, "archived": {"$ne": True}}).to_list(10001):
            variants = [{"variant_id": str(v["id"]), "name": v.get("name") or v.get("sku") or str(v["id"]),
                         "sku": v.get("sku"), "barcode": v.get("barcode")}
                        for v in row.get("variants") or [] if isinstance(v, dict) and v.get("id")]
            products.append({"id": row["mezan_product_id"], "product_id": row["mezan_product_id"],
                             "name": row.get("name"), "sku": row.get("sku"), "barcode": row.get("barcode"),
                             "variants": variants, "variants_required": bool(variants or row.get("variants_count"))})
        resources = await db[RESOURCES].find({"user_id": owner, "track_inventory": True}).to_list(10001)
        components = [{"id": r["id"], "resource_id": r["id"], "name": r.get("name"), "code": r.get("code"),
                       "category_ids": r.get("category_ids") or [], "track_inventory": True}
                      for r in resources if component_is_active(r) and r.get("kind") != "service"]
        categories = await db.mezan_component_categories_v2.find({"user_id": owner}, {"_id": 0, "id": 1, "name": 1}).to_list(10001)
        cabinets = {c["id"]: c for c in await db.warehouse_locations_cabinets.find({"user_id": owner}).to_list(10001)}
        locations = []
        for loc in await db.warehouse_locations.find({"user_id": owner, "state": {"$ne": "disabled"}}).to_list(20001):
            if (loc.get("purpose") or cabinets.get(loc.get("cabinet_id"), {}).get("purpose")) != "permanent_storage":
                continue
            if loc.get("warehouse_id") and _warehouse_allowed(context, [loc["warehouse_id"]]):
                locations.append({"id": loc["id"], "code": loc.get("code"), "barcode": loc.get("barcode_value") or loc.get("code"), "warehouse_id": loc["warehouse_id"]})
        _, mappings = await approved_account_mappings(db, owner)
        return {"products": products, "components": components, "categories": categories, "locations": locations, "account_mappings": mappings}

    @router.post("/tax-evidence")
    async def tax_evidence(file: UploadFile = File(...), supplier_counterparty_id: str = Form(...),
                           invoice_number: str = Form(...), user=Depends(current_user)):
        _, owner = await actor_scope(db, user, "accounting.purchases.post")
        await _supplier(db, owner, supplier_counterparty_id)
        content = await file.read(MAX_BYTES + 1)
        allowed = content.startswith(b"%PDF-") or content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff")
        if not allowed or not content or len(content) > MAX_BYTES:
            fail("purchase_tax_evidence_file_invalid", 422)
        file_id = stable_id("purchase-tax-file", owner, supplier_counterparty_id, invoice_number, hashlib.sha256(content).hexdigest())
        async def preserve(scoped):
            await _write_actor(scoped, user, owner)
            digest = await preserve_original(scoped, owner, file_id, content)
            await scoped.mz2_purchase_tax_evidence.update_one({"_id": file_id}, {"$setOnInsert": {
                "user_id": owner, "file_id": file_id, "supplier_counterparty_id": supplier_counterparty_id,
                "invoice_number": invoice_number, "sha256": digest, "filename": (file.filename or "invoice")[:200], "created_at": now()}}, upsert=True)
            return {"file_id": file_id, "sha256": digest, "filename": (file.filename or "invoice")[:200]}
        return await atomic_owner(db, owner, preserve)

    @router.post("")
    async def create_invoice(payload: PurchaseInvoiceCreate, user=Depends(current_user)):
        _, owner = await actor_scope(db, user, "accounting.purchases.post")
        inv_id = str(uuid.uuid4())
        async def create(scoped):
            await _write_actor(scoped, user, owner)
            values = await _draft_values(scoped, owner, payload)
            stamp = now()
            doc = {**values, "_id": stable_id("purchase-invoice", owner, inv_id), "id": inv_id, "user_id": owner,
                   "schema_version": SCHEMA, "state": "draft", "revision": 1,
                   "approval_operation_id": stable_id("purchase-approval", owner, inv_id, 1), "created_at": stamp, "updated_at": stamp}
            await scoped.purchase_invoices.insert_one(doc)
            return await _public(scoped, owner, doc)
        return await atomic_owner(db, owner, create)

    @router.get("")
    async def list_invoices(supplier_id: str | None = None, status: str | None = None,
                            from_date: str | None = Query(None, alias="from"), to_date: str | None = Query(None, alias="to"),
                            limit: int = Query(200, ge=1, le=2000), user=Depends(current_user)):
        _, owner = await actor_scope(db, user)
        query = {"user_id": owner}
        if supplier_id:
            query["supplier_counterparty_id"] = supplier_id
        if from_date:
            query["invoice_date"] = {"$gte": from_date}
        if to_date:
            query.setdefault("invoice_date", {})["$lte"] = to_date
        rows = []
        for doc in await db.purchase_invoices.find(query).sort([("invoice_date", -1), ("created_at", -1)]).to_list(limit):
            row = await _public(db, owner, doc)
            if not status or row.get("status") == status or row.get("payment_status") == status:
                rows.append(row)
        return {"items": rows, "total": len(rows)}

    @router.get("/supplier/{cp_id}/statement")
    async def supplier_statement(cp_id: str, user=Depends(current_user)):
        _, owner = await actor_scope(db, user)
        supplier = await _supplier(db, owner, cp_id)
        rows = []
        async for doc in db.purchase_invoices.find({"user_id": owner, "supplier_counterparty_id": cp_id}):
            if doc.get("schema_version") == SCHEMA and doc.get("state") != "approved":
                continue
            rows.append(await _public(db, owner, doc))
        invoiced = round(sum(r["total"] for r in rows), 2)
        paid = round(sum(r["paid_amount"] for r in rows), 2)
        return {"supplier": {k: supplier.get(k) for k in ("id", "name", "kind")},
                "totals": {"total_invoiced": invoiced, "total_paid": paid, "balance_owed": max(0, round(invoiced - paid, 2))},
                "invoices": rows, "generated_at": now()}

    @router.get("/{inv_id}")
    async def get_invoice(inv_id: str, user=Depends(current_user)):
        _, owner = await actor_scope(db, user)
        doc = await db.purchase_invoices.find_one({"id": inv_id, "user_id": owner})
        if not doc:
            fail("purchase_invoice_not_found", 404)
        return await _public(db, owner, doc)

    @router.put("/{inv_id}")
    async def update_invoice(inv_id: str, payload: PurchaseInvoiceUpdate, user=Depends(current_user)):
        _, owner = await actor_scope(db, user, "accounting.purchases.post")
        async def update(scoped):
            await _write_actor(scoped, user, owner)
            doc = await scoped.purchase_invoices.find_one({"user_id": owner, "id": inv_id})
            if not doc or doc.get("schema_version") != SCHEMA:
                fail("legacy_purchase_requires_reconciliation")
            if doc["state"] != "draft" or doc["revision"] != payload.expected_revision or await scoped[OPERATIONS].find_one({"_id": doc["approval_operation_id"]}):
                fail("purchase_draft_locked_or_revision_conflict")
            values = await _draft_values(scoped, owner, payload)
            revision = doc["revision"] + 1
            values.update(revision=revision, updated_at=now(), approval_operation_id=stable_id("purchase-approval", owner, inv_id, revision))
            await scoped.purchase_invoices.update_one({"_id": doc["_id"], "state": "draft", "revision": doc["revision"]}, {"$set": values})
            return await _public(scoped, owner, {**doc, **values})
        return await atomic_owner(db, owner, update)

    @router.delete("/{inv_id}")
    async def delete_invoice(inv_id: str, expected_revision: int = Query(..., ge=1), user=Depends(current_user)):
        _, owner = await actor_scope(db, user, "accounting.purchases.post")
        async def delete(scoped):
            await _write_actor(scoped, user, owner)
            doc = await scoped.purchase_invoices.find_one({"user_id": owner, "id": inv_id})
            if not doc or doc.get("schema_version") != SCHEMA:
                fail("legacy_purchase_requires_reconciliation")
            if doc["state"] != "draft" or doc["revision"] != expected_revision or await scoped[OPERATIONS].find_one({"_id": doc["approval_operation_id"]}):
                fail("purchase_draft_locked_or_revision_conflict")
            await scoped.purchase_invoices.delete_one({"_id": doc["_id"], "revision": expected_revision, "state": "draft"})
            return {"ok": True}
        return await atomic_owner(db, owner, delete)

    @router.post("/{inv_id}/approve-receive")
    async def approve(inv_id: str, payload: PurchaseApproval, user=Depends(current_user)):
        return await approve_and_receive(db, user=user, invoice_id=inv_id, payload=payload.model_dump(mode="json"))
    parent_router.include_router(router)
