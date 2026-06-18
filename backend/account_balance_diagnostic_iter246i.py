"""Iter-246i — Per-account balance diagnostic.

Read-only audit that lists, for every bank/cash/payment_platform
account belonging to the merchant:
  * stored_balance   — `accounts.current_balance`
  * ssot_balance     — `account_balance_ssot()` (canonical source used
                       by /accounts, /accounts/summary, /financial-
                       position)
  * ledger_balance   — `compute_balance(entity_type="bank", ...)`
                       restricted to the `general_ledger` collection
  * difference       — ssot − stored
  * status           — "ok" when |difference| ≤ 0.01, else "drift"

Reports the **same** SSOT across:
  • Iter-245 «فاتورة مورد» (`accounts-with-availability` after
    Iter-246i now also uses ssot)
  • Legacy «سداد مورد» (uses ssot via /accounts)
  • /accounts and /accounts/summary
  • /accounting/financial-position
"""
from __future__ import annotations

from fastapi import APIRouter, Depends


def make_balance_diagnostic_router(db, current_user):
    router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

    @router.get("/account-balances")
    async def account_balance_audit(user: dict = Depends(current_user)):
        uid = user["id"]
        rows = await db.accounts.find(
            {"user_id": uid,
             "account_type": {"$in": ["bank", "cash", "payment_platform"]}},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1,
             "current_balance": 1, "currency": 1, "status": 1,
             "opening_balance": 1, "expected_orders_balance": 1,
             "normalized_payment_method": 1},
        ).sort([("account_type", 1), ("name", 1)]).to_list(500)

        try:
            from financial_position_ssot import account_balance_ssot
        except Exception:  # noqa: BLE001
            account_balance_ssot = None
        try:
            from ledger_core import compute_balance
        except Exception:  # noqa: BLE001
            compute_balance = None

        out = []
        n_drift = 0
        for r in rows:
            stored = round(float(r.get("current_balance") or 0), 2)
            ssot = stored
            if account_balance_ssot:
                try:
                    ssot = round(float(await account_balance_ssot(
                        db, user_id=uid, account=r)), 2)
                except Exception:  # noqa: BLE001
                    ssot = None
            ledger_bal = None
            if compute_balance:
                try:
                    b = await compute_balance(
                        db, user_id=uid, entity_type="bank",
                        entity_id=r["id"])
                    ledger_bal = round(
                        float(b.get("net_balance") or 0), 2)
                except Exception:  # noqa: BLE001
                    ledger_bal = None
            diff = (
                round(ssot - stored, 2)
                if ssot is not None else None
            )
            status = "ok"
            if diff is not None and abs(diff) > 0.01:
                status = "drift"
                n_drift += 1
            row = {
                "account_id": r["id"],
                "account_name": r.get("name"),
                "account_type": r.get("account_type"),
                "currency": r.get("currency"),
                "stored_balance": stored,
                "ssot_balance": ssot,
                "ledger_balance": ledger_bal,
                "difference": diff,
                "status": status,
            }
            out.append(row)

        return {
            "ok": True,
            "iter": "iter246i",
            "summary": {
                "total_accounts": len(out),
                "drifted": n_drift,
                "drift_total":
                    round(sum(
                        r["difference"] or 0
                        for r in out if r["status"] == "drift"), 2),
            },
            "accounts": out,
            "note": (
                "SSOT is computed by `account_balance_ssot()`.  Every "
                "merchant-facing screen (فاتورة مورد، سداد مورد، "
                "الأصول والحسابات، صفحة الحساب، شاشة المركز المالي) "
                "consumes the SAME source after Iter-246i.  Drifts here "
                "mean a stale `current_balance` document field — they "
                "no longer affect any displayed number."
            ),
        }

    return router
