"""Synthetic sealed native opening for real Mongo cutover acceptance tests."""
from decimal import Decimal

from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_opening_journal_v2, verify_active_opening_v2
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS


async def native_balance(db, *, user_id, entity_type, entity_id, sub_account=None):
    from accounting_recognition_native import native_rows
    rows = [r for r in await native_rows(db, user_id)
            if r["entity_type"] == entity_type and r["entity_id"] == entity_id
            and (sub_account is None or r.get("sub_account") == sub_account)]
    return {"net_balance": float(sum((Decimal(r["amount"]) * (1 if r["side"] == "debit" else -1)
                                     for r in rows), Decimal(0)))}


async def provision_native_opening(db, *, owner="owner", cutover="2020-01-01T00:00:00Z",
                                   bank_balances=None, zero_accounts=(), entries=None,
                                   providers=("salla", "tamara", "tabby", "emkan")):
    """Only explicitly supplied balances; never read or import Legacy history.

    A one-riyal synthetic asset/equity opening is used when no funded legs are
    supplied. It cannot fund a bank, supplier, employee or provider payment.
    Existing owner pause state is preserved. Reuse never modifies an opening.
    """
    current = await db.settings.find_one({"user_id": owner}) or {}
    state = current.get("mezan2_financial_cutover") or {}
    if state.get("opening_active_txn_group_id"):
        assert await verify_active_opening_v2(db, user_id=owner, cutover=state)
        return state["opening_active_txn_group_id"]
    await ensure_accounting_ledger_v2_indexes(db)
    await db.mz2_atomic_owners.update_one({"_id": owner}, {"$set": {
        "ledger_backend_state": "v2_active", "ledger_backend_revision": 2,
        "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "SYN-TEST-ONLY",
        "ledger_backend_updated_by": owner}, "$setOnInsert": {
        "revision": 0, "writes_paused": False, "control_revision": 0}}, upsert=True)
    legs = list(entries or [])
    zeros = list(zero_accounts) + [("payment_gateway", p, "receivable") for p in providers]
    total = Decimal(0)
    for bank, value in (bank_balances or {}).items():
        value = Decimal(str(value))
        await db.mz2_financial_accounts.update_one({"user_id": owner, "id": bank}, {"$setOnInsert": {
            "name": "Synthetic " + bank, "account_type": "bank", "status": "active", "currency": "SAR"}}, upsert=True)
        if value:
            legs.append(dict(entity_type="bank", entity_id=bank, sub_account="main",
                side="debit" if value > 0 else "credit", amount=format(abs(value), ".2f")))
            total += value
        else:
            zeros.append(("bank", bank, "main"))
    if bank_balances and total:
        legs.append(dict(entity_type="equity", entity_id="opening_balance_equity", sub_account="main",
            side="credit" if total > 0 else "debit", amount=format(abs(total), ".2f")))
    if not legs:
        legs = [dict(entity_type=t, entity_id=i, sub_account=s, side=side, amount="1.00")
                for t, i, s, side in [("asset", "SYN-OPENING-FIXTURE", "other_receivable", "debit"),
                                      ("equity", "opening_balance_equity", "main", "credit")]]
    legs = [{**row, "leg_key": row.get("leg_key", f"opening:{n}"), "entry_type": "opening_balance"}
            for n, row in enumerate(legs)]
    covered = {(r["entity_type"], r["entity_id"], r.get("sub_account") or "") for r in legs}
    async with await db.client.start_session() as session:
        async def write(active):
            return await post_opening_journal_v2(db, user_id=owner, actor_id=owner, actor_name=owner,
                opening_operation_id="synthetic-native-opening", approved_preview_hash="a" * 64,
                effective_at=cutover, entries=legs, mongo_session=active)
        journal = await session.with_transaction(write)
    group = journal["group"]["txn_group_id"]
    state = dict(operation_id=OPERATION_ID, status="active", cutover_at=cutover,
        ledger_source="accounting_v2_operation_scoped", opening_active_txn_group_id=group,
        opening_root_txn_group_id=group, opening_balance_txn_group_id=group,
        evidence_sheet_ref="SYN-SIGNED-CUTOVER", evidence_sections={s["id"]: "synthetic" for s in EVIDENCE_SECTIONS},
        opening_balance_preview_id="SYN-PREVIEW", opening_balance_preview_balanced=True,
        opening_balance_approved_at=cutover, opening_balance_approved_by=owner,
        opening_balance_zero_accounts=[dict(entity_type=t, entity_id=i, sub_account=s,
            evidence_ref="SYN-APPROVED-ZERO-"+i, accounting_at=cutover, opening_balance_txn_group_id=group)
            for t, i, s in sorted(set(zeros) - covered)])
    await db.settings.update_one({"user_id": owner}, {"$set": {"mezan2_financial_cutover": state}}, upsert=True)
    assert await verify_active_opening_v2(db, user_id=owner, cutover=state)
    return group
