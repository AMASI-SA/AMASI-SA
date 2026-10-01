"""Salla canonical delivered is COD custody evidence; no receipt upload.

The external path reads the Order Engine provider snapshot and mapper. The
store-driver path separately requires #1211's bound operational collection.
"""
from datetime import datetime, timezone
from decimal import Decimal

from accounting_shipping_native_contract import digest, money, amount, instant, fail

DELIVERED = {"delivered", "تم التوصيل"}
COD = {"cod", "cash_on_delivery", "cash on delivery", "الدفع عند الاستلام", "دفع عند الاستلام", "دفع عند الإستلام"}


def _number(value):
    if isinstance(value, dict):
        value = value.get("amount")
    return money(value)


def canonical_facts(raw, *, order_number, source_revision=None, require_cod=True, require_carrier=True,
                    driver_amount=None, driver_delivered_at=None):
    # Order Engine's package initializes its operational routes. Load it only
    # when consuming evidence, not when registering the accounting/setup API.
    from order_engine.mapper import map_salla_order, OrderMappingError
    try:
        order = map_salla_order(raw)
    except (OrderMappingError, ValueError, TypeError):
        fail("MZ2_COURIER_COD_COLLECTION_EVIDENCE_REQUIRED")
    if order.order_number != order_number or not raw.get("id"):
        fail("shipping_order_identity_conflict")
    status = (order.status or "").strip().casefold()
    if driver_delivered_at and status in {"cancelled", "canceled", "refunded", "ملغي", "ملغى"}:
        fail("shipping_cancelled_or_refunded")
    if not driver_delivered_at and status not in DELIVERED:
        fail("shipping_canonical_delivered_required")
    method = (order.payment.method or "").strip().casefold()
    is_cod = method in COD
    if require_cod and not is_cod:
        fail("shipping_cod_payment_required")
    if not method:
        fail("shipping_payment_method_required")
    if order.cancelled_at or order.refunded_at:
        fail("shipping_cancelled_or_refunded")
    payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}
    for row in (raw, payment):
        if str(row.get("payment_status") or row.get("status") or "").lower() in {"refunded", "cancelled", "canceled"}:
            fail("shipping_cancelled_or_refunded")
        for key in ("refunded_amount", "refund_amount", "refunded_sar"):
            if row.get(key) is not None and _number(row[key]) != 0:
                fail("shipping_cancelled_or_refunded")
    amounts = raw.get("amounts") if isinstance(raw.get("amounts"), dict) else {}
    totals = [_number(v) for v in (amounts.get("total"), raw.get("total"), raw.get("total_amount")) if v is not None]
    if not totals or len(set(totals)) != 1 or totals[0] <= 0:
        fail("shipping_cod_amount_missing_or_ambiguous")
    total = totals[0]
    currencies = [r.get("currency") for r in (raw, payment, amounts,
        amounts.get("total") if isinstance(amounts.get("total"), dict) else {},
        raw.get("total") if isinstance(raw.get("total"), dict) else {}) if r.get("currency")]
    if not currencies or any(c != "SAR" for c in currencies) or order.totals.currency != "SAR":
        fail("shipping_currency_unsupported")
    remaining_action = raw.get("remaining_action") if isinstance(raw.get("remaining_action"), dict) else {}
    actions = raw.get("payment_actions") if isinstance(raw.get("payment_actions"), dict) else {}
    canonical_remaining = actions.get("remaining_action") if isinstance(actions.get("remaining_action"), dict) else {}
    refund_action = actions.get("refund_action") if isinstance(actions.get("refund_action"), dict) else {}
    remaining = [_number(r["remaining_amount"]) for r in (raw, payment, remaining_action, canonical_remaining) if r.get("remaining_amount") is not None]
    paid = [_number(r["paid_amount"]) for r in (raw, payment, remaining_action, canonical_remaining, refund_action) if r.get("paid_amount") is not None]
    if len(set(remaining)) > 1 or len(set(paid)) > 1:
        fail("shipping_cod_amount_missing_or_ambiguous")
    # A source COD order total is due on delivery unless an explicit source
    # remaining/paid fact says otherwise. Never use the DTO's synthesized zero.
    due = (remaining[0] if remaining else total - (paid[0] if paid else Decimal(0))) if is_cod else Decimal(0)
    if driver_amount is None and is_cod and (due <= 0 or due > total or (paid and due + paid[0] != total)):
        fail("shipping_cod_amount_missing_or_ambiguous")
    if driver_amount is None and is_cod and str(order.payment.collection_status or "") == "partial" and not remaining and not paid:
        fail("shipping_cod_amount_missing_or_ambiguous")
    if driver_amount is not None:
        # #1211's delivery collection amount snapshots the outstanding amount
        # at handover. Cash custody is a different fact; a later Salla payment
        # update must not replace this responsibility snapshot with zero.
        due = money(driver_amount)
        if due > total:
            fail("shipping_driver_collection_amount_conflict")
    carrier = order.shipping.company_code or order.shipping.company
    if require_carrier and not carrier:
        fail("shipping_canonical_identity_required")
    source_time = source_revision or raw.get("updated_at") or raw.get("date_updated")
    if isinstance(source_time, dict):
        source_time = source_time.get("date")
    event_at = driver_delivered_at or order.shipping.delivered_at or source_time
    if event_at is None:
        fail("shipping_delivery_timestamp_required")
    when = instant(event_at)
    if when < order.created_at or when > datetime.now(timezone.utc):
        fail("shipping_delivery_timestamp_invalid")
    return {"order_id": order.order_id, "order_number": order_number,
            "order_created_at": order.created_at.isoformat(), "delivery_event_at": when.isoformat(),
            "payment_method": "COD" if is_cod else method, "delivery_status": "delivered", "cod_amount": amount(due),
            "sale_total": amount(total), "currency": "SAR", "source_carrier_key": carrier,
            "operational_source": "order_engine.salla_direct", "source_record_id": order.source.source_order_id,
            "source_revision": str(source_time or when.isoformat()), "source_hash": digest(raw),
            "provenance_status": "canonical_salla_delivered", "version": 1, "context": "delivery"}


