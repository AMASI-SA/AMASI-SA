"""BNPL Single Source of Truth (SSOT) for provider balances.

🔒 OFFICIAL FORMULA — every page that shows a Tabby/Tamara/Emkan balance
MUST call `get_bnpl_provider_balance()` from this module.  No page
is allowed to compute the balance with a different formula.

    bnpl_provider_balance =
        Σ payment_transactions.amount                # captured sales
      − Σ payment_transactions.refunded_amount       # refunds
      − Σ commission                                  # net_sales × rate
      − Σ commission_vat                              # commission × 15%
      − Σ settlement_fees                             # SAR × weekly invoices
      − Σ account_transactions.amount (direction=out) # transferred to bank

This is the same `remaining_with_provider` shown on the BNPL
Settlements page.  By routing the Accounts/Transfers page through
the same helper, all pages display identical numbers.

The helper is a thin wrapper around `compute_settlement_for_provider`
so we never duplicate the math.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .settlements_service import (
    compute_settlement_for_provider,
    PROVIDERS,
)


async def get_bnpl_provider_balance(
    db, user_id: str, provider: str,
) -> Dict[str, Any]:
    """Return the canonical balance breakdown for ONE provider.

    Output:
        {
            "provider": "tabby",
            "is_bnpl": True,
            "balance": 6514.87,            # the ONE number every page shows
            "components": {
                "gross_sales":     ...,
                "refunds":         ...,
                "net_sales":       ...,
                "commission":      ...,
                "commission_vat":  ...,
                "settlement_fee":  ...,
                "transferred_out": ...,
                "balance":         ...,
            },
            "account_id":   "...",         # linked payment_platform account
            "account_name": "حساب تابي",
        }
    """
    s = await compute_settlement_for_provider(db, user_id, provider)
    t = s.get("totals", {})
    b = s.get("bank", {})
    return {
        "provider": provider,
        "is_bnpl": True,
        # Balance = what provider still owes us (net_payable - transferred).
        # Equals `bank.remaining_with_provider` in the settlements API.
        "balance": float(b.get("remaining_with_provider") or 0),
        "components": {
            "gross_sales":     t.get("gross_sales", 0),
            "refunds":         t.get("total_refunds", 0),
            "net_sales":       t.get("net_sales", 0),
            "commission":      t.get("commission", 0),
            "commission_vat":  t.get("commission_vat", 0),
            "settlement_fee":  t.get("settlement_fee", 0),
            "transferred_out": b.get("transferred_amount", 0),
            "balance":         b.get("remaining_with_provider", 0),
        },
        "account_id":   b.get("linked_account_id"),
        "account_name": b.get("linked_account_name"),
        "fee_rates":    s.get("fee_rates", {}),
        "transactions_count": t.get("transactions_count", 0),
    }


async def get_all_bnpl_balances(db, user_id: str) -> List[Dict[str, Any]]:
    """Return canonical balances for all registered BNPL providers."""
    out: List[Dict[str, Any]] = []
    for p in PROVIDERS:
        out.append(await get_bnpl_provider_balance(db, user_id, p))
    return out


def is_bnpl_account(account: Dict[str, Any]) -> str | None:
    """Return the provider key ('tabby'/'tamara'/'emkan') if `account` is a
    BNPL payment_platform wallet for one of them, else None."""
    if not account or account.get("account_type") != "payment_platform":
        return None
    name = (account.get("provider_name") or account.get("name") or "").lower()
    norm = (account.get("normalized_payment_method") or "").lower()
    for p in PROVIDERS:
        if p in name or p in norm:
            return p
    return None
