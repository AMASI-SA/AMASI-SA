"""Iter-195 — Centralized live-balance resolver.

🔒 Single helper that every endpoint MUST use when displaying or
validating an account's "live" balance. Priority chain:

    1. BNPL SSOT  (Tabby/Tamara payment_platform)
       → balance comes from `get_bnpl_provider_balance()` which
         derives it live from `payment_transactions` /
         `account_transactions`.  Bypasses the stale
         `accounts.current_balance` field which was found to drift
         by 60k+ SAR on production (forensic-report iter-194).
    2. Universal Ledger (bank/cash with an `opening_balance` entry)
       → balance comes from `compute_balance()` over
         `general_ledger`. This honours the iter-192 SSOT fix.
    3. Fallback: raw `accounts.current_balance` (legacy path).

The helper returns a dict so callers can show the source on the
UI (Phase-1 requirement: merchant must SEE that Tabby balance is
sourced from BNPL SSOT, not Ledger, until backfill is done).

READ-ONLY — never mutates any document.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


async def resolve_live_balance(
    db, *, user_id: str, account: Dict[str, Any],
) -> Dict[str, Any]:
    """Return the canonical live balance for ONE account.

    Args:
        account: an `accounts` document (or projection) containing at
            least `id`, `account_type`, `name`, `current_balance`.

    Returns:
        {
            "balance":        float,    # the ONE number every UI uses
            "source":         str,      # 'bnpl_ssot' | 'ledger' | 'current_balance'
            "raw_balance":    float,    # accounts.current_balance (unchanged)
            "components":     dict|None,# BNPL formula breakdown when applicable
        }
    """
    raw = round(float(account.get("current_balance") or 0), 2)

    # 1) BNPL SSOT — Tabby/Tamara
    try:
        from bnpl.balance_service import (
            is_bnpl_account, get_bnpl_provider_balance,
        )
        provider = is_bnpl_account(account)
        if provider:
            canon = await get_bnpl_provider_balance(
                db, user_id, provider,
            )
            return {
                "balance": round(float(canon.get("balance") or 0), 2),
                "source": "bnpl_ssot",
                "raw_balance": raw,
                "components": canon.get("components"),
            }
    except Exception:  # noqa: BLE001
        # Diagnostic failure should never block business endpoints.
        pass

    # 2) Universal Ledger — bank/cash with opening_balance
    if account.get("account_type") in ("bank", "cash"):
        try:
            from ledger_core import compute_balance as _cb
            has_opening = await db.general_ledger.find_one(
                {"user_id": user_id,
                 "entity_type": "bank",
                 "entity_id": account["id"],
                 "entry_type": "opening_balance",
                 "status": "posted"},
                {"_id": 1},
            )
            if has_opening:
                bal = await _cb(
                    db, user_id=user_id, entity_type="bank",
                    entity_id=account["id"], sub_account="main",
                )
                return {
                    "balance": round(float(bal["net_balance"]), 2),
                    "source": "ledger",
                    "raw_balance": raw,
                    "components": None,
                }
        except Exception:  # noqa: BLE001
            pass

    # 3) Fallback — raw current_balance
    return {
        "balance": raw,
        "source": "current_balance",
        "raw_balance": raw,
        "components": None,
    }


async def resolve_live_balance_by_id(
    db, *, user_id: str, account_id: str,
) -> Optional[Dict[str, Any]]:
    """Convenience: load the account doc first, then resolve.

    Returns None when the account does not exist for `user_id`.
    """
    acc = await db.accounts.find_one(
        {"id": account_id, "user_id": user_id},
        {"_id": 0, "id": 1, "name": 1, "account_type": 1,
         "current_balance": 1, "normalized_payment_method": 1,
         "provider_name": 1},
    )
    if not acc:
        return None
    res = await resolve_live_balance(db, user_id=user_id, account=acc)
    res["account"] = acc
    return res
