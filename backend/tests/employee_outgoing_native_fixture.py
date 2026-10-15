"""Synthetic native opening for payroll/daily movement regressions only."""
from decimal import Decimal
from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_opening_journal_v2
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS


async def opening(db, *, owner, bank, employee=None, supplier=None, payable="0"):
    state = ((await db.settings.find_one({"user_id": owner})) or {}).get("mezan2_financial_cutover", {})
    if state.get("opening_active_txn_group_id"):
        from accounting_ledger_v2 import verify_active_opening_v2
        assert await verify_active_opening_v2(db, user_id=owner, cutover=state)
        return
    cut = "2026-09-20T00:00:00+03:00"
    await ensure_accounting_ledger_v2_indexes(db)
    await db.mz2_financial_accounts.update_one({"user_id": owner, "id": bank}, {"$set": {
        "account_type": "bank", "currency": "SAR", "status": "active"}}, upsert=True)
    await db.mz2_atomic_owners.update_one({"_id": owner}, {"$set": {
        "writes_paused": False, "ledger_backend_state": "v2_active", "ledger_backend_revision": 1,
        "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "synthetic-only"}}, upsert=True)
    entries = []
    def leg(kind, identity, sub, side, amount):
        entries.append(dict(entity_type=kind, entity_id=identity, sub_account=sub,
            side=side, amount=format(Decimal(str(amount)), ".2f"), leg_key=str(len(entries)), entry_type="opening_balance"))
    leg("bank", bank, "main", "debit", "10000")
    zeros = []
    equity = Decimal("10000")
    if employee:
        leg("employee", employee, "salary_payable", "credit", "3000")
        leg("employee", employee, "advance", "debit", "500")
        equity -= Decimal("2500")
        zeros.append(("employee", employee, "custody"))
    if supplier:
        await db.mezan_suppliers_v2.insert_one({"user_id": owner, "id": supplier,
            "name": "Synthetic supplier", "status": "active"})
        leg("supplier", supplier, "payable", "credit", payable)
        equity -= Decimal(payable)
        zeros.append(("supplier", supplier, "advance"))
    leg("equity", "opening_balance_equity", "main", "credit", equity)
    async with await db.client.start_session() as session:
        result = await session.with_transaction(lambda active: post_opening_journal_v2(db,
            user_id=owner, actor_id=owner, actor_name=owner, opening_operation_id="synthetic-native-opening",
            approved_preview_hash="a" * 64, effective_at=cut, entries=entries, mongo_session=active))
    group = result["group"]["txn_group_id"]
    await db.settings.update_one({"user_id": owner}, {"$set": {"mezan2_financial_cutover": {
        "operation_id": OPERATION_ID, "status": "active", "cutover_at": cut,
        "opening_active_txn_group_id": group, "opening_root_txn_group_id": group,
        "opening_balance_txn_group_id": group, "opening_balance_preview_id": "synthetic",
        "opening_balance_preview_balanced": True, "opening_balance_approved_at": cut,
        "opening_balance_approved_by": owner, "evidence_sheet_ref": "synthetic",
        "evidence_sections": {s["id"]: "synthetic" for s in EVIDENCE_SECTIONS},
        "opening_balance_zero_accounts": [{"entity_type": k, "entity_id": i, "sub_account": s,
            "accounting_at": cut, "evidence_ref": "synthetic-zero", "opening_balance_txn_group_id": group}
            for k, i, s in zeros]}}}, upsert=True)


from pymongo.monitoring import CommandListener

class NoLegacyFinancial(CommandListener):
    def __init__(self):
        self.accesses = []
    def started(self, event):
        collection = event.command.get(event.command_name)
        if isinstance(collection, str) and collection in {"general_ledger", "accounts", "suppliers", "counterparties"}:
            self.accesses.append((event.command_name, collection))
    def succeeded(self, event): pass
    def failed(self, event): pass
