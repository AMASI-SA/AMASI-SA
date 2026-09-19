"""Supported Mezan 2 order recognition, shared by accountant and BNPL ingress.

No generated provider identities, opening balances, settlement writes or
provider network calls. Uncertain posting results remain blocked for recovery.
"""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal
from accounting_atomic import atomic_owner

from accounting_sales_tax import TaxError, refund_split
from accounting_sales_tax_service import read_policy, sale_snapshot
from accounting_recognition_evidence import EvidenceError, qualify

OPERATION = "MZ2-FIN-CUTOVER-001"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def managed_owner(db, owner):
    settings = getattr(db, "settings", None)
    if settings is None:
        return False
    row = await settings.find_one({"user_id": owner}, {"mezan2_financial_cutover": 1})
    if ((row or {}).get("mezan2_financial_cutover") or {}).get("operation_id") == OPERATION:
        return True
    policies = getattr(db, "mz2_sales_tax_policies", None)
    return policies is not None and bool(await policies.find_one({"_id": owner}, {"_id": 1}))


async def cutoff_for(db, owner):
    settings = await db.settings.find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    if cutover.get("operation_id") == OPERATION and cutover.get("cutover_at"):
        return cutover["cutover_at"]
    raise EvidenceError("recognition_cutoff_not_configured")


async def source_documents(db, owner, provider, payment_id, refund_id=None, incoming=None):
    payment = await db.payment_transactions.find_one({
        "user_id": owner, "provider": provider, "provider_id": payment_id,
    })
    if not payment:
        raise EvidenceError("payment_evidence_missing")
    if incoming and not refund_id:
        # Ingress callers may hold a newly generated local document id after
        # an upsert. Compare economic facts, not that noncanonical local id.
        for key in ("provider_id", "currency", "amount", "order_reference_id"):
            if key in incoming and str(incoming[key]) != str(payment.get(key)):
                if key != "amount" or Decimal(str(incoming[key])) != Decimal(str(payment.get(key))):
                    raise EvidenceError("incoming_payment_conflict")
    reference = str(payment.get("order_reference_id") or payment.get("order_number") or "")
    orders = await db.orders_db.find({"user_id": owner, "order_number": reference}).to_list(2)
    if len(orders) != 1:
        raise EvidenceError("unique_source_order_required")
    refund = None
    if refund_id:
        refund = await db.payment_refunds.find_one({
            "user_id": owner, "provider": provider, "provider_refund_id": refund_id,
        })
        if not refund:
            raise EvidenceError("refund_evidence_missing")
        if incoming:
            for key in ("provider_payment_id", "currency", "amount"):
                if key in incoming and str(incoming[key]) != str(refund.get(key)):
                    if key != "amount" or Decimal(str(incoming[key])) != Decimal(str(refund.get(key))):
                        raise EvidenceError("incoming_refund_conflict")
    return orders[0], payment, refund


async def prepare(db, *, owner, provider, payment_id, refund_id=None, incoming=None):
    order, payment, refund = await source_documents(db, owner, provider, payment_id, refund_id, incoming)
    event = qualify(owner, provider, order, payment, cutoff=await cutoff_for(db, owner), refund=refund)
    economic = {k: event[k] for k in (
        "kind", "provider", "provider_payment_id", "canonical_event_id",
        "order_number", "amount", "sale_amount", "currency", "recognized_at",
    )}
    event_key = digest([owner, event["idempotency_key"]])
    prior = await db.mz2_recognition_events.find_one({"_id": event_key, "user_id": owner})
    if prior:
        if prior["economic_hash"] != digest(economic):
            raise EvidenceError("previous_recognition_source_conflict")
        if prior["status"] != "posted":
            raise EvidenceError("previous_post_result_requires_recovery")
        return {**prior["proposal"], "state": "already_posted",
                "txn_group_id": prior["txn_group_id"]}
    if refund is not None:
        raise EvidenceError("refund_requires_daily_movement_approval")
    # Detect both the canonical bridge id and other recorded revenue/order
    # paths. Never silently reclassify an existing gross or tax journal.
    alternatives = [{"metadata.idempotency_key": event["idempotency_key"]}]
    if refund is None:
        alternatives += [
            {"metadata.order_reference_id": event["order_number"], "entry_type": {"$ne": "bnpl_refund"}},
            {"metadata.order_number": event["order_number"], "entry_type": {"$ne": "bnpl_refund"}},
        ]
        if order.get("id") or order.get("order_id"):
            alternatives.append({"metadata.source_order_id": str(order.get("id") or order["order_id"])})
    existing = await db.general_ledger.find_one({
        "user_id": owner, "status": {"$in": ["posted", "reversed"]},
        "$or": alternatives,
    })
    if existing or (refund is None and any(order.get(k) for k in (
        "pre_cutover_qoyod_invoice_id", "sales_journal_id", "revenue_txn_group_id", "tax_journal_id",
    ))):
        raise EvidenceError("existing_journal_requires_review")
    if refund is None:
        tax = sale_snapshot(await read_policy(db, owner), event, order)
        original_key = None
    else:
        original_key = digest([owner, f"bnpl_sale:{provider}:{payment_id}"])
        original = await db.mz2_recognition_events.find_one({
            "_id": original_key, "user_id": owner, "status": "posted",
        })
        if not original:
            raise EvidenceError("original_recognition_required")
        if original["proposal"]["event"]["sale_amount"] != event["sale_amount"]:
            raise EvidenceError("original_sale_amount_conflict")
        refunds = await db.mz2_recognition_events.find({
            "user_id": owner, "original_key": original_key,
        }).to_list(10001)
        if len(refunds) > 10000 or any(r["status"] != "posted" for r in refunds):
            raise EvidenceError("refund_history_requires_recovery")
        tax = refund_split(original["proposal"]["tax"], [r["proposal"]["tax"] for r in refunds], event["amount"])
    proposal = {"state": "eligible", "event_key": event_key, "event": event,
                "economic_hash": digest(economic), "tax": tax, "original_key": original_key}
    proposal["preview_hash"] = digest(proposal)
    return proposal


