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
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from tz_utils import riyadh_today


# ── Iter-130 — BNPL providers operate on Saudi Arabia local time
# (Asia/Riyadh, UTC+3, no DST).  Tabby & Tamara cut off invoice
# periods at midnight Saudi time.  Provider API timestamps however
# come back in UTC ISO form (e.g. "2026-05-03T23:50:41Z" = 02:50
# Saudi May 4).  Earlier versions of this engine filtered Mongo
# string-fields with the raw user-supplied YYYY-MM-DD which
# implicitly treated the boundary as UTC midnight → orders that
# happened in the last 3 UTC hours of the previous day (= the first
# 3 hours of the next Saudi day) were silently dropped from the
# settlement, producing a +/−413 SAR discrepancy vs the official
# Tabby invoice.  Centralising the conversion here also makes the
# logic trivially testable.
RIYADH_UTC_OFFSET = timedelta(hours=3)


def _local_date_window_utc(
    date_from: Optional[str], date_to: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Convert a Saudi-local [date_from, date_to] window (inclusive,
    YYYY-MM-DD strings) into UTC ISO bounds suitable for string-
    comparison against provider timestamps stored as ISO-Z text.

    Returns (utc_gte, utc_lte) where:
        utc_gte = `date_from` 00:00 Asia/Riyadh expressed in UTC
        utc_lte = `date_to`   23:59:59 Asia/Riyadh expressed in UTC
    Either component may be ``None`` if the corresponding input is
    falsy (caller decides whether to set that side of the range).
    """
    utc_gte: Optional[str] = None
    utc_lte: Optional[str] = None
    if date_from:
        try:
            d = datetime.strptime(date_from[:10], "%Y-%m-%d")
            utc_gte = (d - RIYADH_UTC_OFFSET).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            utc_gte = date_from
    if date_to:
        try:
            # 23:59:59 Saudi  =>  20:59:59 UTC of the same date.
            d = datetime.strptime(date_to[:10], "%Y-%m-%d") + timedelta(
                hours=23, minutes=59, seconds=59,
            )
            utc_lte = (d - RIYADH_UTC_OFFSET).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            utc_lte = date_to + "T23:59:59Z"
    return utc_gte, utc_lte


# Fee rates (mirror payment_methods.py — keeping a local copy so we
# don't create a circular import.  These are merchant-default rates;
# bnpl_settings overrides them per merchant.).
DEFAULT_FEE_RATES: Dict[str, Dict[str, float]] = {
    # Iter-134 — kept in sync with config_store.DEFAULTS so every code
    # path (UI defaults, settlements engine fallback) sees the same
    # vendor-canonical numbers.
    "tabby":  {"commission_pct": 6.99, "vat_pct": 15.0,
               "fixed_fee_per_order": 1.0,
               "refundable_commission_pct": 4.99,
               "settlement_fee_per_invoice": 6.0,
               "settlement_fee_vat_applicable": True,
               "settlement_period_days": 7},
    "tamara": {"commission_pct": 6.99, "vat_pct": 15.0,
               "fixed_fee_per_order": 1.50,
               # Iter-232 — Tamara DOES NOT refund commission on
               # refunded orders.  Their weekly Statement charges the
               # full MDR + fixed_fee on every Captured order, and
               # lists Refunds as a separate net-amount deduction with
               # ZERO commission rebate.  Setting this to 0 makes the
               # engine match Tamara's official totals to the cent.
               "refundable_commission_pct": 0.0,
               "settlement_fee_per_invoice": 0.0,
               "settlement_fee_vat_applicable": True,
               "settlement_period_days": 7},
}

PROVIDERS = ("tabby", "tamara")

# ── Iter-121 — Weekday-based settlement cycle ─────────────────────
# Python's date.weekday() returns 0 = Monday … 6 = Sunday.  We map the
# canonical lowercase names used in `bnpl_settings.invoice_weekdays`
# / `transfer_weekdays` to those integers.
WEEKDAY_TO_INT: Dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _weekday_set(names: Optional[List[str]]) -> set[int]:
    """Convert a list of canonical weekday names to a set of ints."""
    if not names:
        return set()
    return {WEEKDAY_TO_INT[n] for n in names if n in WEEKDAY_TO_INT}


def _next_or_same_weekday(d: date, allowed: set[int]) -> Optional[date]:
    """Return the first date ≥ `d` whose weekday is in `allowed`.

    If `allowed` is empty, returns None (caller should fall back to the
    legacy day-count window).  We never look more than 14 days ahead —
    if the user picked an empty allowed set we'd loop forever.
    """
    if not allowed:
        return None
    from datetime import timedelta
    for offset in range(14):  # at most one full fortnight
        cand = d + timedelta(days=offset)
        if cand.weekday() in allowed:
            return cand
    return None


def _next_strict_weekday(d: date, allowed: set[int]) -> Optional[date]:
    """Like above but STRICTLY after `d`."""
    if not allowed:
        return None
    from datetime import timedelta
    return _next_or_same_weekday(d + timedelta(days=1), allowed)


def _r(x: float) -> float:
    return round(float(x or 0), 2)


def _round_sar(value: Decimal | float) -> float:
    """Round money to halalas with provider-style half-up semantics."""
    decimal_value = (
        value if isinstance(value, Decimal) else Decimal(str(value or 0))
    )
    return float(decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _capture_fee_components(
    provider: str,
    amount: float,
    commission_rate: float,
    fixed_fee: float,
    vat_rate: float,
) -> tuple[float, float]:
    """Return capture fee and fee VAT using the provider's rounding model.

    Tamara's five verified 2026-08 statements round the fee per capture and
    then calculate VAT on that displayed rounded fee.  Tabby retains the
    existing sum-first behavior, so its unrounded components are returned for
    accumulation and final rounding by the caller.
    """
    if provider == "tamara":
        amount_d = Decimal(str(amount or 0))
        rate_d = Decimal(str(commission_rate or 0))
        fixed_d = Decimal(str(fixed_fee or 0))
        vat_d = Decimal(str(vat_rate or 0))
        fee = _round_sar(amount_d * rate_d + fixed_d)
        vat = _round_sar(Decimal(str(fee)) * vat_d)
        return fee, vat

    fee_unrounded = float(amount or 0) * commission_rate + fixed_fee
    return fee_unrounded, fee_unrounded * vat_rate


def _count_settlements_in_period(
    date_from: Optional[str], date_to: Optional[str],
    settlement_period_days: int = 7,
    invoice_weekdays: Optional[List[str]] = None,
) -> int:
    """How many provider settlement invoices fall inside the requested
    period.  Iter-121 — when `invoice_weekdays` is configured, count
    occurrences of those weekdays inside [date_from, date_to];
    otherwise fall back to the legacy day-count window.  Floors at 1
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
    days = (d_to - d_from).days + 1
    if days <= 0:
        return 1
    # Iter-121 — if invoice_weekdays is configured, count actual
    # occurrences of those weekdays inside the window.
    allowed = _weekday_set(invoice_weekdays)
    if allowed:
        from datetime import timedelta
        count = 0
        cur = d_from
        while cur <= d_to:
            if cur.weekday() in allowed:
                count += 1
            cur += timedelta(days=1)
        return max(1, count)
    return max(1, math.ceil(days / max(1, settlement_period_days)))


async def _merchant_fee_rates(
    db, user_id: str, provider: str,
) -> Dict[str, Any]:
    """Load per-merchant fee rates with a UNIFIED SOURCE OF TRUTH.

    Iter-126 — Resolution order:
      1. `users.settings.payment_methods[name]` — the Settings page is the
         SINGLE place merchants edit commission_percent / fixed_fee /
         vat_percent for ALL payment methods (mada, visa, tabby, tamara,
         إمكان…).  This is the canonical source.
      2. `bnpl_settings.{mdr_percent, fixed_fee_per_order,
         vat_on_fees_percent}` — legacy per-BNPL fields, kept as a
         migration fallback for users whose payment_methods entry is
         still missing.  When a user saves on either screen, both
         locations get updated by the settings save hook.
      3. Code defaults from `DEFAULT_FEE_RATES` — only used the very
         first time the user opens the page (before any save).

    Settlement-specific fields (`settlement_fee_per_invoice`,
    `settlement_period_days`, `invoice_weekdays`, `transfer_weekdays`)
    live exclusively in `bnpl_settings` since they have no analogue in
    `payment_methods`.

    Returns the rates as PERCENT (e.g. 5.0) — not fractions — so the
    UI can display them directly.
    """
    defaults = DEFAULT_FEE_RATES.get(provider, {
        "commission_pct": 0, "vat_pct": 0,
        "fixed_fee_per_order": 0,
        "settlement_fee_per_invoice": 0, "settlement_period_days": 7,
    })
    rates: Dict[str, Any] = dict(defaults)
    # Iter-121 — weekday defaults (canonical).  Tabby = Monday close,
    # Tue/Wed payouts; Tamara statements close Friday and issue Saturday.
    weekday_defaults = {
        "tabby":  {"invoice_weekdays":  ["monday"],
                   "transfer_weekdays": ["tuesday", "wednesday"]},
        "tamara": {"invoice_weekdays":  ["saturday"],
                   "transfer_weekdays": ["tuesday"]},
    }
    wd = weekday_defaults.get(provider, {})
    rates["invoice_weekdays"]  = list(wd.get("invoice_weekdays")  or [])
    rates["transfer_weekdays"] = list(wd.get("transfer_weekdays") or [])
    rates["fee_source"] = "code_default"

    # Iter-137 — Read commission_mode FIRST so STEP 1 (payment_methods)
    # can also be bypassed when the merchant is on auto.  Otherwise an
    # outdated commission_percent in payment_methods would leak through
    # even after the user toggled to "auto".
    doc = await db.bnpl_settings.find_one(
        {"user_id": user_id, "provider": provider}, {"_id": 0},
    )
    commission_mode = (doc or {}).get("commission_mode", "auto")
    auto_locked_fields = (
        {"mdr_percent", "vat_on_fees_percent", "fixed_fee_per_order",
         "refundable_commission_percent", "settlement_fee_per_invoice",
         "settlement_fee_vat_applicable"}
        if commission_mode == "auto" else set()
    )

    # Iter-126 — STEP 1: read from the unified payment_methods settings.
    # Provider name → Arabic display label that matches `payment_methods`.
    PROVIDER_AR_NAME = {"tabby": "تابي", "tamara": "تمارا", "emkan": "إمكان"}
    ar_name = PROVIDER_AR_NAME.get(provider)
    if ar_name and commission_mode != "auto":
        user_doc = await db.users.find_one(
            {"id": user_id},
            {"_id": 0, "settings.payment_methods": 1},
        )
        pm_list = ((user_doc or {}).get("settings") or {}).get("payment_methods") or []
        match = next((pm for pm in pm_list if pm.get("name") == ar_name), None)
        if match:
            if match.get("commission_percent") is not None:
                rates["commission_pct"] = float(match["commission_percent"])
            if match.get("vat_percent") is not None:
                rates["vat_pct"] = float(match["vat_percent"])
            if match.get("fixed_fee") is not None:
                rates["fixed_fee_per_order"] = float(match["fixed_fee"])
            rates["fee_source"] = "payment_methods_settings"
    elif commission_mode == "auto":
        rates["fee_source"] = "auto_canonical_defaults"

    # Iter-126 — STEP 2: bnpl_settings legacy fallback / overrides for
    # the BNPL-specific fields (settlement_fee_per_invoice, weekdays).
    # We also let bnpl_settings.* override fee fields IF the unified
    # payment_methods entry didn't supply them (graceful migration).

    if doc:
        if rates["fee_source"] != "payment_methods_settings":
            # No payment_methods entry yet — fall back to bnpl_settings.
            if (doc.get("mdr_percent") is not None
                    and "mdr_percent" not in auto_locked_fields):
                rates["commission_pct"] = round(float(doc["mdr_percent"]) * 100, 4)
                rates["fee_source"] = "bnpl_settings_legacy"
            if (doc.get("vat_on_fees_percent") is not None
                    and "vat_on_fees_percent" not in auto_locked_fields):
                rates["vat_pct"] = round(float(doc["vat_on_fees_percent"]) * 100, 4)
            if (doc.get("fixed_fee_per_order") is not None
                    and "fixed_fee_per_order" not in auto_locked_fields):
                rates["fixed_fee_per_order"] = float(doc["fixed_fee_per_order"])
        # ALWAYS take these BNPL-specific fields from bnpl_settings
        # since payment_methods has no equivalent.
        if (doc.get("settlement_fee_per_invoice") is not None
                and "settlement_fee_per_invoice" not in auto_locked_fields):
            rates["settlement_fee_per_invoice"] = float(doc["settlement_fee_per_invoice"])
        if doc.get("settlement_period_days") is not None:
            rates["settlement_period_days"] = int(doc["settlement_period_days"])
        if "invoice_weekdays" in doc and isinstance(doc["invoice_weekdays"], list):
            invoice_weekdays = list(doc["invoice_weekdays"])
            if provider == "tamara":
                from .config_store import TAMARA_STATEMENT_CYCLE_VERSION
                if (
                    doc.get("statement_cycle_defaults_version")
                    != TAMARA_STATEMENT_CYCLE_VERSION
                    and invoice_weekdays == ["sunday"]
                ):
                    invoice_weekdays = ["saturday"]
            rates["invoice_weekdays"] = invoice_weekdays
        if "transfer_weekdays" in doc and isinstance(doc["transfer_weekdays"], list):
            rates["transfer_weekdays"] = list(doc["transfer_weekdays"])
        # Iter-134 — per-order commission split + VAT on settlement fee
        if (doc.get("refundable_commission_percent") is not None
                and "refundable_commission_percent" not in auto_locked_fields):
            rates["refundable_commission_pct"] = round(
                float(doc["refundable_commission_percent"]) * 100, 4,
            )
        if ("settlement_fee_vat_applicable" in doc
                and "settlement_fee_vat_applicable" not in auto_locked_fields):
            rates["settlement_fee_vat_applicable"] = bool(
                doc["settlement_fee_vat_applicable"],
            )
    # Iter-137 — record which mode the engine ran under so the UI /
    # settlement table can show a "🤖 Auto rates" badge.
    rates["commission_mode"] = commission_mode
    # Iter-134 — when the merchant hasn't set a separate refundable
    # commission percent on their bnpl_settings doc, fall back to the
    # vendor-canonical default for THIS provider (Tabby = 4.99%,
    # Tamara = 7%) — NOT to the full MDR.  Defaulting to full MDR
    # caused a +12 SAR over-pay in the merchant's Tabby invoice
    # because every refund was rebated at 6.99% instead of 4.99%.
    rates.setdefault(
        "refundable_commission_pct",
        defaults.get("refundable_commission_pct", rates.get("commission_pct", 0)),
    )
    # KSA VAT applies to processor fees by default.
    rates.setdefault("settlement_fee_vat_applicable", True)
    return rates


async def _compute_provider_totals(
    db, user_id: str, provider: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate gross sales + refunds for one provider.

    ⚠️ ITER-120 / ITER-146 — IMPORTANT ACCOUNTING RULES:
        • Sales are aggregated by ORDER DATE (`created_at_provider`) for
          Tabby and other providers — Tabby's settlement statement counts
          orders from the day the payment was captured.
        • Sales are aggregated by `billing_eligible_at` for **Tamara**
          (Iter-146): Tamara enters an order into its weekly settlement
          on the week it first reaches a billable status
          (shipped / prepared / out_for_delivery / delivered / executed),
          NOT the week the order was created.  Orders that haven't
          reached a billable status are excluded from the period.
        • Refunds are aggregated by REFUND DATE (`refunded_at`) for ALL
          providers — this matches every BNPL statement we've observed.
    """
    # Iter-146 / Iter-147 — Tamara uses `effective_settlement_date`
    # for sales (priority: provider_official > billing_eligible >
    # estimated).  Other providers keep `created_at_provider`.
    sales_date_field = (
        "effective_settlement_date" if provider == "tamara"
        else "created_at_provider"
    )
    # ── Gross sales — by order date (or billing_eligible_at for Tamara)
    # Iter-130 — convert the Saudi-local window to a UTC ISO range so
    # we match exactly what Tabby/Tamara include in their invoices.
    utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)
    sales_match: Dict[str, Any] = {"user_id": user_id, "provider": provider}
    if utc_gte or utc_lte:
        rng: Dict[str, str] = {}
        if utc_gte:
            rng["$gte"] = utc_gte
        if utc_lte:
            rng["$lte"] = utc_lte
        sales_match[sales_date_field] = rng

    # Iter-246r — Same-Week Net-Zero captures (Tamara) are excluded from
    # Gross sales aggregation (their refund still appears on the refunds
    # side, so the net effect on the cycle is zero, matching what Tamara
    # actually reports on its Statement).  Read-only: no DB writes.
    if provider == "tamara":
        sales_match["same_week_netzero_exclusion"] = {"$ne": True}

    # Iter-149 — Skip orders & refunds whose entity date is BEFORE the
    # merchant's `accounting_start_date` for this provider.  These are
    # historical / pre-accounting rows the merchant has archived.
    try:
        from accounting_cutoffs import get_cutoff
        _cutoff = await get_cutoff(db, user_id, provider)
    except Exception:
        _cutoff = None
    if _cutoff:
        # Apply to BOTH date fields so the filter survives no matter
        # which one we're aggregating by.
        rng = sales_match.setdefault(sales_date_field, {})
        if isinstance(rng, dict):
            prev = rng.get("$gte")
            if not prev or str(prev) < _cutoff:
                rng["$gte"] = _cutoff
        # Also explicitly exclude any row already flagged.
        sales_match["is_pre_accounting"] = {"$ne": True}

    gross = 0.0
    count = 0
    async for r in db.payment_transactions.aggregate([
        {"$match": sales_match},
        {"$group": {
            "_id": None,
            "n": {"$sum": 1},
            "gross": {"$sum": {"$ifNull": ["$amount", 0]}},
        }},
    ]):
        count = int(r.get("n") or 0)
        gross = float(r.get("gross") or 0)

    # ── Refunds — by REFUND date, NOT order date ───────────────────
    # Iter-130 — same Saudi-local → UTC conversion as the sales side.
    refund_match: Dict[str, Any] = {"user_id": user_id, "provider": provider}
    if utc_gte or utc_lte:
        rng2: Dict[str, str] = {}
        if utc_gte:
            rng2["$gte"] = utc_gte
        if utc_lte:
            rng2["$lte"] = utc_lte
        refund_match["refunded_at"] = rng2

    # Iter-149 — exclude pre-accounting refunds too.
    if _cutoff:
        rng2 = refund_match.setdefault("refunded_at", {})
        if isinstance(rng2, dict):
            prev = rng2.get("$gte")
            if not prev or str(prev) < _cutoff:
                rng2["$gte"] = _cutoff
        refund_match["is_pre_accounting"] = {"$ne": True}

    refunds = 0.0
    refunds_count = 0
    async for r in db.payment_refunds.aggregate([
        {"$match": refund_match},
        {"$group": {
            "_id": None,
            "n": {"$sum": 1},
            "refunds": {"$sum": {"$ifNull": ["$amount", 0]}},
        }},
    ]):
        refunds_count = int(r.get("n") or 0)
        refunds = float(r.get("refunds") or 0)

    # Iter-234 — Tamara-only "orphan refund recovery".
    #
    # Problem: when an order is BOTH captured AND refunded inside the
    # SAME weekly Statement, Tamara counts it in BOTH Captured and
    # Refunds.  Our engine groups Tamara sales by
    # `effective_settlement_date` (priority: provider_captured →
    # billing_eligible → created_at_provider).  If the only attribution
    # we have is `created_at_provider` and it falls outside the window,
    # the order shows up ONLY on the refunds side and we miss its
    # +amount in Captured → net_sales is short by exactly that amount.
    #
    # Fix: for Tamara, scan refunds inside the window and "re-credit"
    # the gross side with the matching `payment_transactions.amount`
    # for any refund whose original order is missing from the window's
    # sales aggregation.  Match by `provider_payment_id` (Tamara
    # order_id) or by `order_reference_id`.
    if provider == "tamara" and refunds_count > 0:
        # Build the set of provider_ids already counted in `gross`.
        in_window_ids: set[str] = set()
        async for t in db.payment_transactions.find(
            sales_match,
            {"_id": 0, "provider_id": 1},
        ):
            pid = t.get("provider_id")
            if pid:
                in_window_ids.add(pid)

        # For every refund in the window, look up its original txn and
        # add it to gross if not already counted.
        recovered_amount = 0.0
        recovered_count = 0
        async for rf in db.payment_refunds.find(
            refund_match,
            {"_id": 0, "provider_payment_id": 1, "order_reference_id": 1,
             "amount": 1},
        ):
            pp_id = rf.get("provider_payment_id")
            ref_id = rf.get("order_reference_id")
            if pp_id and pp_id in in_window_ids:
                continue  # original already in gross — nothing to recover
            # Find the original Tamara payment.  Match by provider_id
            # first (strongest), fall back to order_reference_id.
            orig = None
            if pp_id:
                orig = await db.payment_transactions.find_one(
                    {"user_id": user_id, "provider": "tamara",
                     "provider_id": pp_id},
                    {"_id": 0, "amount": 1, "provider_id": 1,
                     "is_pre_accounting": 1,
                     "settlement_source": 1,
                     "same_week_netzero_exclusion": 1},
                )
            if not orig and ref_id:
                orig = await db.payment_transactions.find_one(
                    {"user_id": user_id, "provider": "tamara",
                     "order_reference_id": ref_id},
                    {"_id": 0, "amount": 1, "provider_id": 1,
                     "is_pre_accounting": 1,
                     "settlement_source": 1,
                     "same_week_netzero_exclusion": 1},
                )
            if not orig:
                continue
            if orig.get("is_pre_accounting"):
                continue
            # Iter-246r — DO NOT recover captures that were explicitly
            # pinned to a past Tamara settlement file (Fix #3) — they
            # were already accounted for in a previous cycle.  Also
            # skip captures flagged as Same-Week Net-Zero, since they
            # were intentionally excluded from this cycle's Gross.
            if (orig.get("settlement_source")
                    == "settlement_entries_historical"):
                continue
            if orig.get("same_week_netzero_exclusion"):
                continue
            orig_pid = orig.get("provider_id")
            if orig_pid and orig_pid in in_window_ids:
                continue
            amt = float(orig.get("amount") or 0)
            if amt <= 0:
                continue
            recovered_amount += amt
            recovered_count += 1
            if orig_pid:
                in_window_ids.add(orig_pid)
        if recovered_amount > 0:
            gross += recovered_amount
            count += recovered_count

    return {
        "transactions_count": count,
        "refunds_count": refunds_count,
        "gross_sales": _r(gross),
        "total_refunds": _r(refunds),
        "net_sales": _r(gross - refunds),
    }


async def _compute_period_items(
    db, user_id: str, provider: str,
    date_from: str, date_to: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Iter-120 — return the raw sales + refunds list for a settlement
    period, used by the UI to render the two detail tables under each
    weekly settlement row.

    Each refund row is enriched with the ORIGINAL order's date and
    amount (looked up via `provider_payment_id` → `payment_transactions
    .provider_id`) so the merchant can see at a glance that a refund
    in the current period belongs to an order from an earlier period.
    """
    # Iter-130 — same Saudi-local → UTC conversion as
    # `_compute_provider_totals` so detail tables match the totals.
    utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)
    sales_filter_range: Dict[str, str] = {}
    if utc_gte:
        sales_filter_range["$gte"] = utc_gte
    if utc_lte:
        sales_filter_range["$lte"] = utc_lte

    # Iter-146 / Iter-147 — Tamara uses effective_settlement_date for
    # the sales drill-down (drill-down must match the totals filter).
    sales_date_field = (
        "effective_settlement_date" if provider == "tamara"
        else "created_at_provider"
    )

    # Sales — orders whose order date falls inside the window.
    sales: List[Dict[str, Any]] = []
    async for t in db.payment_transactions.find(
        {
            "user_id":         user_id,
            "provider":        provider,
            sales_date_field:  sales_filter_range,
        },
        {"_id": 0, "id": 1, "order_reference_id": 1, "order_number": 1,
         "amount": 1, "currency": 1, "created_at_provider": 1,
         "billing_eligible_at": 1, "status": 1, "buyer_email": 1},
    ).sort([(sales_date_field, 1)]):
        sales.append({
            "id":                  t.get("id"),
            "order_reference_id":  t.get("order_reference_id"),
            "order_number":        t.get("order_number"),
            "order_date":          t.get("created_at_provider"),
            "billing_eligible_at": t.get("billing_eligible_at"),
            "amount":              _r(float(t.get("amount") or 0)),
            "currency":            t.get("currency") or "SAR",
            "status":              t.get("status"),
            "payment_method":      provider,
        })

    # Refunds — by refund date.  Then enrich with the original order
    # info so the table can show order_date + original_order_amount.
    refund_docs: List[Dict[str, Any]] = []
    async for r in db.payment_refunds.find(
        {
            "user_id":     user_id,
            "provider":    provider,
            "refunded_at": sales_filter_range,
        },
        {"_id": 0, "id": 1, "provider_refund_id": 1,
         "provider_payment_id": 1, "order_reference_id": 1,
         "amount": 1, "currency": 1, "refunded_at": 1,
         "reason": 1, "status": 1},
    ).sort([("refunded_at", 1)]):
        refund_docs.append(r)

    pmt_ids = list({r.get("provider_payment_id")
                    for r in refund_docs
                    if r.get("provider_payment_id")})
    pmt_map: Dict[str, Dict[str, Any]] = {}
    if pmt_ids:
        async for t in db.payment_transactions.find(
            {"user_id": user_id, "provider": provider,
             "provider_id": {"$in": pmt_ids}},
            {"_id": 0, "provider_id": 1, "amount": 1,
             "created_at_provider": 1, "order_reference_id": 1,
             "order_number": 1},
        ):
            pmt_map[t.get("provider_id")] = t

    refunds: List[Dict[str, Any]] = []
    for r in refund_docs:
        orig = pmt_map.get(r.get("provider_payment_id")) or {}
        refunds.append({
            "id":                    r.get("id"),
            "provider_refund_id":    r.get("provider_refund_id"),
            "order_reference_id":    r.get("order_reference_id")
                                     or orig.get("order_reference_id"),
            "order_number":          orig.get("order_number"),
            "order_date":            orig.get("created_at_provider"),
            "refund_date":           r.get("refunded_at"),
            "original_order_amount": (_r(float(orig.get("amount") or 0))
                                      if orig else None),
            "refund_amount":         _r(float(r.get("amount") or 0)),
            "currency":              r.get("currency") or "SAR",
            "payment_method":        provider,
            "reason":                r.get("reason"),
            "status":                r.get("status"),
        })

    return {"sales": sales, "refunds": refunds}


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



async def _aggregate_official_totals(
    db, user_id: str, date_from: str, date_to: str,
) -> Optional[Dict[str, Any]]:
    """Iter-147 v3 — Aggregate per-order entries from imported Tamara
    settlement files for a given period.

    Tamara's official settlement file is the ground truth for what the
    merchant was actually charged.  When such a file exists for the
    period, its totals override our computed totals (which can differ
    by a few hundred SAR because of missing webhooks / refunds in the
    wrong week / fee rate drift).

    Field mapping (matches `settlements_import/parsers/tamara.py`):
      • event_type             — "sale", "refund", or "canceled_fee"
      • actual_gross_amount    — gross order amount  (only on sales)
      • actual_payment_fee     — commission on sales; fixed fee on cancel
      • actual_payment_vat     — VAT on those provider fees
      • actual_net_amount      — order net (can be negative for refunds)
      • actual_refund_amount        — full refund amount
      • actual_partial_refund_amount — partial refund amount
      • settlement_date        — date Tamara booked this row

    Returns `None` when no entries exist (caller falls back to the
    computed totals).
    """
    if not date_from or not date_to:
        return None

    # Iter-149 — pre-accounting cutoff.
    try:
        from accounting_cutoffs import get_cutoff
        cutoff = await get_cutoff(db, user_id, "tamara")
    except Exception:
        cutoff = None
    effective_from = (
        max(date_from, cutoff) if cutoff and cutoff > date_from else date_from
    )

    # Iter-147 v3.2 — Deduplicate by (order_number, event_type,
    # settlement_date).  When the merchant uploads the same Tamara
    # settlement file twice (different bytes / re-export → new file_hash),
    # each row appears N times in `settlement_entries`.  We pick the
    # MOST RECENT entry per unique key so totals match Tamara's
    # statement instead of being multiplied by the upload count.
    gross_sales = 0.0
    total_refunds = 0.0
    sales_count = 0
    refunds_count = 0
    canceled_count = 0
    canceled_amount = 0.0
    commission = 0.0
    commission_vat = 0.0
    net_payable = 0.0
    found = False

    pipeline = [
        {"$match": {
            "user_id": user_id,
            "provider": "tamara",
            "settlement_date": {"$gte": effective_from, "$lte": date_to},
            "is_pre_accounting": {"$ne": True},
        }},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": {
                "order_number":   "$order_number",
                "event_type":     "$event_type",
                "settlement_date": "$settlement_date",
            },
            "doc": {"$first": "$$ROOT"},
        }},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    async for e in db.settlement_entries.aggregate(pipeline):
        found = True
        ev = (e.get("event_type") or "").lower()
        if ev == "refund":
            refunds_count += 1
            total_refunds += abs(float(
                e.get("actual_refund_amount")
                or e.get("actual_partial_refund_amount")
                or 0.0,
            ))
        elif ev == "canceled_fee":
            # Cancellation is not captured gross.  Tamara still deducts the
            # fixed fee and its VAT, so keep those expense legs and the
            # negative net without incrementing the sale count.
            canceled_count += 1
            canceled_amount += abs(float(
                e.get("actual_canceled_amount") or 0.0,
            ))
            commission += float(e.get("actual_payment_fee") or 0.0)
            commission_vat += float(e.get("actual_payment_vat") or 0.0)
        else:
            sales_count += 1
            gross_sales += float(e.get("actual_gross_amount") or 0.0)
            commission += float(e.get("actual_payment_fee") or 0.0)
            commission_vat += float(e.get("actual_payment_vat") or 0.0)
        net_payable += float(e.get("actual_net_amount") or 0.0)

    if not found:
        return None

    return {
        "transactions_count": sales_count,
        "refunds_count":      refunds_count,
        "canceled_count":     canceled_count,
        "canceled_amount":    _r(canceled_amount),
        "gross_sales":        _r(gross_sales),
        "total_refunds":      _r(total_refunds),
        "commission":         _r(commission),
        "commission_vat":     _r(commission_vat),
        "net_payable":        _r(net_payable),
    }


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
    # Iter-134 — per-order commission split + VAT on settlement fee.
    refundable_rate = float(fee_rates.get("refundable_commission_pct") or 0) / 100.0
    settlement_fee_vat_applicable = bool(
        fee_rates.get("settlement_fee_vat_applicable", True),
    )

    totals = await _compute_provider_totals(db, user_id, provider, date_from, date_to)

    # ── Provider-specific commission rounding ─────────────────────
    # Tabby accumulates raw fee products and rounds final totals. Tamara
    # rounds each capture fee and then VAT on the displayed rounded fee.
    # iterate the raw rows so the totals match the provider's invoice
    # to the cent.
    # Iter-146 / Iter-147 — for Tamara we filter sales by
    # `effective_settlement_date` so the per-order commission loop
    # matches the totals loop.
    utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)
    sales_date_field = (
        "effective_settlement_date" if provider == "tamara"
        else "created_at_provider"
    )
    sales_match: Dict[str, Any] = {"user_id": user_id, "provider": provider}
    if utc_gte or utc_lte:
        sales_match[sales_date_field] = {
            **({"$gte": utc_gte} if utc_gte else {}),
            **({"$lte": utc_lte} if utc_lte else {}),
        }
    # Iter-246r — match `_compute_provider_totals`: exclude Same-Week
    # Net-Zero captures from the per-order commission loop so the
    # commission stays consistent with Gross.
    if provider == "tamara":
        sales_match["same_week_netzero_exclusion"] = {"$ne": True}
    refund_match: Dict[str, Any] = {"user_id": user_id, "provider": provider}
    if utc_gte or utc_lte:
        refund_match["refunded_at"] = {
            **({"$gte": utc_gte} if utc_gte else {}),
            **({"$lte": utc_lte} if utc_lte else {}),
        }

    # Provider-specific rounding: Tabby sums raw products then rounds the
    # final total; Tamara rounds each capture fee, then VAT on that displayed
    # rounded fee.  Mixing these rules causes recurring halala drift.
    sales_commission = 0.0
    sales_vat = 0.0
    counted_pids: set[str] = set()
    async for t in db.payment_transactions.find(
        sales_match,
        {"_id": 0, "amount": 1, "provider_id": 1},
    ):
        amt = float(t.get("amount") or 0)
        capture_fee, capture_vat = _capture_fee_components(
            provider,
            amt,
            commission_rate,
            fixed_fee_per_order,
            vat_rate,
        )
        sales_commission += capture_fee
        sales_vat += capture_vat
        pid = t.get("provider_id")
        if pid:
            counted_pids.add(pid)

    # Iter-234 — Tamara orphan-refund recovery for the commission
    # loop.  Same logic as `_compute_provider_totals`: if a refund
    # lives in this window but its original capture isn't in the
    # window's sales filter (because attribution fell back to
    # `created_at_provider` outside the period), we still owe Tamara
    # the MDR + fixed_fee on that capture.  Add it here so the
    # per-order commission matches Tamara's Statement Total Fees.
    if provider == "tamara":
        async for rf in db.payment_refunds.find(
            refund_match,
            {"_id": 0, "provider_payment_id": 1,
             "order_reference_id": 1, "amount": 1},
        ):
            pp_id = rf.get("provider_payment_id")
            ref_id = rf.get("order_reference_id")
            if pp_id and pp_id in counted_pids:
                continue
            orig = None
            if pp_id:
                orig = await db.payment_transactions.find_one(
                    {"user_id": user_id, "provider": "tamara",
                     "provider_id": pp_id},
                    {"_id": 0, "amount": 1, "provider_id": 1,
                     "is_pre_accounting": 1,
                     "settlement_source": 1,
                     "same_week_netzero_exclusion": 1},
                )
            if not orig and ref_id:
                orig = await db.payment_transactions.find_one(
                    {"user_id": user_id, "provider": "tamara",
                     "order_reference_id": ref_id},
                    {"_id": 0, "amount": 1, "provider_id": 1,
                     "is_pre_accounting": 1,
                     "settlement_source": 1,
                     "same_week_netzero_exclusion": 1},
                )
            if not orig or orig.get("is_pre_accounting"):
                continue
            # Iter-246r — skip historically-pinned and Net-Zero captures
            # in the commission recovery loop too (mirrors gross loop).
            if (orig.get("settlement_source")
                    == "settlement_entries_historical"):
                continue
            if orig.get("same_week_netzero_exclusion"):
                continue
            orig_pid = orig.get("provider_id")
            if orig_pid and orig_pid in counted_pids:
                continue
            amt = float(orig.get("amount") or 0)
            if amt <= 0:
                continue
            capture_fee, capture_vat = _capture_fee_components(
                provider,
                amt,
                commission_rate,
                fixed_fee_per_order,
                vat_rate,
            )
            sales_commission += capture_fee
            sales_vat += capture_vat
            if orig_pid:
                counted_pids.add(orig_pid)

    refund_rebate = 0.0
    refund_vat_rebate = 0.0
    async for r in db.payment_refunds.find(refund_match, {"_id": 0, "amount": 1}):
        amt = float(r.get("amount") or 0)
        rebate_unrounded = amt * refundable_rate
        refund_rebate += rebate_unrounded
        refund_vat_rebate += rebate_unrounded * vat_rate

    commission     = _r(sales_commission - refund_rebate)
    commission_vat = _r(sales_vat        - refund_vat_rebate)

    # Settlement fee — charged ONCE per provider invoice.  Iter-121:
    # use weekday-based count if the merchant configured invoice_weekdays.
    settlement_invoices_count = _count_settlements_in_period(
        date_from, date_to, settlement_period_days,
        invoice_weekdays=fee_rates.get("invoice_weekdays"),
    )
    settlement_fee = _r(settlement_fee_per_invoice * settlement_invoices_count)
    # Iter-134 — KSA VAT on the processor's settlement service fee.
    settlement_fee_vat = (
        _r(settlement_fee * vat_rate) if settlement_fee_vat_applicable else 0.0
    )

    net_payable = _r(
        totals["net_sales"] - commission - commission_vat
        - settlement_fee - settlement_fee_vat
    )

    # Iter-147 v3 — When the merchant has uploaded an OFFICIAL Tamara
    # settlement file covering this period, use the file's totals as
    # the source of truth.  This guarantees the displayed weekly
    # invoice matches Tamara's invoice to the cent — eliminating any
    # discrepancy caused by missing/extra orders in our DB.
    data_source = "computed"
    system_totals: Optional[Dict[str, Any]] = None
    if provider == "tamara" and date_from and date_to:
        official = await _aggregate_official_totals(
            db, user_id, date_from, date_to,
        )
        if official and (
            official.get("transactions_count", 0)
            or official.get("refunds_count", 0)
            or official.get("canceled_count", 0)
        ):
            # Snapshot what our DB computed so the UI can show the diff.
            system_totals = {
                "transactions_count": totals["transactions_count"],
                "refunds_count": totals.get("refunds_count", 0),
                "gross_sales": totals["gross_sales"],
                "total_refunds": totals["total_refunds"],
                "net_sales": totals["net_sales"],
                "commission": _r(commission),
                "commission_vat": _r(commission_vat),
                "net_payable": _r(net_payable),
            }
            # Override with Tamara's official numbers.
            totals["transactions_count"] = official["transactions_count"]
            totals["refunds_count"] = official.get("refunds_count", 0)
            totals["canceled_count"] = official.get("canceled_count", 0)
            totals["canceled_amount"] = official.get("canceled_amount", 0.0)
            totals["gross_sales"] = official["gross_sales"]
            totals["total_refunds"] = official["total_refunds"]
            totals["net_sales"] = _r(
                official["gross_sales"] - official["total_refunds"],
            )
            commission = official["commission"]
            commission_vat = official["commission_vat"]
            # Tamara's official files don't separate fixed_fee_per_order
            # from variable_rate fees — they roll up into "fees".  Keep
            # settlement_fee as 0 (Tamara has no per-invoice fee).
            net_payable = _r(official["net_payable"])
            data_source = "provider_official_file"

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
            "refunds_count": totals.get("refunds_count", 0),
            "canceled_count": totals.get("canceled_count", 0),
            "canceled_amount": totals.get("canceled_amount", 0.0),
            "gross_sales": totals["gross_sales"],
            "total_refunds": totals["total_refunds"],
            "net_sales": totals["net_sales"],
            "commission": _r(commission),
            "commission_vat": _r(commission_vat),
            "settlement_fee": _r(settlement_fee),
            "settlement_fee_per_invoice": _r(settlement_fee_per_invoice),
            "settlement_invoices_count": settlement_invoices_count,
            # Iter-134 — settlement-fee VAT surfaces as its own line.
            "settlement_fee_vat": _r(settlement_fee_vat),
            "net_payable": _r(net_payable),
        },
        "bank": bank_info,
        "fee_rates": fee_rates,
        "period": {"from": date_from, "to": date_to},
        # Iter-147 v3 — surface the data source so the UI can show a
        # badge: "أرقام رسمية من تمارا" when an official file is used.
        "data_source": data_source,
        "system_totals": system_totals,
        # Iter-234 — version marker so the UI / debug tools can verify
        # the deployed engine includes the Tamara orphan-refund
        # recovery fix (gross + commission both honour
        # same-week-capture+refund orders).
        "engine_version": "iter246r",
    }


