"""Adapt existing recognition economics to the delivered sealed V2 writer.

This module owns no ledger storage and creates no financial identities. Every
write uses the caller's owner transaction, existing permission, approved
opening, accounting period, and exact deterministic business event identity.
"""
from decimal import Decimal

from fastapi import HTTPException

from accounting_atomic import SessionDatabase
from accounting_financial_identity import require_financial_ledger_identity
from accounting_ledger_v2 import (AccountingLedgerV2Error, post_journal_v2,
    read_reporting_entries_v2, read_verified_journal_metadata_v2)
from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_mz2_balances import read_mz2_write_balances
from accounting_periods import assert_open_journal_periods
from accounting_write_control import AccountingDatabase, fresh_actor


def _db(db):
    return db.current() if isinstance(db, AccountingDatabase) else db


async def native_rows(db, owner):
    db = _db(db)
    try:
        rows = await read_reporting_entries_v2(db, user_id=owner, effective_before="9999-12-31T00:00:00Z",
            mongo_session=getattr(db, "_session", None))
        metadata = {}
        for row in rows:
            group = row["txn_group_id"]
            if group not in metadata:
                metadata[group] = await read_verified_journal_metadata_v2(db, user_id=owner,
                    txn_group_id=group, mongo_session=getattr(db, "_session", None))
            row["metadata"] = {**metadata[group], **(row.get("metadata") or {})}
            row["idempotency_key"] = metadata[group].get("business_event_key")
        return rows
    except AccountingLedgerV2Error as exc:
        raise HTTPException(409, {"code": exc.code}) from exc


async def verify_event_journal(db, owner, group, **expected):
    try:
        metadata = await read_verified_journal_metadata_v2(db, user_id=owner,
            txn_group_id=group, mongo_session=getattr(_db(db), "_session", None), require_unreversed=True)
    except AccountingLedgerV2Error as exc:
        raise HTTPException(409, {"code": exc.code}) from exc
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise HTTPException(409, "native_event_journal_conflict")
    return metadata


def _exact(value):
    # Existing settlement previews use already-rounded decimal money as floats.
    # The native contract records decimal strings, never binary floats.
    if isinstance(value, float):
        return str(Decimal(str(value)))
    if isinstance(value, dict):
        return {k: _exact(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_exact(v) for v in value]
    return value


async def post_recognition_journal(db, *, user_id, actor_id, actor_name, entries,
        txn_type, idempotency_key, effective_at, permission, notes="", metadata=None,
        source="mz2_existing_financial_cutover", reason_code=None):
    db = _db(db)
    if not isinstance(db, SessionDatabase) or db._owner != user_id or not db._session.in_transaction:
        raise HTTPException(409, "native_post_requires_owner_transaction")
    actor = await fresh_actor(db, {"id": actor_id})
    if accounting_owner_id(actor) != user_id:
        raise HTTPException(403, "accounting_owner_scope_mismatch")
    require_accounting_permission(actor, permission)
    required = []
    for leg in entries:
        if leg["entity_type"] == "bank":
            await require_financial_ledger_identity(db, user_id, leg["entity_id"],
                account_types=("bank",), currency="SAR")
        if leg["entity_type"] in {"bank", "payment_gateway"}:
            required.append((leg["entity_type"], leg["entity_id"], leg.get("sub_account") or ""))
    await read_mz2_write_balances(db, owner=user_id, required_accounts=required)
    await assert_open_journal_periods(db, user_id, [{"metadata": {"accounting_at": effective_at}}])
    meta = _exact(dict(metadata or {}))
    # These values live in the sealed group envelope, never caller metadata.
    meta.pop("operation_id", None)
    meta.pop("idempotency_key", None)
    meta["business_event_key"] = idempotency_key
    if reason_code:
        meta["reason_code"] = reason_code
    try:
        journal = await post_journal_v2(db._db, user_id=user_id, actor_id=actor_id,
            actor_name=actor_name or actor_id, idempotency_key=idempotency_key,
            txn_type=txn_type, source=source, effective_at=effective_at,
            entries=[{**_exact(leg), "leg_key": f"{txn_type}:{i}",
                      "amount": format(Decimal(str(leg["amount"])), ".2f")}
                     for i, leg in enumerate(entries)], notes=notes, metadata=meta, mongo_session=db._session)
    except AccountingLedgerV2Error as exc:
        raise HTTPException(409, {"code": exc.code}) from exc
    group = journal["group"]
    return {"txn_group_id": group["txn_group_id"], "entries": journal["entries"],
            "debit_total": group["debit_total_minor"] / 100,
            "credit_total": group["credit_total_minor"] / 100}
