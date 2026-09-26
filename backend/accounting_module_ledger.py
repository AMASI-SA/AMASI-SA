"""Cutover-scoped ledger readers; never use legacy balance fallbacks."""
from __future__ import annotations

from typing import Any

from accounting_module_contract import OPERATION_ID


def summarize_accounting_home_ledger(
    rows: list[dict[str, Any]],
    *,
    account_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    types = account_types or {}
    banks = providers = courier_receivable = courier_payable = 0.0
    unclassified: set[tuple[str, str, str]] = set()
    for row in rows:
        entity_type = str(row.get("entity_type") or "")
        entity_id = str(row.get("entity_id") or "")
        sub_account = str(row.get("sub_account") or "")
        net = round(float(row.get("net") or 0), 2)
        if entity_type == "bank":
            account_type = types.get(entity_id)
            if account_type in {"bank", "cash"}:
                banks += net
            elif account_type == "payment_platform":
                providers += max(net, 0.0)
            else:
                unclassified.add((entity_type, entity_id, sub_account))
        elif entity_type == "payment_gateway" and sub_account == "receivable":
            providers += max(net, 0.0)
        elif entity_type == "courier" and sub_account == "cod_receivable":
            courier_receivable += max(net, 0.0)
        elif entity_type == "courier" and sub_account == "payable":
            courier_payable += max(-net, 0.0)
        elif entity_type == "store_driver" and sub_account == "cod_receivable":
            courier_receivable += max(net, 0.0)
        elif entity_type == "store_driver" and sub_account == "delivery_fee_payable":
            courier_payable += max(-net, 0.0)
    return {
        "banks": round(banks, 2),
        "providers": round(providers, 2),
        "couriers_cod": round(courier_receivable - courier_payable, 2),
        "couriers_cod_receivable": round(courier_receivable, 2),
        "couriers_payable": round(courier_payable, 2),
        "unclassified_count": len(unclassified),
    }


async def opening_posted_is_verified(db, *, user_id: str, cutover: dict[str, Any]) -> bool:
    from accounting_writer_transition import transition_state
    transition = await transition_state(db, user_id)
    if transition["state"] == "v2_active":
        from accounting_ledger_v2 import verify_active_opening_v2
        return await verify_active_opening_v2(db, user_id=user_id, cutover=cutover)
    if transition["state"] != "legacy_active":
        return False
    group_id = str(cutover.get("opening_balance_txn_group_id") or "").strip()
    if not group_id:
        return False
    rows = await db.general_ledger.find(
        {
            "user_id": user_id,
            "txn_group_id": group_id,
            "entry_type": "opening_balance",
            "status": "posted",
            "metadata.operation_id": OPERATION_ID,
        },
        {"_id": 0, "amount": 1, "side": 1},
    ).to_list(10000)
    if len(rows) < 2:
        return False
    debit = round(sum(float(row.get("amount") or 0) for row in rows if row.get("side") == "debit"), 2)
    credit = round(sum(float(row.get("amount") or 0) for row in rows if row.get("side") == "credit"), 2)
    return debit > 0 and abs(debit - credit) <= 0.01


async def ledger_only_home_balances(db, *, user_id: str, cutover_at: str) -> dict[str, Any] | None:
    # The supplied date is retained for call compatibility, never scope authority.
    from accounting_mz2_reports import read_mz2_ledger, _sums
    scope = await read_mz2_ledger(db, owner=user_id)
    if scope["status"] != "available":
        return None
    account_types = {}
    async for account in db.accounts.find({"user_id": user_id},
            {"_id": 0, "id": 1, "account_type": 1}):
        account_types[str(account.get("id") or "")] = str(account.get("account_type") or "")
    async for account in db.mz2_financial_accounts.find(
            {"user_id": user_id}, {"_id": 0, "id": 1, "account_type": 1}):
        account_types[str(account.get("id") or "")] = str(account.get("account_type") or "")
    return summarize_accounting_home_ledger(_sums(scope["items"]), account_types=account_types)
