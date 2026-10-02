"""Native journal boundary for payroll and classified daily expenses."""
from decimal import Decimal
from fastapi import HTTPException
from accounting_atomic import SessionDatabase
from accounting_ledger_v2 import AccountingLedgerV2Error, post_journal_v2, read_verified_journal_metadata_v2
from accounting_write_control import AccountingDatabase, fresh_actor
from accounting_periods import assert_open_journal_periods
from accounting_module_contract import accounting_owner_id, require_accounting_permission


async def verify_native_event(db, owner, event):
    """A retained domain row never substitutes for a sealed native journal."""
    if isinstance(db, AccountingDatabase):
        db = db.current()
    try:
        metadata = await read_verified_journal_metadata_v2(db._db, user_id=owner,
            txn_group_id=event.get("txn_group_id"), mongo_session=db._session, require_unreversed=True)
    except AccountingLedgerV2Error as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    event_id = event.get("id") or event.get("_id")
    if event.get("kind") == "supplier_payment":
        from accounting_supplier_payments_v2 import key, PAYMENT_CONTRACT
        matches = (metadata.get("payment_contract") == PAYMENT_CONTRACT
                   and metadata.get("payment_id") == key(owner, event_id)
                   and metadata.get("supplier_id") == event.get("classification_ref"))
    else:
        matches = (metadata.get("payroll_event_id") or metadata.get("outgoing_event_id")) == event_id
    if not event_id or not matches:
        raise HTTPException(409, "native_employee_outgoing_event_binding_invalid")


async def post_operational_journal(db, *, user_id, actor_id, actor_name,
                                   txn_type, notes, metadata, entries):
    if isinstance(db, AccountingDatabase):
        db = db.current()
    if not isinstance(db, SessionDatabase) or db._owner != user_id or not db._session.in_transaction:
        raise HTTPException(409, "native_employee_outgoing_requires_owner_transaction")
    actor = await fresh_actor(db, {"id": actor_id})
    if accounting_owner_id(actor) != user_id:
        raise HTTPException(403, "accounting_owner_scope_mismatch")
    require_accounting_permission(actor, "accounting.payroll.post" if txn_type.startswith("mz2_employee_")
        or txn_type == "mz2_salary_accrual" else "accounting.journals.manual_create")
    event_id = metadata.get("payroll_event_id") or metadata.get("employee_event_id") or metadata.get("outgoing_event_id")
    if not event_id:
        raise HTTPException(409, "native_employee_outgoing_event_required")
    await assert_open_journal_periods(db, user_id, [{"metadata": metadata}])
    legs = [{**row, "leg_key": str(index), "sub_account": row.get("sub_account"),
             "amount": format(Decimal(str(row["amount"])), ".2f")}
            for index, row in enumerate(entries)]
    try:
        result = await post_journal_v2(db._db, user_id=user_id, actor_id=actor_id,
            actor_name=actor_name, idempotency_key=event_id, txn_type=txn_type,
            source=metadata["source"], effective_at=metadata["accounting_at"],
            notes=notes, metadata={k: v for k, v in metadata.items() if k != "operation_id"}, entries=legs, mongo_session=db._session)
    except AccountingLedgerV2Error as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    return {"txn_group_id": result["group"]["txn_group_id"]}