async def compute_weekly_settlements(
    db, user_id: str, provider: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return ONE settlement row per invoice period from `date_from` to
    `date_to` (inclusive).

    Iter-121 — period boundaries are driven by the merchant's
    `invoice_weekdays` setting (e.g. Tabby = Monday).  Each row's `to`
    is an invoice-issuance weekday; `from` is the day AFTER the
    previous invoice (or activation_date for the very first row).
    The `expected_transfer_date` is the soonest selected transfer
    weekday on/after the invoice date.

    If `invoice_weekdays` is empty (e.g. user cleared all checkboxes)
    we gracefully fall back to fixed N-day windows from
    `settlement_period_days`.
    """
    # Resolve floor + ceiling
    if not date_from:
        sett = await db.bnpl_settings.find_one(
            {"user_id": user_id, "provider": provider}, {"activation_date": 1},
        ) or {}
        date_from = sett.get("activation_date") or (
            (riyadh_today().replace(day=1)).isoformat()
        )
    if not date_to:
        date_to = riyadh_today().isoformat()

    try:
        floor = date.fromisoformat(date_from)
        ceil_ = date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return []

    if ceil_ < floor:
        return []

    fees = await _merchant_fee_rates(db, user_id, provider)
    period_days = int(fees.get("settlement_period_days") or 7)
    invoice_set = _weekday_set(fees.get("invoice_weekdays") or [])
    transfer_set = _weekday_set(fees.get("transfer_weekdays") or [])

    from datetime import timedelta
    rows: List[Dict[str, Any]] = []
    invoice_no = 1

    if invoice_set:
        # Iter-123 — Period convention change.  Each invoice covers a
        # FULL cycle that STARTS on its invoice_weekday and ENDS the
        # day BEFORE the next invoice_weekday.  Example:
        #     invoice_weekdays = [Monday]
        #     period 1 = Mon Apr 27 → Sun May 3   (full 7 days)
        #     period 2 = Mon May 4 → Sun May 10
        # The statement is "issued" on the FOLLOWING invoice_weekday
        # (i.e. day after the period ends), which is the day Tabby /
        # Tamara generate the actual settlement file.  The expected
        # bank transfer date is the first transfer_weekday on/after
        # the issue_date.
        period_start = floor
        while period_start <= ceil_:
            if period_start.weekday() in invoice_set:
                # We're already on an invoice day → full cycle.
                issue_date = _next_strict_weekday(period_start, invoice_set)
            else:
                # Mid-week start (e.g. activation date is a Wednesday).
                # First period is partial — ends just before the next
                # invoice day.
                issue_date = _next_or_same_weekday(period_start, invoice_set)

            if issue_date is None:
                # No more invoice days ahead — close out remaining
                # range as one final partial row.
                period_end = ceil_
            else:
                period_end = min(issue_date - timedelta(days=1), ceil_)

            if period_end < period_start:
                break

            s = await compute_settlement_for_provider(
                db, user_id, provider,
                period_start.isoformat(), period_end.isoformat(),
            )
            t = s.get("totals", {})
            b = s.get("bank", {})
            expected_transfer = (
                _next_or_same_weekday(issue_date, transfer_set)
                if (transfer_set and issue_date) else None
            )
            # Iter-129 — Each invoice's bank transfer arrives AFTER the
            # period ends (on/after `issue_date`).  Override the in-period
            # `transferred_amount` with the transfer window
            # [issue_date, issue_date + 14d] so the row reflects THIS
            # invoice's actual payout — not the previous invoice's that
            # happened to land mid-period.  Account-level transferred is
            # only available when a provider account exists.
            provider_acc = s.get("bank", {})
            acc_id = provider_acc.get("linked_account_id")
            if acc_id and issue_date:
                from datetime import timedelta
                payout_window_to = (issue_date + timedelta(days=14)).isoformat()
                this_invoice_transfer = await _bank_transfer_total(
                    db, user_id, acc_id,
                    issue_date.isoformat(), payout_window_to,
                )
            else:
                this_invoice_transfer = float(b.get("transferred_amount", 0) or 0)
            net_payable_val = float(t.get("net_payable", 0) or 0)
            row_remaining = round(net_payable_val - this_invoice_transfer, 2)
            rows.append({
                "invoice_no": invoice_no,
                "from": period_start.isoformat(),
                "to": period_end.isoformat(),
                "issue_date": issue_date.isoformat() if issue_date else None,
                "expected_transfer_date": (
                    expected_transfer.isoformat() if expected_transfer else None
                ),
                "transactions_count": t.get("transactions_count", 0),
                "refunds_count": t.get("refunds_count", 0),
                "canceled_count": t.get("canceled_count", 0),
                "canceled_amount": t.get("canceled_amount", 0),
                "gross_sales": t.get("gross_sales", 0),
                "total_refunds": t.get("total_refunds", 0),
                "net_sales": t.get("net_sales", 0),
                "commission": t.get("commission", 0),
                "commission_vat": t.get("commission_vat", 0),
                "settlement_fee": t.get("settlement_fee", 0),
                "settlement_fee_vat": t.get("settlement_fee_vat", 0),
                "net_payable": t.get("net_payable", 0),
                "transferred_amount": _r(this_invoice_transfer),
                "remaining_with_provider": row_remaining,
                # Iter-147 v3 — per-row data source so UI can show
                # "أرقام رسمية من تمارا" badge.
                "data_source": s.get("data_source", "computed"),
                "system_totals": s.get("system_totals"),
            })
            if issue_date is None or issue_date > ceil_:
                break
            period_start = issue_date
            invoice_no += 1
        return rows

    # Legacy fixed-N-day window (kept for backwards compat).
    cursor = floor
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
            "expected_transfer_date": None,
            "transactions_count": t.get("transactions_count", 0),
            "refunds_count": t.get("refunds_count", 0),
            "canceled_count": t.get("canceled_count", 0),
            "canceled_amount": t.get("canceled_amount", 0),
            "gross_sales": t.get("gross_sales", 0),
            "total_refunds": t.get("total_refunds", 0),
            "net_sales": t.get("net_sales", 0),
            "commission": t.get("commission", 0),
            "commission_vat": t.get("commission_vat", 0),
            "settlement_fee": t.get("settlement_fee", 0),
            "settlement_fee_vat": t.get("settlement_fee_vat", 0),
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
        "settlement_fee_vat": 0.0,
        "net_payable": 0.0,
        "transferred_amount": 0.0,
        "remaining_with_provider": 0.0,
    }
    for p in providers_out:
        t = p.get("totals", {})
        b = p.get("bank", {})
        for k in ("gross_sales", "total_refunds", "net_sales",
                  "commission", "commission_vat", "settlement_fee",
                  "settlement_fee_vat", "net_payable"):
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
