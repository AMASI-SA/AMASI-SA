"""Atomic use of the real general_ledger owner with exact read-back verification.

The event collection is a replay/proof index. Monetary balances remain in the
existing MZ2 general ledger; no parallel journal or mutable balance store exists.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5

from .binding import require_bound
from .contracts import CostProof, LedgerProof
from .domain import DomainError, digest

OPERATION_ID = "MZ2-FIN-CUTOVER-001"
EVENTS = "mezan_special_order_financial_events_v1"
BANK_BINDINGS = "mezan_special_order_bank_bindings_v1"
POLICIES = "mezan_special_order_accounting_policies_v1"
FX_SNAPSHOTS = "mezan_special_fx_snapshots_v1"


def minor(value):
    if isinstance(value, bool) or value is None:
        raise DomainError("ledger_money_invalid")
    try:
        amount = value.to_decimal() if hasattr(value, "to_decimal") else Decimal(str(value))
        units = amount * 100
        if not units.is_finite() or units < 0 or units != units.to_integral_value():
            raise ValueError()
        return int(units)
    except (ArithmeticError, ValueError, TypeError):
        raise DomainError("ledger_money_invalid") from None


def major(value):
    if type(value) is not int or value < 0 or value > 10**14:
        raise DomainError("ledger_minor_amount_invalid")
    return float(Decimal(value) / 100)


def leg(entity_type, entity_id, side, amount, sub_account=None):
    if type(amount) is not int or amount <= 0:
        raise DomainError("positive_ledger_leg_required")
    return {"entity_type": entity_type, "entity_id": str(entity_id), "side": side,
            "amount_minor": amount, "sub_account": sub_account}


def counterparty_key(purpose_bucket):
    return "special_orders:" + purpose_bucket


def recovery_legs(policy, bucket, gross, *, reverse=False):
    rate = int(policy["tax_basis_points"])
    tax = int((Decimal(gross) * rate / (10000 + rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    net = gross - tax
    direction = "debit" if reverse else "credit"
    out = []
    if net:
        entity_type = "expense" if policy["classification"] == "expense_recovery" else "other_income"
        out.append(leg(entity_type, counterparty_key(bucket), direction, net))
    if tax:
        out.append(leg("tax", "output_vat", direction, tax, "payable"))
    return out


def balance_fx(entries):
    debit = sum(r["amount_minor"] for r in entries if r["side"] == "debit")
    credit = sum(r["amount_minor"] for r in entries if r["side"] == "credit")
    if debit > credit:
        entries.append(leg("other_income", "special_orders:fx_gain", "credit", debit - credit))
    elif credit > debit:
        entries.append(leg("expense", "special_orders:fx_loss", "debit", credit - debit))
    return entries


def entry_facts(row):
    return {"id": row["id"], "txn_group_id": row["txn_group_id"],
        "entity_type": row["entity_type"], "entity_id": row["entity_id"],
        "sub_account": row.get("sub_account"), "side": row["side"],
        "amount_minor": minor(row["amount"]), "currency": row["currency"],
        "entry_type": row["entry_type"], "posted_by": row.get("posted_by"),
        "posted_at": str(row.get("posted_at") or row.get("created_at") or ""),
        "metadata_digest": digest(row.get("metadata") or {})}


async def ensure_indexes(db):
    from ledger_core import ensure_indexes as ensure_ledger_indexes
    await ensure_ledger_indexes(db)
    await db[EVENTS].create_index([("tenant_id", 1), ("movement_id", 1)], unique=True)
    await db[EVENTS].create_index([("tenant_id", 1), ("order_id", 1)])
    await db[BANK_BINDINGS].create_index([("tenant_id", 1), ("account_id", 1), ("reference_key", 1)], unique=True)
    await db[BANK_BINDINGS].create_index([("tenant_id", 1), ("transaction_id", 1)], unique=True)
    await db[POLICIES].create_index([("tenant_id", 1), ("effective_at", 1)], unique=True)
    await db[FX_SNAPSHOTS].create_index([("tenant_id", 1), ("evidence_id", 1)], unique=True)


async def require_cutover(db, tenant_id, event_at=None):
    require_bound(db, write=True, finance=True, tenant_id=tenant_id)
    from store_delivery_accounting import financial_cutover_is_active
    if not await financial_cutover_is_active(db, user_id=str(tenant_id), event_at=event_at):
        raise DomainError("mezan2_cutover_not_active")


async def acquire_ledger_fence(db, tenant_id):
    binding = require_bound(db, write=True, finance=True, tenant_id=tenant_id)
    if binding.session is None:
        raise DomainError("real_mongo_transaction_required")
    from .binding import require_admitted
    require_admitted(db, tenant_id, {"financial"})
    # This write serializes new special-order journals for a merchant and is
    # rolled back with every other effect. Existing ledger entry_no uniqueness
    # remains the guard against concurrent journals from other modules.
    await db["mezan_special_order_ledger_fences_v1"].update_one(
        {"_id": str(tenant_id)}, {"$inc": {"sequence": 1}}, upsert=True,
    )


async def post_group(db, tenant_id, actor_id, *, order_id, event_id, entries, metadata=None):
    binding = require_bound(db, write=True, finance=True, tenant_id=tenant_id)
    if binding.session is None:
        raise DomainError("real_mongo_transaction_required")
    from .binding import require_admitted
    require_admitted(db, tenant_id, {"financial"})
    if not entries:
        return []  # A verified zero-cost fact needs no zero-value journal.
    if sum(r["amount_minor"] * (1 if r["side"] == "debit" else -1) for r in entries) != 0:
        raise DomainError("journal_not_exactly_balanced")
    from ledger_core import post_txn_group
    meta = {"operation_id": OPERATION_ID, "source": "mezan_special_orders_v1",
        "special_order_id": order_id, "special_event_id": event_id,
        "idempotency_key": event_id, **(metadata or {})}
    expected = [{k: row.get(k) for k in ("entity_type", "entity_id", "side", "sub_account", "amount_minor")} for row in entries]
    result = await post_txn_group(db, user_id=str(tenant_id), actor_id=str(actor_id), actor_name=str(actor_id),
        txn_type="special_order_event", reason_code="actual_payment",
        notes="حركة طلب ميزان " + order_id,
        metadata=meta, entries=[{**{k: row.get(k) for k in ("entity_type", "entity_id", "side", "sub_account")},
            "amount": major(row["amount_minor"]), "entry_type": "payment"} for row in entries])
    rows = await db.general_ledger.find({"user_id": str(tenant_id), "txn_group_id": result["txn_group_id"]}, {"_id": 0}).to_list(len(entries) + 1)
    actual = [{k: entry_facts(r).get(k) for k in ("entity_type", "entity_id", "side", "sub_account", "amount_minor")} for r in rows]
    key = lambda r: (r["entity_type"], r["entity_id"], r["side"], r["sub_account"] or "", r["amount_minor"])
    if sorted(actual, key=key) != sorted(expected, key=key) or any(r["status"] != "posted" or r["currency"] != "SAR" for r in rows):
        raise DomainError("posted_journal_readback_mismatch")
    return [entry_facts(r) for r in rows]


async def save_event(db, document, movement_id, *, operation, entries, proof=None, payload_digest=None, extra=None):
    row = {"tenant_id": document["tenant_id"], "order_id": document["order_id"],
        "order_number": document["order_number"], "movement_id": movement_id,
        "operation": operation, "entries": entries, "proof": proof.model_dump(mode="json") if proof else None,
        "payload_digest": payload_digest, "extra": extra or {}}
    row["integrity_digest"] = digest(row)
    await db[EVENTS].insert_one(deepcopy(row))
    return row


async def verify_event(db, tenant_id, order_id, movement_id):
    event = await db[EVENTS].find_one({"tenant_id": str(tenant_id), "order_id": order_id, "movement_id": movement_id}, {"_id": 0})
    if not event:
        raise DomainError("posted_special_event_not_found", 404)
    fingerprint = event.pop("integrity_digest", None)
    if digest(event) != fingerprint:
        raise DomainError("financial_event_integrity_failed")
    event["integrity_digest"] = fingerprint
    facts = event["entries"]
    if facts:
        rows = await db.general_ledger.find({"user_id": str(tenant_id), "id": {"$in": [r["id"] for r in facts]}}, {"_id": 0}).to_list(len(facts) + 1)
        if len(rows) != len(facts) or any(r.get("status") != "posted" or r.get("currency") != "SAR" for r in rows):
            raise DomainError("ledger_reconciliation_required")
        by_id = {r["id"]: entry_facts(r) for r in rows}
        if any(by_id.get(r["id"]) != r for r in facts):
            raise DomainError("ledger_reconciliation_required")
    elif not event["extra"].get("verified_zero_cost"):
        raise DomainError("financial_event_without_journal")
    native = event["extra"].get("native_assignment")
    if native:
        from .delivery_bridge import assignment_facts, native_collection_facts, native_earning_facts
        from store_delivery_handover_routes import ASSIGNMENTS
        from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_EARNINGS
        assignment = await db[ASSIGNMENTS].find_one({"user_id": str(tenant_id), "id": native["id"]}, {"_id": 0})
        collection = await db[DRIVER_COLLECTIONS].find_one({"user_id": str(tenant_id), "assignment_id": native["id"]}, {"_id": 0})
        earning = await db[DRIVER_EARNINGS].find_one({"user_id": str(tenant_id), "assignment_id": native["id"]}, {"_id": 0})
        if (not assignment or assignment_facts(assignment) != native or not collection or not earning
            or native_collection_facts(collection) != event["extra"].get("native_collection")
            or native_earning_facts(earning) != event["extra"].get("native_earning")):
            raise DomainError("native_delivery_source_reconciliation_required")
    settlement = event["extra"].get("native_settlement")
    if settlement:
        from .delivery_settlements import settlement_facts, verify_native_settlement
        from store_delivery_settlement_routes import SETTLEMENTS
        current = await db[SETTLEMENTS].find_one({"user_id": str(tenant_id), "id": settlement["id"]}, {"_id": 0})
        if not current or settlement_facts(current) != settlement:
            raise DomainError("native_settlement_source_reconciliation_required")
        await verify_native_settlement(db, current)
    source = event["extra"].get("source_invoice")
    if source:
        from .cost_sources import invoice_facts
        from supplier_invoice_integrity import INVOICES, verify_persisted_supplier_invoice
        current = await verify_persisted_supplier_invoice(db, user_id=str(tenant_id), invoice_id=source["id"],
            session_id=source["session_id"], supplier_id=source["supplier_id"])
        if invoice_facts(current) != source:
            raise DomainError("supplier_source_reconciliation_required")
    return event


async def payment_proof(db, tenant_id, order_id, movement_id):
    event = await verify_event(db, tenant_id, order_id, movement_id)
    if event["operation"] not in {"bank_collection", "cod_collection", "remittance", "refund"}:
        raise DomainError("not_a_collection_event")
    return LedgerProof.model_validate(event["proof"])


async def cost_proof(db, tenant_id, order_id, movement_id):
    event = await verify_event(db, tenant_id, order_id, movement_id)
    if event["operation"] not in {"expense", "reverse_cost", "supplier_invoice", "inventory_issue", "delivery_fee"}:
        raise DomainError("not_a_cost_event")
    return CostProof.model_validate(event["proof"])
