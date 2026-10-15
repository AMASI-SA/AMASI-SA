"""Iter-251 · Phase 2B — Settlement Engine Generation module.

Generates ``settlement_periods``, ``settlement_invoices`` and
``expected_transfers`` documents from the EXISTING centralised
formulas — never hard-codes commission / VAT / settlement-fee
percentages.

Source-of-truth resolution
==========================

BNPL providers (tamara, tabby, Emkan)
    Uses ``bnpl.settlements_service.compute_weekly_settlements`` —
    the SAME function consumed by the
    ``/api/bnpl/settlements/register`` page.  That helper in turn
    reads:

        users.settings.payment_methods[<provider>]   ← unified rates
        bnpl_settings.<provider>                     ← weekday cycle
        DEFAULT_FEE_RATES                            ← code defaults
                                                       (only when the
                                                        user has never
                                                        saved)

    Any future change in commission %, VAT %, weekdays, etc. on the
    BNPL settings page is automatically reflected here.

Salla
    Uses ``db.settlement_entries`` grouped by ``settlement_reference``
    — exact same source the dry-run engine already groups.

Emkan
    Uses the same central fee source and one period per uploaded official
    settlement report. No weekly cycle is invented.

Generation safety
-----------------
* No GL writes, no ``bank_transfer_review`` rows, no webhook touches.
* Idempotent: re-running with the same ``(provider, period_from,
  period_to)`` returns the existing period & invoice.
* Guarded behind the ``settlement_engine_enabled`` feature flag at
  the route layer.  This module itself is pure.

Lifecycle (settlement_invoices.status)
--------------------------------------
    draft                       — generation in progress
    generated                   — successfully generated, no transfer yet
    waiting_transfer            — bank transfer expected
    pending_review              — linked to a bank_transfer_review row
    confirmed                   — reviewer confirmed received = expected
    confirmed_with_difference   — reviewer confirmed with diff
    cancelled                   — manually voided
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional


INVOICE_STATUSES = {
    "draft", "generated", "waiting_transfer", "pending_review",
    "confirmed", "confirmed_with_difference", "cancelled",
}
PERIOD_STATUSES = {"open", "closed", "invoiced", "cancelled"}
EXPECTED_TRANSFER_STATUSES = {
    "pending", "linked_to_review", "settled", "cancelled",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _r(x: Any) -> float:
    try:
        return round(float(x or 0), 2)
    except Exception:
        return 0.0


async def _resolve_default_bank(
    db, uid: str, provider: str,
) -> tuple[Optional[str], Optional[str]]:
    """Return (bank_id, bank_name) from
    ``settings.default_bank_for_<provider>`` — never hard-coded."""
    setting_provider = "imkan" if provider == "emkan" else provider
    setting_key = f"default_bank_for_{setting_provider}"
    s = await db.settings.find_one(
        {"user_id": uid},
        {"_id": 0, setting_key: 1},
    ) or {}
    bid = s.get(setting_key)
    if not bid:
        return None, None
    acc = await db.accounts.find_one(
        {"user_id": uid, "id": bid},
        {"_id": 0, "id": 1, "name": 1},
    )
    if not acc:
        return None, None
    return acc["id"], acc.get("name")


async def _build_bnpl_periods(
    db, uid: str, provider: str,
    date_from: Optional[str], date_to: Optional[str],
) -> tuple[list[dict], dict]:
    """Return (period_rows, rules_snapshot) for tamara/tabby.

    When a ``provider_invoice_calendar`` exists for this provider we
    walk the calendar entries one-by-one and call
    ``compute_settlement_for_provider(date_from=period_start,
    date_to=period_end)``.  This guarantees that Phase 2B-generated
    invoices use the SAME real invoice dates surfaced by the Dry-Run
    panel (e.g. Tamara 23/05, 30/05, 06/06, …).

    When no calendar exists we fall back to the legacy
    ``compute_weekly_settlements`` (weekday-based weekly cycle from
    ``bnpl_settings``).  All rules (commission, VAT, settlement fee)
    still come from ``_merchant_fee_rates`` — never hard-coded.
    """
    from bnpl.settlements_service import (
        compute_settlement_for_provider, compute_weekly_settlements,
        _merchant_fee_rates,
    )
    from provider_invoice_calendar import get_calendar as _cal

    rules = await _merchant_fee_rates(db, uid, provider)
    calendar = await _cal(
        db, uid, provider,
        from_date=date_from, to_date=date_to,
    )

    if calendar:
        rows: list[dict] = []
        for idx, c in enumerate(calendar, start=1):
            s = await compute_settlement_for_provider(
                db, uid, provider,
                c["period_start"], c["period_end"],
            )
            t = s.get("totals", {}) or {}
            rows.append({
                "invoice_no":             idx,
                "from":                   c["period_start"],
                "to":                     c["period_end"],
                "issue_date":             c["invoice_date"],
                "expected_transfer_date": c["expected_transfer_date"],
                "transactions_count":     t.get("transactions_count", 0),
                "refunds_count":          t.get("refunds_count", 0),
                "gross_sales":            t.get("gross_sales", 0),
                "total_refunds":          t.get("total_refunds", 0),
                "net_sales":              t.get("net_sales", 0),
                "commission":             t.get("commission", 0),
                "commission_vat":         t.get("commission_vat", 0),
                "settlement_fee":         t.get("settlement_fee", 0),
                "settlement_fee_vat":     t.get("settlement_fee_vat", 0),
                "net_payable":            t.get("net_payable", 0),
                "data_source":            s.get("data_source", "computed"),
            })
        # Mark rules snapshot with calendar provenance.
        rules = {**rules,
                 "calendar_source": "provider_invoice_calendar",
                 "calendar_entries": len(calendar)}
        return rows, rules

    # Legacy weekday cycle.
    rows = await compute_weekly_settlements(
        db, uid, provider, date_from, date_to,
    )
    return rows or [], rules


async def _build_salla_periods(
    db, uid: str, date_from: Optional[str], date_to: Optional[str],
) -> tuple[list[dict], dict]:
    """For Salla we group existing settlement_entries by
    ``settlement_reference`` — these ARE the merchant's invoices as
    Salla itself defines them.  We never invent a Salla rule."""
    match: dict[str, Any] = {"user_id": uid, "provider": "salla"}
    if date_from or date_to:
        rng: dict[str, str] = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        match["settlement_date"] = rng

    rows: list[dict] = []
    async for r in db.settlement_entries.aggregate([
        {"$match": match},
        {"$group": {
            "_id": "$settlement_reference",
            "orders_count":  {"$sum": 1},
            "gross":         {"$sum": {"$ifNull":
                                          ["$actual_gross_amount", 0]}},
            "refunds":       {"$sum": {"$ifNull":
                                          ["$actual_refund_amount", 0]}},
            "fee":           {"$sum": {"$ifNull":
                                          ["$actual_payment_fee", 0]}},
            "vat":           {"$sum": {"$ifNull":
                                          ["$actual_payment_vat", 0]}},
            "net":           {"$sum": {"$ifNull":
                                          ["$actual_net_amount", 0]}},
            "min_date":      {"$min": "$settlement_date"},
            "max_date":      {"$max": "$settlement_date"},
        }},
        {"$sort": {"min_date": 1}},
    ]):
        rows.append({
            "settlement_reference": r["_id"],
            "from":           r.get("min_date"),
            "to":             r.get("max_date"),
            "issue_date":     r.get("max_date"),
            "expected_transfer_date": r.get("max_date"),
            "transactions_count": r["orders_count"],
            "refunds_count":  0,
            "gross_sales":    _r(r.get("gross")),
            "total_refunds":  _r(r.get("refunds")),
            "net_sales":      _r((r.get("gross") or 0) - (r.get("refunds") or 0)),
            "commission":     _r(r.get("fee")),
            "commission_vat": _r(r.get("vat")),
            "settlement_fee": 0.0,
            "settlement_fee_vat": 0.0,
            "net_payable":    _r(r.get("net")),
            "data_source":    "settlement_entries",
        })

    rules_snapshot = {
        "fee_source": "settlement_entries",  # rates already embedded
                                              # per-row by Salla
        "note": "Salla rules read directly from settlement_entries; "
                "no central percentage is applied.",
    }
    return rows, rules_snapshot


async def _build_emkan_periods(
    db, uid: str, date_from: Optional[str], date_to: Optional[str],
) -> tuple[list[dict], dict]:
    """Build one period per unique uploaded Emkan settlement report."""
    from bnpl.settlements_service import (
        _merchant_fee_rates,
        compute_weekly_settlements,
    )

    rules = await _merchant_fee_rates(db, uid, "emkan")
    rules = {
        **rules,
        "cycle_source": "provider_statement_date",
        "cycle_verified": False,
        "evidence_version": "emkan-statements-2026-08-v1",
    }
    rows = await compute_weekly_settlements(
        db, uid, "emkan", date_from, date_to,
    )
    return rows, rules


async def generate_for_provider(
    db, uid: str, user: dict, provider: str,
    date_from: Optional[str], date_to: Optional[str],
    *, dry_run: bool = False,
) -> dict:
    """Generate periods / invoices / expected transfers for one
    provider over ``[date_from, date_to]``.

    Returns a summary dict.  When ``dry_run`` is true, NO documents
    are persisted — the caller can inspect what would be inserted.

    Idempotent: if a period already exists for
    ``(user_id, provider, period_from, period_to)``, the existing
    docs are reused — no new ids.
    """
    if provider in ("tamara", "tabby"):
        rows, rules = await _build_bnpl_periods(
            db, uid, provider, date_from, date_to)
        cycle_kind = "weekly_bnpl"
    elif provider in ("imkan", "emkan"):
        rows, rules = await _build_emkan_periods(
            db, uid, date_from, date_to)
        cycle_kind = "statement_date_bnpl"
    elif provider == "salla":
        rows, rules = await _build_salla_periods(
            db, uid, date_from, date_to)
        cycle_kind = "settlement_entries"
    else:
        return {
            "provider": provider,
            "rule_source_missing": True,
            "note": (
                f"المزوّد '{provider}' لا يوجد له مصدر قواعد مركزي "
                "(commission/VAT). أضف إعداداته في صفحة BNPL أو في "
                "settings.payment_methods قبل التوليد."
            ),
            "generated": {
                "periods": 0, "invoices": 0, "expected_transfers": 0,
            },
        }

    bank_id, bank_name = await _resolve_default_bank(db, uid, provider)

    counts = {
        "periods_new": 0, "periods_reused": 0,
        "invoices_new": 0, "invoices_reused": 0,
        "expected_transfers_new": 0, "expected_transfers_reused": 0,
        "skipped_zero_amount": 0,
    }
    created_invoice_ids: list[str] = []

    for r in rows:
        period_from = r.get("from")
        period_to   = r.get("to")
        if not period_from or not period_to:
            continue
        net_payable = _r(r.get("net_payable"))
        # We still record zero-amount periods so the merchant sees
        # the audit trail, but skip the expected_transfer document.

        existing_period = await db.settlement_periods.find_one(
            {"user_id": uid, "provider": provider,
             "period_from": period_from, "period_to": period_to},
            {"_id": 0},
        )
        if dry_run:
            counts["periods_new" if not existing_period
                   else "periods_reused"] += 1
            counts["invoices_new" if not existing_period
                   else "invoices_reused"] += 1
            if net_payable > 0:
                counts["expected_transfers_new" if not existing_period
                       else "expected_transfers_reused"] += 1
            else:
                counts["skipped_zero_amount"] += 1
            continue

        # ── Create / fetch period ─────────────────────────────
        if existing_period:
            period_id = existing_period["id"]
            counts["periods_reused"] += 1
        else:
            period_id = str(uuid.uuid4())
            period_doc = {
                "id":              period_id,
                "user_id":         uid,
                "provider":        provider,
                "period_from":     period_from,
                "period_to":       period_to,
                "issue_date":      r.get("issue_date"),
                "cycle_kind":      cycle_kind,
                "status":          "invoiced",
                "invoice_count":   0,
                "rules_snapshot":  rules,
                "totals": {
                    "transactions_count":  int(r.get("transactions_count") or 0),
                    "refunds_count":       int(r.get("refunds_count") or 0),
                    "gross_sales":         _r(r.get("gross_sales")),
                    "total_refunds":       _r(r.get("total_refunds")),
                    "net_sales":           _r(r.get("net_sales")),
                    "commission":          _r(r.get("commission")),
                    "commission_vat":      _r(r.get("commission_vat")),
                    "settlement_fee":      _r(r.get("settlement_fee")),
                    "settlement_fee_vat":  _r(r.get("settlement_fee_vat")),
                    "net_payable":         net_payable,
                },
                "generated_at":     _now(),
                "generated_by":     user.get("id"),
                "source":           "settlement_engine_v1",
            }
            await db.settlement_periods.insert_one(period_doc)
            counts["periods_new"] += 1

        # ── Create / fetch invoice ───────────────────────────
        existing_inv = await db.settlement_invoices.find_one(
            {"user_id": uid, "settlement_period_id": period_id},
            {"_id": 0, "id": 1},
        )
        if existing_inv:
            invoice_id = existing_inv["id"]
            counts["invoices_reused"] += 1
        else:
            invoice_id = str(uuid.uuid4())
            invoice_status = "generated"
            invoice_doc = {
                "id":                  invoice_id,
                "user_id":             uid,
                "provider_name":       provider,
                "settlement_period_id": period_id,
                "invoice_no":          int(r.get("invoice_no") or 1),
                "invoice_date":        r.get("issue_date") or period_to,
                "period_from":         period_from,
                "period_to":           period_to,
                "status":              invoice_status,
                "source_orders_count": int(r.get("transactions_count") or 0),
                "refunds_count":       int(r.get("refunds_count") or 0),
                "gross_sales":         _r(r.get("gross_sales")),
                "total_refunds":       _r(r.get("total_refunds")),
                "net_sales":           _r(r.get("net_sales")),
                "commission":          _r(r.get("commission")),
                "commission_vat":      _r(r.get("commission_vat")),
                "settlement_fee":      _r(r.get("settlement_fee")),
                "settlement_fee_vat":  _r(r.get("settlement_fee_vat")),
                "expected_transfer_amount": net_payable,
                "rules_snapshot":      rules,
                "data_source":         r.get("data_source", "computed"),
                "expected_transfer_id": None,
                "bank_transfer_review_id": None,
                "ledger_txn_group_id":  None,
                "generated_at":        _now(),
                "generated_by":        user.get("id"),
                "created_by":          user.get("id"),
                "updated_at":          _now(),
            }
            # Provider-specific references for audit
            if provider == "salla":
                invoice_doc["provider_reference"] = r.get(
                    "settlement_reference")
            elif provider in ("imkan", "emkan"):
                invoice_doc["provider_reference"] = r.get(
                    "settlement_reference"
                )
            await db.settlement_invoices.insert_one(invoice_doc)
            await db.settlement_periods.update_one(
                {"id": period_id},
                {"$inc": {"invoice_count": 1},
                 "$set": {"updated_at": _now()}},
            )
            counts["invoices_new"] += 1
            created_invoice_ids.append(invoice_id)

        # ── Create / fetch expected_transfer ─────────────────
        if net_payable <= 0:
            counts["skipped_zero_amount"] += 1
            continue
        existing_xfer = await db.expected_transfers.find_one(
            {"user_id": uid, "settlement_invoice_id": invoice_id},
            {"_id": 0, "id": 1},
        )
        if existing_xfer:
            xfer_id = existing_xfer["id"]
            counts["expected_transfers_reused"] += 1
        else:
            xfer_id = str(uuid.uuid4())
            xfer_doc = {
                "id":                    xfer_id,
                "user_id":               uid,
                "provider_name":         provider,
                "settlement_invoice_id": invoice_id,
                "settlement_period_id":  period_id,
                "expected_transfer_date": (
                    r.get("expected_transfer_date")
                    or r.get("issue_date")
                    or period_to
                ),
                "expected_amount":       net_payable,
                "target_bank_id":        bank_id,
                "target_bank_name":      bank_name,
                "bank_transfer_review_id": None,
                "status":                "pending",
                "generated_at":          _now(),
                "generated_by":          user.get("id"),
                "updated_at":            _now(),
            }
            await db.expected_transfers.insert_one(xfer_doc)
            await db.settlement_invoices.update_one(
                {"id": invoice_id},
                {"$set": {
                    "expected_transfer_id": xfer_id,
                    "status": "waiting_transfer",
                    "updated_at": _now(),
                }},
            )
            counts["expected_transfers_new"] += 1

    return {
        "provider":              provider,
        "period_from":           date_from,
        "period_to":             date_to,
        "rows_considered":       len(rows),
        "counts":                counts,
        "default_bank": {
            "id":   bank_id, "name": bank_name,
            "configured": bool(bank_id),
        },
        "rules_snapshot":        rules,
        "dry_run":               dry_run,
        "created_invoice_ids":   created_invoice_ids,
    }


async def cancel_invoice(
    db, uid: str, user: dict, invoice_id: str, reason: str,
) -> dict:
    """Cancel a generated invoice (and its expected_transfer).
    Pure state change — no GL touch."""
    inv = await db.settlement_invoices.find_one(
        {"id": invoice_id, "user_id": uid}, {"_id": 0},
    )
    if not inv:
        return {"error": "not_found"}
    if inv["status"] in ("confirmed", "confirmed_with_difference"):
        return {
            "error": "cannot_cancel_after_confirm",
            "status": inv["status"],
        }
    now = _now()
    await db.settlement_invoices.update_one(
        {"id": invoice_id, "user_id": uid},
        {"$set": {
            "status": "cancelled",
            "cancelled_at":      now,
            "cancelled_by":      user.get("id"),
            "cancellation_note": reason or "",
            "updated_at":        now,
        }},
    )
    if inv.get("expected_transfer_id"):
        await db.expected_transfers.update_one(
            {"id": inv["expected_transfer_id"], "user_id": uid,
             "status": {"$in": ["pending", "linked_to_review"]}},
            {"$set": {"status": "cancelled", "updated_at": now}},
        )
    return {"ok": True, "invoice_id": invoice_id, "status": "cancelled"}
