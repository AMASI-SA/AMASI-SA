"""Physical receipt evidence and quantity placement; never financial recognition.

All quantities live in warehouse_locations. Source identities are immutable;
partial financial adoption runs only inside the existing G47 owner transaction.
"""
from copy import deepcopy
from decimal import Decimal

from operational_atomic import operational_owner
from inventory_receipt_service import place_inventory_receipt
from product_inventory_rules import canonical_specifications, build_inventory_configuration_key

PENDING = "PENDING_FINANCIAL_VALUATION"
SOURCE = "operational_physical_receipt"
RECEIPTS = "mezan_inventory_receipts_v2"
LOCATIONS = "warehouse_locations"
EVENTS = "warehouse_location_events"


def helpers():
    # Deferred to avoid the financial service -> proof reader import cycle.
    import purchase_receiving_service as p
    return p


async def verified_pending(db, owner, location, item):
    p = helpers()
    if item.get("valuation_status") != PENDING or item.get("source_type") != SOURCE:
        return False
    rid = item.get("receipt_id")
    proof = await db[RECEIPTS].find_one({"_id": rid, "id": rid, "user_id": owner, "source_type": SOURCE})
    if not proof or proof.get("schema_version") != "physical-receipt-v1" or proof.get("status") != "posted":
        return False
    if proof.get("valuation_status") != PENDING or proof.get("location_id") != location.get("id") or proof.get("warehouse_id") != location.get("warehouse_id"):
        return False
    cutover, _ = await p.approved_account_mappings(db, owner)
    if not proof.get("cutover_id") or proof["cutover_id"] != cutover.get("opening_active_txn_group_id"):
        return False
    if rid != p.stable_id("physical-receipt", owner, proof.get("supplier_id"), proof.get("source_document_id"), proof.get("source_line_id")):
        return False
    identity = proof.get("inventory_identity") or {}
    if not identity or any(item.get(k) != v for k, v in identity.items()):
        return False
    if item.get("specifications") != proof.get("specifications") or item.get("preparation_state") != proof.get("preparation_state"):
        return False
    residual = p.number(proof["quantity"]) - p.number(proof.get("returned_quantity", 0)) - p.number(proof.get("adopted_quantity", 0))
    return residual >= 0 and p.number(item.get("quantity")) == residual


async def _context(db, user):
    from fulfillment_v2_routes import _actor_context, _require_permission
    from accounting_write_control import fresh_actor
    actor = await fresh_actor(db, user)
    context = await _actor_context(db, actor)
    _require_permission(context, "inventory.receipts.write")
    return actor, context


