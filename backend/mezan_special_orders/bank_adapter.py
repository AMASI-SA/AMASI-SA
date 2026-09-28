"""Verified, nonduplicating bank legs for explicit daily financial commands."""
from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from .contracts import FxSnapshot
from .domain import DomainError
from .evidence import EvidenceStore
from .ledger_adapter import BANK_BINDINGS, OPERATION_ID, entry_facts, major, minor


def normalized_reference(value):
    return " ".join(value.split()).casefold()


async def verify_fx(db, tenant_id, snapshot: FxSnapshot):
    from .ledger_adapter import FX_SNAPSHOTS
    if snapshot.currency == "SAR":
        if snapshot.rate_to_sar != "1" or snapshot.evidence_id != "sar-fixed-1":
            raise DomainError("sar_identity_snapshot_required", 422)
        return True
    row = await db[FX_SNAPSHOTS].find_one({
        "tenant_id": str(tenant_id), "evidence_id": snapshot.evidence_id,
    }, {"_id": 0})
    if not row or row.get("snapshot") != snapshot.model_dump(mode="json"):
        raise DomainError("fx_snapshot_not_verified", 422)
    evidence = row.get("evidence") or {}
    from .contracts import Evidence
    await EvidenceStore(db).verify(tenant_id, Evidence.model_validate(evidence))
    return True


async def bank_movement(db, document, movement, *, amount_minor, direction, event_id, expected_entries):
    """Return (ledger metadata, existing verified group facts or None).

    Reuse is accepted only when the already posted MZ2 group has exactly the
    requested bank/receivable/FX legs. Unknown prior postings block reconciliation
    rather than touching the bank a second time. New movement mode is explicit.
    """
    tenant_id = document["tenant_id"]
    if movement.cash_fx.currency != document["fx"]["currency"]:
        raise DomainError("bank_fx_currency_mismatch")
    await verify_fx(db, tenant_id, movement.cash_fx)
    actual_sar = movement.cash_fx.to_sar_minor(amount_minor)
    if actual_sar <= 0:
        raise DomainError("bank_movement_rounds_to_zero")
    account = await db.accounts.find_one({
        "user_id": tenant_id, "id": movement.bank_account_id,
        "account_type": {"$in": ["bank", "cash"]}, "status": {"$nin": ["hidden", "inactive"]},
    }, {"_id": 0})
    if not account or account.get("currency", "SAR") != "SAR":
        raise DomainError("verified_sar_bank_or_cash_account_required")
    await EvidenceStore(db).verify(tenant_id, movement.evidence)
    reference_key = normalized_reference(movement.bank_reference)
    previous = await db[BANK_BINDINGS].find_one({"tenant_id": tenant_id,
        "account_id": movement.bank_account_id, "reference_key": reference_key}, {"_id": 0})
    if previous:
        raise DomainError("bank_movement_already_allocated")
    existing_facts = None
    if movement.existing_transaction_id:
        tx = await db.account_transactions.find_one({
            "user_id": tenant_id, "id": movement.existing_transaction_id,
            "account_id": movement.bank_account_id,
        }, {"_id": 0})
        if not tx or tx.get("direction") != direction or minor(tx.get("amount")) != actual_sar:
            raise DomainError("bank_transaction_does_not_match")
        if tx.get("currency", "SAR") != "SAR" or tx.get("status") not in {"posted", "reconciled"}:
            raise DomainError("bank_transaction_not_posted")
        references = [tx.get(k) for k in ("reference", "bank_reference", "reference_number") if tx.get(k)]
        if not any(normalized_reference(str(v)) == reference_key for v in references):
            raise DomainError("bank_transaction_reference_mismatch")
        bank_rows = await db.general_ledger.find({"user_id": tenant_id,
            "metadata.account_transaction_id": tx["id"], "entity_type": "bank",
            "entity_id": movement.bank_account_id, "status": "posted",
        }, {"_id": 0}).to_list(2)
        if len(bank_rows) != 1:
            raise DomainError("existing_bank_movement_requires_ledger_reconciliation")
        rows = await db.general_ledger.find({"user_id": tenant_id,
            "txn_group_id": bank_rows[0]["txn_group_id"]}, {"_id": 0}).to_list(16)
        if any(r.get("status") != "posted" or (r.get("metadata") or {}).get("operation_id") != OPERATION_ID for r in rows):
            raise DomainError("existing_bank_movement_not_mz2_posted")
        fields = ("entity_type", "entity_id", "side", "sub_account", "amount_minor")
        key = lambda row: (row["entity_type"], row["entity_id"], row["side"], row["sub_account"] or "", row["amount_minor"])
        actual = [{k: entry_facts(r).get(k) for k in fields} for r in rows]
        expected = [{k: r.get(k) for k in fields} for r in expected_entries]
        if sorted(actual, key=key) != sorted(expected, key=key):
            raise DomainError("existing_bank_movement_counterparty_reconciliation_required")
        existing_facts = [entry_facts(r) for r in rows]
        transaction_id = tx["id"]
    else:
        # Check references before creating another transaction. The private binding
        # index arbitrates concurrent requests handled by this integration.
        existing = await db.account_transactions.find_one({"user_id": tenant_id,
            "account_id": movement.bank_account_id, "$or": [
                {"reference": movement.bank_reference}, {"bank_reference": movement.bank_reference},
                {"reference_number": movement.bank_reference}, {"metadata.special_reference_key": reference_key},
            ]}, {"_id": 0, "id": 1})
        if existing:
            raise DomainError("bank_reference_exists_select_existing_movement")
        transaction_id = str(uuid5(NAMESPACE_URL, f"special-bank:{tenant_id}:{movement.bank_account_id}:{reference_key}"))
        await db.account_transactions.insert_one({
            "_id": transaction_id, "id": transaction_id, "user_id": tenant_id,
            "account_id": movement.bank_account_id, "amount": major(actual_sar), "currency": "SAR",
            "direction": direction, "transaction_type": "settlement", "status": "posted",
            "reference": movement.bank_reference, "transaction_date": movement.occurred_at.date().isoformat(),
            "created_at": movement.occurred_at.isoformat(), "description": "حركة طلب ميزان " + document["order_number"],
            "metadata": {"operation_id": OPERATION_ID, "source": "mezan_special_orders_v1",
                "special_order_id": document["order_id"], "special_event_id": event_id,
                "special_reference_key": reference_key, "evidence_id": movement.evidence.object_id},
        })
    await db[BANK_BINDINGS].insert_one({
        "tenant_id": tenant_id, "account_id": movement.bank_account_id,
        "reference_key": reference_key, "transaction_id": transaction_id,
        "order_id": document["order_id"], "event_id": event_id,
    })
    return {"account_transaction_id": transaction_id, "bank_reference": movement.bank_reference,
        "original_currency": document["fx"]["currency"], "original_amount_minor": amount_minor,
        "cash_fx": movement.cash_fx.model_dump(mode="json"), "bank_evidence_id": movement.evidence.object_id}, existing_facts