async def execute(db, *, owner, actor_id, actor_name, provider, payment_id,
                  refund_id=None, preview_hash=None, incoming=None):
    # Resolve and reject invalid evidence before acquiring a write lock.
    proposal = await prepare(db, owner=owner, provider=provider, payment_id=payment_id,
                             refund_id=refund_id, incoming=incoming)
    if proposal["state"] == "already_posted":
        return proposal
    if preview_hash is not None and proposal["preview_hash"] != preview_hash:
        raise EvidenceError("preview_changed_review_again")
    async def commit_recognition(scoped):
        return await _execute_transaction(scoped, owner=owner, actor_id=actor_id,
            actor_name=actor_name, provider=provider, payment_id=payment_id,
            refund_id=refund_id, proposal=proposal, incoming=incoming)
    return await atomic_owner(db, owner, commit_recognition)


async def _execute_transaction(db, *, owner, actor_id, actor_name, provider,
                               payment_id, refund_id, proposal, incoming):
    latest = await prepare(db, owner=owner, provider=provider, payment_id=payment_id,
                           refund_id=refund_id, incoming=incoming)
    if latest["state"] == "already_posted":
        return latest
    if latest["preview_hash"] != proposal["preview_hash"]:
        raise EvidenceError("preview_changed_review_again")
    event, tax = latest["event"], latest["tax"]
    kind = event["kind"]
    is_sale = kind == "sale"
    entries = [{
        "entity_type": "payment_gateway", "entity_id": provider, "sub_account": "receivable",
        "side": "debit" if is_sale else "credit", "amount": tax["gross"],
        "entry_type": f"bnpl_{kind}",
    }]
    for entity_type, entity_id, amount in (
        ("revenue", "bnpl_sales", tax["net"]), ("tax", "sales_vat_payable", tax["tax"]),
    ):
        if Decimal(amount) > 0:
            entries.append({"entity_type": entity_type, "entity_id": entity_id,
                            "side": "credit" if is_sale else "debit",
                            "amount": amount, "entry_type": f"bnpl_{kind}"})
    record = {"_id": latest["event_key"], "user_id": owner, "status": "posting",
              "economic_hash": latest["economic_hash"], "proposal": latest,
              "original_key": latest["original_key"], "actor_id": actor_id}
    await db.mz2_recognition_events.insert_one(record)
    try:
        from ledger_core import post_txn_group
        result = await post_txn_group(
            db, user_id=owner, actor_id=actor_id, actor_name=actor_name,
            entries=entries, txn_type=f"bnpl_{kind}",
            notes=f"ميزان 2 — إثبات {kind} {provider} {event['order_number']}",
            metadata={"operation_id": OPERATION, "idempotency_key": event["idempotency_key"],
                      "provider": provider, "provider_id": payment_id,
                      "order_reference_id": event["order_number"],
                      "recognition_event_key": latest["event_key"],
                      "recognized_at": event["recognized_at"],
                      "recognition_source": event["source"], "sales_tax": tax,
                      "original_recognition_key": latest["original_key"]},
        )
        await db.mz2_recognition_events.update_one(
            {"_id": record["_id"], "user_id": owner, "status": "posting"},
            {"$set": {"status": "posted", "txn_group_id": result["txn_group_id"]}})
    except Exception:
        # The enclosing Mongo transaction aborts every leg, event and audit.
        # A process crash is recovered by Mongo; retry uses canonical identity.
        raise
    return {**latest, "state": "posted", "txn_group_id": result["txn_group_id"]}