async def receive(db, *, user, payload):
    p = helpers()
    actor, context = await _context(db, user)
    owner = context["merchant_id"]
    allowed = {"supplier_id", "source_document_id", "source_line_id", "line", "location_id", "scanned_location_barcode", "preparation_state", "specifications", "unit", "operational_unit_cost"}
    if set(payload) - allowed or any(not isinstance(payload.get(k), str) or not payload[k].strip() for k in ("supplier_id", "source_document_id", "source_line_id", "unit")):
        p.fail("physical_receipt_source_required", 422)
    request = deepcopy(payload)
    rid = p.stable_id("physical-receipt", owner, request["supplier_id"], request["source_document_id"], request["source_line_id"])
    fingerprint = p.stable_id("physical-payload", request)
    async def commit(scoped):
        current_actor, current = await _context(scoped, user)
        if current["merchant_id"] != owner:
            p.fail("physical_owner_changed", 403)
        prior = await scoped[RECEIPTS].find_one({"_id": rid, "user_id": owner})
        if prior:
            from fulfillment_v2_routes import _warehouse_allowed
            if not _warehouse_allowed(current,[prior.get("warehouse_id")]):
                p.fail("inventory_warehouse_not_assigned",403)
            if prior.get("payload_fingerprint") != fingerprint:
                p.fail("physical_source_payload_conflict")
            return {k:v for k,v in prior.items() if k != "_id"}
        from supplier_identity_service import require_supplier_v2
        await require_supplier_v2(scoped, owner, request["supplier_id"])
        cutover, _ = await p.approved_account_mappings(scoped, owner)
        if not cutover.get("opening_active_txn_group_id"):
            p.fail("physical_cutover_identity_required")
        identity, catalog = await p.resolve_line(scoped, owner, request["line"])
        if request["unit"] != (catalog.get("unit") or "piece"):
            p.fail("physical_unit_mismatch", 422)
        quantity = p.number(request["line"]["quantity"])
        if quantity <= 0 or quantity > Decimal("1000000000") or quantity != quantity.quantize(p.PRECISION):
            p.fail("physical_quantity_invalid", 422)
        unit_cost = p.number(request["operational_unit_cost"])
        specs = canonical_specifications(request.get("specifications") or {})
        state = request.get("preparation_state")
        if state not in {"ready_complete", "requires_preparation"}:
            p.fail("inventory_preparation_state_required", 422)
        identity["configuration_key"] = (p.stable_id("component-configuration",identity["resource_id"]) if identity["item_type"] == "stock_component" else build_inventory_configuration_key(sku=identity.get("sku") or identity["product_id"],preparation_state=state,specifications=specs))
        location, barcode = await p._location(scoped, owner, request, identity, quantity, current)
        timestamp = p.now()
        proof = {"_id":rid,"id":rid,"receipt_id":rid,"user_id":owner,"schema_version":"physical-receipt-v1","source_type":SOURCE,"source_id":request["source_document_id"],"source_document_id":request["source_document_id"],"source_line_id":request["source_line_id"],"supplier_id":request["supplier_id"],"payload_fingerprint":fingerprint,"status":"posted","valuation_status":PENDING,"cutover_id":cutover["opening_active_txn_group_id"],"inventory_identity":identity,"quantity":float(quantity),"returned_quantity":0,"adopted_quantity":0,"unit":request["unit"],"operational_unit_cost":format(unit_cost,"f"),"operational_total_cost":format(unit_cost*quantity,"f"),"location_id":location["id"],"warehouse_id":location["warehouse_id"],"preparation_state":state,"specifications":specs,"occurred_at":timestamp,"recorded_by":current_actor["id"]}
        item = {**identity,"quantity":float(quantity),"receipt_id":rid,"lot_id":rid,"source_type":SOURCE,"source_id":request["source_document_id"],"source_line_id":request["source_line_id"],"valuation_status":PENDING,"preparation_state":state,"specifications":specs,"condition":"good","placed_at":timestamp}
        await scoped[RECEIPTS].insert_one(proof)
        await place_inventory_receipt(scoped,merchant_id=owner,location_id=location["id"],receipt_id=rid,inventory_item=item,quantity=float(quantity),scanned_barcode=barcode,occurred_at=timestamp)
        await scoped[EVENTS].insert_one({"_id":rid,"id":rid,"user_id":owner,"event_type":"physical_receipt","receipt_id":rid,"quantity":float(quantity),"occurred_at":timestamp,"recorded_by":current_actor["id"]})
        return {k:v for k,v in proof.items() if k != "_id"}
    return await operational_owner(db, owner, commit, profile="physical_receipt")