async def external_facts(db, owner, order_number, setup, *, require_cod=True):
    from order_engine.repository import MongoOrderRepository
    repository = MongoOrderRepository(db)
    snapshot = await repository.financial_delivery_snapshot(user_id=owner, order_number=order_number)
    if not snapshot:
        fail("MZ2_COURIER_COD_COLLECTION_EVIDENCE_REQUIRED")
    watermark = snapshot.get("g47_salla_snapshot") or {}
    if watermark.get("requires_authoritative_refresh") or watermark.get("cancelled"):
        fail("shipping_source_conflict")
    facts = canonical_facts(snapshot["raw_by_source"]["salla_direct"], order_number=order_number,
                            source_revision=watermark.get("source_updated_at"), require_cod=require_cod)
    parties = [r for r in setup["couriers"] if r["status"] == "active" and r.get("confirmed_by")
               and r.get("confirmed_at") and facts["source_carrier_key"] in r["salla_carrier_keys"]]
    if len(parties) != 1:
        fail("shipping_canonical_identity_required")
    changed = await repository.pin_financial_delivery_snapshot(user_id=owner, snapshot=snapshot)
    if changed.matched_count != 1:
        fail("shipping_source_changed")
    return {**facts, "user_id": owner, "party_type": "courier", "party_id": parties[0]["courier_key"]}


async def driver_facts(db, owner, assignment_id, *, require_cod=True):
    from order_engine.repository import MongoOrderRepository
    assignment = await db.store_delivery_assignments.find_one({"user_id": owner, "id": assignment_id})
    if not assignment or assignment.get("status") != "delivered":
        fail("shipping_driver_delivery_required")
    identity = assignment.get("driver_id")
    collection = await db.store_delivery_collections.find_one({"user_id": owner,
        "assignment_id": assignment_id, "driver_id": identity})
    if not collection or collection.get("accounting_status") != "operational_only":
        fail("shipping_driver_collection_required")
    responsibility = money(collection.get("amount"), positive=require_cod)
    method = collection.get("payment_method")
    if responsibility > 0 and method not in {"cash", "bank_transfer", "card_terminal"}:
        fail("shipping_driver_collection_method_required")
    proof = await db.store_delivery_delivery_proofs.find_one({"user_id": owner,
        "driver_id": identity, "token": collection.get("delivery_proof_reference"),
        "status": "bound", "bound_assignment_id": assignment_id})
    if not proof:
        fail("shipping_driver_bound_delivery_proof_required")
    repository = MongoOrderRepository(db)
    number = str(assignment.get("order_number") or "")
    snapshot = await repository.financial_delivery_snapshot(user_id=owner, order_number=number)
    if not snapshot:
        fail("shipping_driver_order_evidence_required")
    watermark = snapshot.get("g47_salla_snapshot") or {}
    if watermark.get("requires_authoritative_refresh") or watermark.get("cancelled"):
        fail("shipping_source_conflict")
    facts = canonical_facts(snapshot["raw_by_source"]["salla_direct"], order_number=number,
                            source_revision=assignment.get("delivered_at"), require_cod=False, require_carrier=False,
                            driver_amount=responsibility, driver_delivered_at=assignment.get("delivered_at"))
    custody = money(collection.get("cod_custody_amount"))
    if (method == "cash" and custody != responsibility) or custody > responsibility:
        fail("shipping_driver_collection_amount_conflict")
    if (collection.get("order_number") != number or collection.get("order_id") != assignment.get("order_id")
            or str(assignment.get("order_id")) != facts["order_id"]):
        fail("shipping_driver_collection_identity_conflict")
    for name, row in (("store_delivery_assignments", assignment), ("store_delivery_collections", collection),
                      ("store_delivery_delivery_proofs", proof)):
        query = {key: {"$eq": value} for key, value in row.items() if key != "mz2_shipping_pin"}
        changed = await db[name].update_one(query, {"$inc": {"mz2_shipping_pin": 1}})
        if changed.matched_count != 1:
            fail("shipping_driver_source_changed")
    changed = await repository.pin_financial_delivery_snapshot(user_id=owner, snapshot=snapshot)
    if changed.matched_count != 1:
        fail("shipping_source_changed")
    return {**facts, "user_id": owner, "party_type": "store_driver", "party_id": identity,
            "operational_source": "store_delivery_collections", "collection_id": collection.get("id"),
            "assignment_id": assignment_id, "delivery_proof_reference": proof["token"],
            "driver_responsibility_amount": amount(responsibility), "collection_method": method,
            "source_hash": digest([facts["source_hash"], {k: v for k, v in collection.items() if k not in {"_id", "mz2_shipping_pin"}}])}
