"""Supplier purchases on account, recorded independently of cash or stock movements."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from operational_balance_service import active_gate, entity, fmt, money
from operational_balance_sources import instant
from operational_balance_store import audit, claim_movement, digest, fail, mutate, now, remember, replay
from operational_invoice_numbers import next_purchase_number, references, supplier_ref_key

DETAILS = "operational_supplier_credit_invoice_details_v1"


class PurchaseLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["product", "service"]
    name: str = Field(min_length=1, max_length=200)
    quantity: str = Field(pattern=r"^[0-9]{1,6}(?:\.[0-9]{1,3})?$")
    unit_price: str = Field(pattern=r"^[0-9]{1,10}(?:\.[0-9]{1,2})?$")

    @field_validator("quantity", "unit_price")
    @classmethod
    def positive(cls, value):
        if Decimal(value) <= 0:
            raise ValueError("positive_value_required")
        return value


class CreditInvoice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    lines: list[PurchaseLine] = Field(min_length=1, max_length=100)
    tax: str = Field(default="0.00", pattern=r"^[0-9]{1,10}(?:\.[0-9]{1,2})?$")


def invoice_totals(invoice):
    lines, subtotal = [], Decimal(0)
    for line in invoice["lines"]:
        total = money((Decimal(line["quantity"]) * money(line["unit_price"])).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP))
        lines.append({"kind": line["kind"], "name": line["name"], "quantity": line["quantity"],
                      "unit": fmt(line["unit_price"]), "total": fmt(total), "services": []})
        subtotal += total
    tax = money(invoice["tax"], zero=True)
    return lines, subtotal, tax, money(subtotal + tax)


async def record_purchase(db, owner, actor, payload, source):
    key = await claim_movement(db, owner, actor["id"], payload)
    action = "supplier_credit_purchase:" + actor["id"]
    stamp = now()
    supplier_id = payload["party_id"]

    async def apply(state):
        prior = replay(state, action, payload)
        if prior is not None:
            return prior
        lines, subtotal, tax, total = invoice_totals(payload["invoice"])
        await active_gate(db, owner, state)
        supplier = await entity(db, owner, "supplier", supplier_id, "SAR")
        day = date.fromisoformat(payload["business_date"])
        zone = ZoneInfo("Asia/Riyadh")
        if not instant(state["started_at"]).astimezone(zone).date() <= day <= instant(stamp).astimezone(zone).date():
            fail("supplier_purchase_date_invalid", "اختر تاريخ فاتورة ضمن فترة التشغيل وحتى اليوم", 422)
        if total != money(payload["amount"]):
            fail("supplier_purchase_total_mismatch", "المبلغ لا يطابق مجموع البنود والضريبة", 422)
        ref = payload.get("reference", "").strip()
        index = state.setdefault("supplier_credit_references", {})
        if ref:
            ref_key = supplier_ref_key(supplier_id, ref)
            existing = list(state.get("inventory_purchases", ()))
            existing.extend(p for case in state.get("customer_exchanges", ()) for p in case.get("purchases", ()))
            if (ref_key in index or any(p["supplier_id"] == supplier_id and ref in references(p) for p in existing)
                    or await db["mezan_supplier_invoices_v2"].find_one(
                        {"user_id": owner, "supplier_id": supplier_id, "invoice_number": ref}, {"_id": 1})):
                fail("supplier_purchase_duplicate", "رقم هذه الفاتورة مسجّل لهذا المورد", 409)
        number = next_purchase_number(state, [{"invoice_number": ref}])
        while (supplier_ref_key(supplier_id, number) in index or await db["mezan_supplier_invoices_v2"].find_one(
                {"user_id": owner, "supplier_id": supplier_id, "invoice_number": number}, {"_id": 1})):
            number = next_purchase_number(state, [])
        # Stage immutable line details. Only the committed claim makes them readable or payable.
        invoice_id = "credit-" + key
        detail = {"lines": lines, "tax": fmt(tax), "total": fmt(total),
                  "note": payload.get("note", ""), "supplier_reference": ref, "currency": "SAR"}
        detail_key = digest([owner, supplier_id, invoice_id, detail])
        await db[DETAILS].update_one({"_id": detail_key}, {"$setOnInsert": {
            "owner_id": owner, "supplier_id": supplier_id, "invoice_id": invoice_id, "detail": detail}}, upsert=True)
        obligation_id = "supplier-credit:" + key
        row = {"id": obligation_id, "kind": "supplier", "party_type": "supplier", "party_id": supplier_id,
               "name": supplier["name"], "currency": "SAR", "direction": "payable", "confirmed": fmt(total),
               "expected": "0.00", "business_date": day.isoformat(), "source_invoice_id": invoice_id,
               "source_invoice_number": number, "source_invoice_actor_id": actor["id"], "source_invoice_actor_name": actor.get("name"),
               "source_invoice_approved_at": stamp, "credit_detail_key": detail_key, "supplier_reference": ref,
               "evidence_ids": [invoice_id], "net_amount": fmt(subtotal), "tax_amount": fmt(tax), "gross_amount": fmt(total),
               "returned_amount": "0.00", "label": "فاتورة شراء آجل " + number}
        state.setdefault("engine", {}).setdefault("obligations", {})[obligation_id] = row
        for reference in (ref, number):
            if reference:
                index[supplier_ref_key(supplier_id, reference)] = invoice_id
        audit(state, actor["id"], "supplier_credit_purchase_saved", None, row, "شراء آجل من المورد", source, stamp)
        result = {"saved": True, "movement_id": obligation_id, "invoice_id": invoice_id, "invoice_number": number,
                  "party_type": "supplier", "party_id": supplier_id, "amount": fmt(total), "business_date": day.isoformat()}
        remember(state, action, payload, result)
        return result
    try:
        return await mutate(db, owner, apply)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") not in {
                "operational_request_conflict", "operational_request_scope_conflict", "operational_concurrent_change"}:
            exc.detail["not_applied"] = True
        raise
