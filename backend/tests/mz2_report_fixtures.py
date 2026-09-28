"""Explicit synthetic cutover evidence; never inferred Production readiness."""
from uuid import uuid4
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS

async def provision_report_opening(db, *, owner="owner", bank="bank", amount=1000,
                                   cutover="2020-01-01T00:00:00Z", existing_group_id=None):
    if bank is not None:
        await db.accounts.update_one({"user_id": owner, "id": bank},
            {"$setOnInsert": {"account_type": "bank", "current_balance": 0}}, upsert=True)
    group = existing_group_id or "SYN-OPEN-" + uuid4().hex
    metadata = {"operation_id": OPERATION_ID, "accounting_at": cutover}
    if existing_group_id:
        await db.general_ledger.update_many({"user_id": owner, "txn_group_id": group},
            {"$set": {"entry_type": "opening_balance", "metadata": metadata}})
    else:
        await db.general_ledger.insert_many([
            {"id": uuid4().hex, "user_id": owner, "txn_group_id": group, "status": "posted",
             "entry_type": "opening_balance", "entity_type": entity, "entity_id": identifier,
             "sub_account": sub, "side": side, "amount": amount,
             "metadata": metadata, "created_at": "2026-09-20T12:00:00Z", "posted_at": "2026-09-20T12:00:00Z"}
            for entity, identifier, sub, side in ((("bank", bank, "main", "debit") if bank is not None
                else ("asset", "SYN-OPENING-FIXTURE", "fixture_asset", "debit")), ("equity", "SYN", "capital", "credit"))])
    state = {"operation_id": OPERATION_ID, "status": "active", "cutover_at": cutover,
        "evidence_sheet_ref": "SYN-SIGNED-CUTOVER",
        "evidence_sections": {row["id"]: {"ref": "SYN-"+row["id"]} for row in EVIDENCE_SECTIONS},
        "opening_balance_preview_id": "SYN-PREVIEW", "opening_balance_preview_balanced": True,
        "opening_balance_approved_at": cutover, "opening_balance_approved_by": owner,
        "opening_balance_txn_group_id": group,
        "opening_balance_zero_accounts": [{"entity_type": "payment_gateway", "entity_id": "tamara",
            "sub_account": "receivable", "evidence_ref": "SYN-ZERO-TAMARA", "accounting_at": cutover,
            "opening_balance_txn_group_id": group}]}
    await db.settings.update_one({"user_id": owner}, {"$set": {"mezan2_financial_cutover": state}}, upsert=True)
    return group


async def provision_write_opening(db, *, existing_group_id=None, bank_zero_ids=(),
                                  providers=("salla", "tamara", "tabby", "emkan")):
    """Opt-in test authorization; never silently invoked by WorkflowTests.

    Reuse a test's existing balanced opening if supplied. Otherwise one unit of
    a separate fixture asset/equity creates the required nonzero opening batch;
    it does not provide a spendable bank/provider balance. Exact zero evidence
    applies only to the synthetic account IDs explicitly supplied by the test.
    """
    cutover = "2020-01-01T00:00:00Z"
    group = await provision_report_opening(db, bank=None, amount=1,
        cutover=cutover, existing_group_id=existing_group_id)
    zeroes = [dict(entity_type=entity, entity_id=identifier, sub_account=sub,
        evidence_ref="SYN-APPROVED-ZERO-" + identifier, accounting_at=cutover,
        opening_balance_txn_group_id=group)
        for entity, identifier, sub in [("bank", b, "main") for b in bank_zero_ids]
        + [("payment_gateway", p, "receivable") for p in providers]]
    await db.settings.update_one({"user_id": "owner"}, {"$set": {
        "mezan2_financial_cutover.opening_balance_zero_accounts": zeroes}})
    return group