async def return_pending(db, *, user, receipt_id, source_return_id, quantity):
    p=helpers(); actor,context=await _context(db,user);owner=context["merchant_id"]
    q=p.number(quantity)
    if q<=0 or not isinstance(source_return_id,str) or not source_return_id.strip():p.fail("physical_return_invalid",422)
    eid=p.stable_id("physical-return",owner,receipt_id,source_return_id)
    async def commit(scoped):
        _,ctx=await _context(scoped,user)
        if ctx["merchant_id"]!=owner:p.fail("physical_owner_changed",403)
        old=await scoped[EVENTS].find_one({"_id":eid,"user_id":owner})
        if old:
            from fulfillment_v2_routes import _warehouse_allowed
            evidence=await scoped[RECEIPTS].find_one({"_id":receipt_id,"user_id":owner})
            if not evidence or not _warehouse_allowed(ctx,[evidence.get("warehouse_id")]):
                p.fail("inventory_warehouse_not_assigned",403)
            if p.number(old["quantity"])!=q:p.fail("physical_return_payload_conflict")
            return {k:v for k,v in old.items() if k!="_id"}
        proof=await scoped[RECEIPTS].find_one({"_id":receipt_id,"user_id":owner})
        if not proof:p.fail("physical_receipt_missing",404)
        location=await scoped[LOCATIONS].find_one({"user_id":owner,"id":proof["location_id"]})
        from fulfillment_v2_routes import _warehouse_allowed
        if not location or not _warehouse_allowed(ctx,[location["warehouse_id"]]):
            p.fail("inventory_warehouse_not_assigned",403)
        items=deepcopy((location.get("occupancy") or {}).get("items") or [])
        matches=[x for x in items if x.get("receipt_id")==receipt_id]
        if len(matches)!=1 or not await verified_pending(scoped,owner,location,matches[0]):p.fail("physical_receipt_unproven")
        item=matches[0]
        if q>p.number(item["quantity"]):p.fail("physical_return_exceeds_pending")
        if item.get("item_type")=="product" and q!=q.to_integral_value():p.fail("product_quantity_must_be_integral",422)
        item["quantity"]=float(p.number(item["quantity"])-q)
        await scoped[LOCATIONS].update_one({"id":location["id"],"user_id":owner},{"$set":{"occupancy":{"items":items,"total_quantity":float(sum((p.number(x["quantity"]) for x in items),Decimal(0)))}}})
        await scoped[RECEIPTS].update_one({"_id":receipt_id,"user_id":owner},{"$inc":{"returned_quantity":float(q)}})
        event={"_id":eid,"id":eid,"user_id":owner,"event_type":"physical_supplier_return","receipt_id":receipt_id,"quantity":float(q),"occurred_at":p.now(),"recorded_by":actor["id"]}
        await scoped[EVENTS].insert_one(event)
        return {k:v for k,v in event.items() if k!="_id"}
    return await operational_owner(db,owner,commit,profile="physical_receipt")


async def adopt_pending(db, owner, receipt_id, location_id, identity, quantity, new_item, financial_receipt, *, supplier_id, source_document_id, source_line_id=None):
    """Called exclusively inside G47 commit; keep total physical quantity fixed."""
    p=helpers();proof=await db[RECEIPTS].find_one({"_id":receipt_id,"user_id":owner})
    location=await db[LOCATIONS].find_one({"user_id":owner,"id":location_id})
    if not proof or not location:p.fail("physical_receipt_missing")
    if proof.get("supplier_id")!=supplier_id or proof.get("source_document_id")!=source_document_id or proof.get("source_line_id")!=source_line_id:p.fail("physical_invoice_source_mismatch")
    items=deepcopy((location.get("occupancy") or {}).get("items") or [])
    rows=[x for x in items if x.get("receipt_id")==receipt_id]
    if len(rows)!=1 or not await verified_pending(db,owner,location,rows[0]):p.fail("physical_receipt_unproven")
    item=rows[0]
    for row in (location, proof, item):
        if row.get("receipt_confirmed") is False:
            p.fail("physical_adoption_condition_blocked")
        for field in ("condition", "quality_status", "inspection_status", "saleability_status"):
            value = str(row.get(field) or "").strip().casefold()
            if value and value not in {"good", "accepted", "sellable", "passed"}:
                p.fail("physical_adoption_condition_blocked")
    if proof["inventory_identity"]!=identity or item.get("specifications")!=new_item.get("specifications") or item.get("preparation_state")!=new_item.get("preparation_state"):p.fail("physical_invoice_configuration_mismatch")
    quantity=p.number(quantity)
    if quantity<=0 or quantity>p.number(item["quantity"]):p.fail("physical_adoption_exceeds_pending")
    item["quantity"]=float(p.number(item["quantity"])-quantity)
    items.append(new_item)
    await db[LOCATIONS].update_one({"user_id":owner,"id":location_id},{"$set":{"occupancy.items":items}})
    await db[RECEIPTS].update_one({"_id":receipt_id,"user_id":owner},{"$inc":{"adopted_quantity":float(quantity)}})
    await db[EVENTS].insert_one({"_id":financial_receipt["id"],"id":financial_receipt["id"],"user_id":owner,"event_type":"physical_financial_adoption","receipt_id":receipt_id,"financial_receipt_id":financial_receipt["id"],"quantity":float(quantity),"operational_unit_cost":proof["operational_unit_cost"],"financial_unit_cost":financial_receipt["unit_purchase_cost"],"occurred_at":p.now()})


