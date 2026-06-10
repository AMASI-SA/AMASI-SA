"""BNPL Automatic Settlement Engine — Phase 4.

Computes each merchant's expected settlement payable for Tabby and
Tamara from the data ALREADY in MongoDB.  No new API calls, no
backfills, no debug endpoints — just a pure aggregation over:

    payment_transactions  (provider-side truth, fetched by auto-sync)
    payment_refunds       (per-refund breakdown)
    accounts              (the provider's "wallet" account in our books)
    account_transactions  (internal_transfer rows = bank-out money)
    bnpl_settings         (per-merchant fee overrides)

Output structure (per provider):
    {
      "provider": "tabby",
      "totals": {
        "gross_sales":           ...,
        "total_refunds":         ...,
        "net_sales":             ...,
        "commission":            ...,   # net_sales × commission_rate
        "commission_vat":        ...,   # commission × VAT_rate
        "settlement_fee":        ...,   # SAR × N invoices in period
        "settlement_fee_per_invoice": 5.0,
        "settlement_invoices_count": 6,
        "net_payable":           ...,   # net − commission − VAT − settle_fee
      },
      "bank": { … },
      "fee_rates": { "commission_pct": 5.0, "vat_pct": 15.0,
                     "settlement_fee_per_invoice": 5.0,
                     "settlement_period_days": 7 },
      "period": { "from": "YYYY-MM-DD", "to": "YYYY-MM-DD" }
    }
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional


# Fee rates (mirror payment_methods.py — keeping a local copy so we
# don't create a circular import.  These are merchant-default rates;
# bnpl_settings overrides them per merchant.).
DEFAULT_FEE_RATES: Dict[str, Dict[str, float]] = {
    "tabby":  {"commission_pct": 5.00, "vat_pct": 15.0,
               "fixed_fee_per_order": 1.0,
               "settlement_fee_per_invoice": 5.0,
               "settlement_period_days": 7},
    "tamara": {"commission_pct": 6.99, "vat_pct": 15.0,
               "fixed_fee_per_order": 0.0,
               "settlement_fee_per_invoice": 0.0,
               "settlement_period_days": 7},
}

PROVIDERS = ("tabby", "tamara")


def _r(x: float) -> float:
    return round(float(x or 0), 2)


def _count_settlements_in_period(
    date_from: Optional[str], date_to: Optional[str],
    settlement_period_days: int = 7,
) -> int:
    """How many provider weekly settlement invoices fall inside the
    requested period.  When the period is open-ended (no `from`/`to`)
    we treat it as the time SINCE provider activation, but the caller
    decides — here we just compute from the dates given.  Floors at 1
    so we always charge AT LEAST one settlement fee per period view."""
    if not date_from or not date_to:
        return 1
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return 1
    days = (d_to - d_from).days + 1
    if days <= 0:
        return 1
    return max(1, math.ceil(days / max(1, settlement_period_days)))


async def _merchant_fee_rates(
    db, user_id: str, provider: str,
) -> Dict[str, float]:
    """Load per-merchant overrides from bnpl_settings, falling back to
    provider defaults.  Returns the rates as PERCENT (e.g. 5.0) — not
    fractions — so the UI can display them directly."""
    defaults = DEFAULT_FEE_RATES.get(provider, {
        "commission_pct": 0, "vat_pct": 0,
        "fixed_fee_per_order": 0,
        "settlement_fee_per_invoice": 0, "settlement_period_days": 7,
    })
    rates = dict(defaults)
    doc = await db.bnpl_settings.find_one(
        {"user_id": user_id, "provider": provider}, {"_id": 0},
    )
    if doc:
        # `mdr_percent` in DB is stored as fraction (0.05) — convert.
        if doc.get("mdr_percent") is not None:
            rates["commission_pct"] = round(float(doc["mdr_percent"]) * 100, 4)
        if doc.get("vat_on_fees_percent") is not None:
            rates["vat_pct"] = round(float(doc["vat_on_fees_percent"]) * 100, 4)
        if doc.get("fixed_fee_per_order") is not None:
            rates["fixed_fee_per_order"] = float(doc["fixed_fee_per_order"])
        if doc.get("settlement_fee_per_invoice") is not None:
            rates["settlement_fee_per_invoice"] = float(doc["settlement_fee_per_invoice"])
        if doc.get("settlement_period_days") is not None:
            rates["settlement_period_days"] = int(doc["settlement_period_days"])
    return rates


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

    fee_rates = await _merchant_fee_rates(db, user_id, provider)
    commission_rate = fee_rates["commission_pct"] / 100.0
    vat_rate = fee_rates["vat_pct"] / 100.0
    fixed_fee_per_order = float(fee_rates.get("fixed_fee_per_order") or 0)
    settlement_fee_per_invoice = fee_rates["settlement_fee_per_invoice"]
    settlement_period_days = int(fee_rates.get("settlement_period_days") or 7)

    totals = await _compute_provider_totals(db, user_id, provider, date_from, date_to)
    txn_count = totals.get("transactions_count", 0)
    # Commission = percentage × net_sales + fixed_fee × txn_count.
    # The fixed per-order fee (e.g. Tabby 1 SAR) is charged on EVERY
    # transaction regardless of refund status, so we multiply by the
    # full transactions count, not net_sales.
    commission_pct_part = totals["net_sales"] * commission_rate
    commission_fixed_part = fixed_fee_per_order * txn_count
    commission = commission_pct_part + commission_fixed_part
    commission_vat = commission * vat_rate

    # Settlement fee — charged ONCE per provider invoice (weekly).
    # Count how many invoices fall inside the requested period.
    settlement_invoices_count = _count_settlements_in_period(
        date_from, date_to, settlement_period_days,
    )
    settlement_fee = settlement_fee_per_invoice * settlement_invoices_count

    net_payable = (
        totals["net_sales"] - commission - commission_vat - settlement_fee
    )

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
            "delta_overpayment": _r(-remaining),
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
            "settlement_fee": _r(settlement_fee),
            "settlement_fee_per_invoice": _r(settlement_fee_per_invoice),
            "settlement_invoices_count": settlement_invoices_count,
            "net_payable": _r(net_payable),
        },
        "bank": bank_info,
        "fee_rates": fee_rates,
        "period": {"from": date_from, "to": date_to},
    }


async def compute_weekly_settlements(
    db, user_id: str, provider: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return ONE settlement row per weekly period from `date_from` to
    `date_to` (inclusive).  Useful for the settlements history table.

    If `date_from` is None, falls back to the provider's activation
    date in bnpl_settings.  If `date_to` is None, falls back to today.
    """
    # Resolve floor + ceiling
    if not date_from:
        sett = await db.bnpl_settings.find_one(
            {"user_id": user_id, "provider": provider}, {"activation_date": 1},
        ) or {}
        date_from = sett.get("activation_date") or (
            (date.today().replace(day=1)).isoformat()
        )
    if not date_to:
        date_to = date.today().isoformat()

    try:
        floor = date.fromisoformat(date_from)
        ceil_ = date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return []

    if ceil_ < floor:
        return []

    fees = await _merchant_fee_rates(db, user_id, provider)
    period_days = int(fees.get("settlement_period_days") or 7)

    rows: List[Dict[str, Any]] = []
    cursor = floor
    invoice_no = 1
    from datetime import timedelta
    while cursor <= ceil_:
        week_end = min(cursor + timedelta(days=period_days - 1), ceil_)
        s = await compute_settlement_for_provider(
            db, user_id, provider,
            cursor.isoformat(), week_end.isoformat(),
        )
        t = s.get("totals", {})
        b = s.get("bank", {})
        rows.append({
            "invoice_no": invoice_no,
            "from": cursor.isoformat(),
            "to": week_end.isoformat(),
            "transactions_count": t.get("transactions_count", 0),
            "gross_sales": t.get("gross_sales", 0),
            "total_refunds": t.get("total_refunds", 0),
            "net_sales": t.get("net_sales", 0),
            "commission": t.get("commission", 0),
            "commission_vat": t.get("commission_vat", 0),
            "settlement_fee": t.get("settlement_fee", 0),
            "net_payable": t.get("net_payable", 0),
            "transferred_amount": b.get("transferred_amount", 0),
            "remaining_with_provider": b.get("remaining_with_provider", 0),
        })
        cursor = week_end + timedelta(days=1)
        invoice_no += 1
    return rows


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
        "settlement_fee": 0.0,
        "net_payable": 0.0,
        "transferred_amount": 0.0,
        "remaining_with_provider": 0.0,
    }
    for p in providers_out:
        t = p.get("totals", {})
        b = p.get("bank", {})
        for k in ("gross_sales", "total_refunds", "net_sales",
                  "commission", "commission_vat", "settlement_fee",
                  "net_payable"):
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
