"""BNPL Automatic Settlement Engine — Phase 4.

Computes each merchant's expected settlement payable for Tabby and
Tamara from the data ALREADY in MongoDB.  No new API calls, no
backfills, no debug endpoints — just a pure aggregation over:

    payment_transactions  (provider-side truth, fetched by auto-sync)
    payment_refunds       (per-refund breakdown)
    accounts              (the provider's "wallet" account in our books)
    account_transactions  (internal_transfer rows = bank-out money)

Output structure (per provider):
    {
      "provider": "tabby",
      "totals": {
        "gross_sales":          ...,
        "total_refunds":        ...,
        "net_sales":            ...,
        "commission":           ...,   # net_sales × commission_rate
        "commission_vat":       ...,   # commission × VAT_rate
        "net_payable":          ...,   # net_sales − commission − VAT
      },
      "bank": {
        "linked_account_id":    "...",
        "linked_account_name":  "حساب تابي",
        "transferred_amount":   ...,   # money already moved OUT of it
        "remaining_with_provider": ..., # what provider still owes us
        "delta_overpayment":    ...,    # positive = surplus, negative = shortfall
      },
      "fee_rates": { "commission_pct": 5.0, "vat_pct": 15.0 },
      "period": { "from": "YYYY-MM-DD", "to": "YYYY-MM-DD" }
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# Fee rates (mirror payment_methods.py — keeping a local copy so we
# don't create a circular import.  These are merchant-default rates;
# real settlement files override them when available).
DEFAULT_FEE_RATES: Dict[str, Dict[str, float]] = {
    "tabby":  {"commission_pct": 5.00, "vat_pct": 15.0},
    "tamara": {"commission_pct": 6.99, "vat_pct": 15.0},
}

PROVIDERS = ("tabby", "tamara")


def _r(x: float) -> float:
    return round(float(x or 0), 2)


async def _compute_provider_totals(
    db, user_id: str, provider: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate gross sales + refunds for one provider from the
    existing `payment_transactions` collection."""
    match: Dict[str, Any] = {"user_id": user_id, "provider": provider}
    if date_from or date_to:
        rng: Dict[str, str] = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to + "T23:59:59Z"
        match["created_at_provider"] = rng

    gross = 0.0
    refunds = 0.0
    count = 0
    async for r in db.payment_transactions.aggregate([
        {"$match": match},
        {"$group": {
            "_id": None,
            "n": {"$sum": 1},
            "gross": {"$sum": {"$ifNull": ["$amount", 0]}},
            "refunds": {"$sum": {"$ifNull": ["$refunded_amount", 0]}},
        }},
    ]):
        count = int(r.get("n") or 0)
        gross = float(r.get("gross") or 0)
        refunds = float(r.get("refunds") or 0)

    return {
        "transactions_count": count,
        "gross_sales": _r(gross),
        "total_refunds": _r(refunds),
        "net_sales": _r(gross - refunds),
    }


async def _find_provider_account(
    db, user_id: str, provider: str,
) -> Optional[Dict[str, Any]]:
    """Locate the merchant's wallet account for this provider.  We
    match on (account_type='payment_platform', provider_name matches
    case-insensitive)."""
    return await db.accounts.find_one(
        {
            "user_id": user_id,
            "account_type": "payment_platform",
            "$or": [
                {"provider_name": {"$regex": f"^{provider}$", "$options": "i"}},
                {"normalized_payment_method": provider},
            ],
        },
        {"_id": 0},
    )


async def _bank_transfer_total(
    db, user_id: str, account_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> float:
    """Sum of internal_transfer outflow from this account into a bank
    account = money that's already left the BNPL wallet."""
    match: Dict[str, Any] = {
        "user_id": user_id,
        "account_id": account_id,
        "transaction_type": "internal_transfer",
        "direction": "out",
    }
    if date_from or date_to:
        rng: Dict[str, str] = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        match["transaction_date"] = rng

    total = 0.0
    async for r in db.account_transactions.aggregate([
        {"$match": match},
        {"$group": {"_id": None, "s": {"$sum": {"$ifNull": ["$amount", 0]}}}},
    ]):
        total = float(r.get("s") or 0)
    return _r(total)


async def compute_settlement_for_provider(
    db, user_id: str, provider: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Full settlement computation for ONE provider.  Pure read — never
    writes to the DB."""
    if provider not in PROVIDERS:
        return {"provider": provider, "error": f"unknown provider {provider}"}

    fee_rates = DEFAULT_FEE_RATES.get(provider, {"commission_pct": 0, "vat_pct": 0})
    commission_rate = fee_rates["commission_pct"] / 100.0
    vat_rate = fee_rates["vat_pct"] / 100.0

    totals = await _compute_provider_totals(db, user_id, provider, date_from, date_to)
    commission = totals["net_sales"] * commission_rate
    commission_vat = commission * vat_rate
    net_payable = totals["net_sales"] - commission - commission_vat

    # Bank-side reconciliation
    account = await _find_provider_account(db, user_id, provider)
    bank_info: Dict[str, Any] = {
        "linked_account_id": None,
        "linked_account_name": None,
        "transferred_amount": 0.0,
        "remaining_with_provider": _r(net_payable),
        "delta_overpayment": 0.0,
        "is_linked": False,
    }
    if account:
        acc_id = account.get("id") or account.get("_id")
        transferred = await _bank_transfer_total(
            db, user_id, acc_id, date_from, date_to,
        )
        remaining = _r(net_payable) - transferred
        bank_info = {
            "linked_account_id": acc_id,
            "linked_account_name": account.get("name") or f"حساب {provider}",
            "transferred_amount": transferred,
            "remaining_with_provider": _r(remaining),
            "delta_overpayment": _r(-remaining),    # positive = bank received more than due
            "is_linked": True,
        }

    return {
        "provider": provider,
        "totals": {
            "transactions_count": totals["transactions_count"],
            "gross_sales": totals["gross_sales"],
            "total_refunds": totals["total_refunds"],
            "net_sales": totals["net_sales"],
            "commission": _r(commission),
            "commission_vat": _r(commission_vat),
            "net_payable": _r(net_payable),
        },
        "bank": bank_info,
        "fee_rates": fee_rates,
        "period": {"from": date_from, "to": date_to},
    }


async def compute_all_settlements(
    db, user_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute settlements for both providers + global totals."""
    providers_out: List[Dict[str, Any]] = []
    for p in PROVIDERS:
        providers_out.append(
            await compute_settlement_for_provider(db, user_id, p, date_from, date_to),
        )

    totals = {
        "gross_sales": 0.0,
        "total_refunds": 0.0,
        "net_sales": 0.0,
        "commission": 0.0,
        "commission_vat": 0.0,
        "net_payable": 0.0,
        "transferred_amount": 0.0,
        "remaining_with_provider": 0.0,
    }
    for p in providers_out:
        t = p.get("totals", {})
        b = p.get("bank", {})
        for k in ("gross_sales", "total_refunds", "net_sales",
                  "commission", "commission_vat", "net_payable"):
            totals[k] += t.get(k, 0)
        totals["transferred_amount"] += b.get("transferred_amount", 0)
        totals["remaining_with_provider"] += b.get("remaining_with_provider", 0)
    for k in totals:
        totals[k] = _r(totals[k])

    return {
        "providers": providers_out,
        "totals": totals,
        "period": {"from": date_from, "to": date_to},
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