def validate_insert(name, doc):
    if name == EVENTS:
        return set(doc) <= {"_id","id","user_id","event_type","receipt_id","quantity","occurred_at","recorded_by"} and doc.get("event_type") in {"physical_receipt","physical_supplier_return"}
    return name == RECEIPTS and doc.get("source_type") == SOURCE and doc.get("schema_version") == "physical-receipt-v1" and doc.get("valuation_status") == PENDING and doc.get("status") == "posted" and doc.get("adopted_quantity") == 0 and doc.get("returned_quantity") == 0 and set(doc) <= {"_id","id","receipt_id","user_id","schema_version","source_type","source_id","source_document_id","source_line_id","supplier_id","payload_fingerprint","status","valuation_status","cutover_id","inventory_identity","quantity","returned_quantity","adopted_quantity","unit","operational_unit_cost","operational_total_cost","location_id","warehouse_id","preparation_state","specifications","occurred_at","recorded_by"}


async def validate_update(db, owner, name, query, update):
    p=helpers()
    if name == RECEIPTS:
        if set(update)!={"$inc"} or set(update["$inc"])!={"returned_quantity"}:return False
        proof=await db[RECEIPTS].find_one(query)
        delta=p.number(update["$inc"]["returned_quantity"])
        return bool(proof) and delta>0 and p.number(proof.get("returned_quantity",0))+p.number(proof.get("adopted_quantity",0))+delta<=p.number(proof["quantity"])
    if name != LOCATIONS:return False
    before=await db[LOCATIONS].find_one(query)
    if not before:
        return update == {"$set":{"occupancy":{"items":[],"total_quantity":0}}} and query.get("occupancy", "missing") is None
    old=before.get("occupancy")
    if update == {"$set":{"occupancy":{"items":[],"total_quantity":0}}}:return old is None
    if set(update)=={"$push","$inc","$set"}:
        if set(update["$push"])!={"occupancy.items"} or set(update["$inc"])!={"occupancy.total_quantity"} or set(update["$set"])-{"state","last_verified_scan","last_scan_verified_at","updated_at"}:return False
        item=update["$push"]["occupancy.items"]
        if any(x.get("receipt_id")==item.get("receipt_id") for x in (old or {}).get("items",[])):return False
        return p.number(item.get("quantity"))>0 and p.number(item["quantity"])==p.number(update["$inc"]["occupancy.total_quantity"]) and await verified_pending(db,owner,before,item)
    if set(update)!={"$set"} or set(update["$set"])!={"occupancy"}:return False
    new=update["$set"]["occupancy"]
    a=(old or {}).get("items") or [];b=new.get("items") or []
    if len(a)!=len(b) or p.number(new.get("total_quantity"))!=sum((p.number(x.get("quantity")) for x in b),Decimal(0)):return False
    changes=0
    for x,y in zip(a,b):
        if x==y:continue
        if {k:v for k,v in x.items() if k!="quantity"}!={k:v for k,v in y.items() if k!="quantity"}:return False
        if not await verified_pending(db,owner,before,x) or not Decimal(0)<=p.number(y.get("quantity"))<p.number(x.get("quantity")):return False
        changes+=1
    return changes==1


# Typed, authenticated HTTP seam shared by Web and mobile; no automatic posting.
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from fastapi import Depends


class PhysicalReceiptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: str = Field(min_length=1, max_length=200)
    source_document_id: str = Field(min_length=1, max_length=200)
    source_line_id: str = Field(min_length=1, max_length=200)
    line: dict
    location_id: str = Field(min_length=1, max_length=200)
    scanned_location_barcode: str = Field(min_length=1, max_length=200)
    preparation_state: Literal["ready_complete", "requires_preparation"]
    specifications: dict = Field(default_factory=dict)
    unit: str = Field(min_length=1, max_length=40)
    operational_unit_cost: str = Field(min_length=1, max_length=40)


class PhysicalReturnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_return_id: str = Field(min_length=1, max_length=200)
    quantity: str = Field(min_length=1, max_length=40)


def attach_physical_routes(router, db, current_user):
    @router.post("/physical-receipts")
    async def intake(payload: PhysicalReceiptInput, user=Depends(current_user)):
        return await receive(db,user=user,payload=payload.model_dump())

    @router.post("/physical-receipts/{receipt_id}/supplier-return")
    async def exit_stock(receipt_id: str, payload: PhysicalReturnInput, user=Depends(current_user)):
        return await return_pending(db,user=user,receipt_id=receipt_id,**payload.model_dump())
