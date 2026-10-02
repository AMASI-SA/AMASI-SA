"""Sealed native journals for existing customer advance/receipt workflows."""
from decimal import Decimal
from hashlib import sha256
from fastapi import HTTPException
from accounting_atomic import SessionDatabase
from accounting_ledger_v2 import post_journal_v2, read_verified_journal_metadata_v2, AccountingLedgerV2Error
from accounting_mz2_balances import read_mz2_write_balances
from accounting_periods import assert_open_journal_periods
from accounting_financial_identity import find_financial_account

async def verified_customer_journal(db, owner, group_id, binding):
    scoped = isinstance(db, SessionDatabase)
    try:
        metadata = await read_verified_journal_metadata_v2(db._db if scoped else db,
            user_id=owner, txn_group_id=group_id, require_unreversed=True, mongo_session=db._session if scoped else None)
    except AccountingLedgerV2Error as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    if any(metadata.get(key) != value for key, value in binding.items()):
        raise HTTPException(409, "customer_native_journal_binding_conflict")
    return metadata

async def post_customer_journal(db, *, user_id, actor_id, actor_name, txn_type, notes, metadata, entries):
    if not isinstance(db, SessionDatabase):
        raise HTTPException(409, "customer_native_owner_transaction_required")
    at = metadata["accounting_at"]
    required = [(e["entity_type"], e["entity_id"], e.get("sub_account"))
        for e in entries if e["entity_type"] in {"bank", "payment_gateway"}]
    await read_mz2_write_balances(db, owner=user_id, required_accounts=required)
    await assert_open_journal_periods(db, user_id, [{"metadata": {"accounting_at": at}}])
    for entity, identity, _ in required:
        if entity == "bank" and not await find_financial_account(db, user_id, identity,
                account_types=("bank",), currency="SAR"):
            raise HTTPException(409, "owned_bank_required")
    facts = {key: value for key, value in metadata.items() if key != "operation_id"}
    identity = facts.get("bank_transfer_review_id") or facts.get("customer_advance_id")
    key = sha256(repr((user_id, txn_type, identity, facts.get("evidence_ref"), at)).encode()).hexdigest()
    legs = [{**e, "leg_key": f"{txn_type}:{index}", "amount": format(Decimal(str(e["amount"])), ".2f")}
        for index, e in enumerate(entries)]
    try:
        result = await post_journal_v2(db._db, user_id=user_id, actor_id=actor_id, actor_name=actor_name,
            idempotency_key="customer:"+key, txn_type=txn_type,
            source="accounting_customer_native", effective_at=at, entries=legs,
            notes=notes, metadata=facts, mongo_session=db._session)
    except AccountingLedgerV2Error as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    return {"txn_group_id": result["group"]["txn_group_id"], "entries": result["entries"]}
