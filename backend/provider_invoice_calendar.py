"""Iter-251 · Phase 2A.5 — Provider Invoice Calendar.

Provides the **real** invoice issuance dates for each provider —
extracted from the merchant's actual ``settlement_entries`` data
(Tamara/Tabby/Salla settlement files), or hand-entered for forecasting.

Why this matters
================
Before this layer, the Settlement Engine Dry-Run divided time into
arbitrary ISO-week buckets starting from order dates.  That works
for a textbook weekly cycle but does **not** match Tamara's actual
invoice calendar, which is anchored to specific dates the provider
chooses (e.g. 23/05, 30/05, 06/06, 13/06, 20/06).

A 1-day drift in period boundaries shifts which orders fall in
which invoice — corrupting:
  • invoice_date
  • period_start / period_end
  • orders_count per invoice
  • expected transfer amount

This module fixes that by giving each provider its own canonical
calendar.

Collection: ``provider_invoice_calendar``
-----------------------------------------
{
    id, user_id, provider,
    invoice_date:           YYYY-MM-DD,
    period_start:           YYYY-MM-DD,
    period_end:             YYYY-MM-DD,
    expected_transfer_date: YYYY-MM-DD,
    source:                 enum{settlement_entries, manual},
    source_ref:             str | None  # settlement_reference, etc.
    settlement_dates:       list[str]   # raw entry settlement_dates
                                         # for audit
    created_at, updated_at, created_by
}

Unique index on (user_id, provider, invoice_date).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Iter-251 · Phase 2A.5 v2 — Period-layout convention per provider.
#
#  • "invoice_as_start" — issue_date = FIRST day of the cycle.
#       Tamara reality: invoice issued on Saturday covers
#       Saturday → next Friday (7 days).
#  • "invoice_as_end"   — issue_date = LAST day of the cycle.
#       Older convention; kept as fallback for providers we haven't
#       confirmed yet.
#
# Overridable per-merchant via
# ``settings.calendar_period_layout_<provider>``.
_PERIOD_LAYOUTS = {
    "tamara": "invoice_as_start",
    "tabby":  "invoice_as_end",
    "imkan":  "invoice_as_end",
    "salla":  "invoice_as_end",
}


async def _period_layout(db, uid: str, provider: str) -> str:
    s = await db.settings.find_one(
        {"user_id": uid},
        {"_id": 0, f"calendar_period_layout_{provider}": 1},
    ) or {}
    v = s.get(f"calendar_period_layout_{provider}")
    if v in ("invoice_as_start", "invoice_as_end"):
        return v
    return _PERIOD_LAYOUTS.get(provider, "invoice_as_end")


# Tamara/Tabby transfer typically lands 1–3 days after the invoice
# date.  Default offset depends on the period layout (because the
# starting reference point differs):
#   * "invoice_as_start": transfer ≈ invoice + 9 (next Mon after Fri end)
#   * "invoice_as_end":   transfer ≈ invoice + 2 (Mon after Sat issue)
_DEFAULT_TRANSFER_OFFSET = {
    ("tamara", "invoice_as_start"): 9,
    ("tamara", "invoice_as_end"):   2,
    ("tabby",  "invoice_as_end"):   1,
    ("imkan",  "invoice_as_end"):   1,
    ("salla",  "invoice_as_end"):   0,
}


async def _transfer_offset(
    db, uid: str, provider: str, layout: str,
) -> int:
    """Allow per-merchant overrides via
    ``settings.calendar_transfer_offset_<provider>``."""
    s = await db.settings.find_one(
        {"user_id": uid},
        {"_id": 0, f"calendar_transfer_offset_{provider}": 1},
    ) or {}
    v = s.get(f"calendar_transfer_offset_{provider}")
    if v is None:
        return _DEFAULT_TRANSFER_OFFSET.get((provider, layout), 2)
    try:
        return max(0, int(v))
    except Exception:
        return _DEFAULT_TRANSFER_OFFSET.get((provider, layout), 2)


def _add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


async def extract_calendar_from_settlement_entries(
    db, uid: str, provider: str,
) -> list[dict]:
    """Group ``settlement_entries`` by ``settlement_date`` (the day
    each row was booked by the provider) to discover real invoice
    dates.

    Period boundaries depend on the provider's layout:
      * ``invoice_as_start``: period_start = invoice_date,
        period_end = invoice_date + 6  (Tamara: Sat → Fri).
      * ``invoice_as_end``:   period_end = invoice_date,
        period_start = previous_invoice_date + 1
        (or invoice_date − 6 for the first one).

    Returns ascending-ordered list of:
        {invoice_date, period_start, period_end,
         expected_transfer_date, source, source_ref,
         settlement_dates, orders_count_hint, net_hint,
         layout}
    """
    pipeline = [
        {"$match": {"user_id": uid, "provider": provider,
                    "settlement_date": {"$ne": None}}},
        {"$group": {
            "_id": "$settlement_date",
            "orders_count":  {"$sum": 1},
            "net":           {"$sum": {"$ifNull":
                                          ["$actual_net_amount", 0]}},
            "refs":          {"$addToSet": "$settlement_reference"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows: list[dict] = []
    async for r in db.settlement_entries.aggregate(pipeline):
        rows.append({
            "settlement_date": str(r["_id"])[:10],
            "orders_count":    int(r.get("orders_count") or 0),
            "net":             round(float(r.get("net") or 0), 2),
            "refs":            [x for x in (r.get("refs") or []) if x],
        })

    if not rows:
        return []

    layout = await _period_layout(db, uid, provider)
    offset = await _transfer_offset(db, uid, provider, layout)
    out: list[dict] = []

    if layout == "invoice_as_start":
        # invoice_date = first day of the 7-day cycle (Sat..Fri).
        for row in rows:
            inv = row["settlement_date"]
            period_start = inv
            period_end   = _add_days(inv, 6)
            expected_transfer_date = _add_days(inv, offset)
            ref = row["refs"][0] if row["refs"] else None
            out.append({
                "invoice_date":            inv,
                "period_start":            period_start,
                "period_end":              period_end,
                "expected_transfer_date":  expected_transfer_date,
                "source":                  "settlement_entries",
                "source_ref":              ref,
                "settlement_dates":        [inv],
                "orders_count_hint":       row["orders_count"],
                "net_hint":                row["net"],
                "layout":                  layout,
            })
        return out

    # Legacy: "invoice_as_end" — derive period_start by walking back
    # from invoice_date (or from the previous row + 1).
    prev_inv: Optional[str] = None
    for row in rows:
        inv = row["settlement_date"]
        if prev_inv:
            period_start = _add_days(prev_inv, 1)
        else:
            period_start = _add_days(inv, -6)
        period_end = inv
        expected_transfer_date = _add_days(inv, offset)
        ref = row["refs"][0] if row["refs"] else None
        out.append({
            "invoice_date":            inv,
            "period_start":            period_start,
            "period_end":              period_end,
            "expected_transfer_date":  expected_transfer_date,
            "source":                  "settlement_entries",
            "source_ref":              ref,
            "settlement_dates":        [inv],
            "orders_count_hint":       row["orders_count"],
            "net_hint":                row["net"],
            "layout":                  layout,
        })
        prev_inv = inv
    return out


async def rebuild_calendar(
    db, uid: str, user: dict, provider: str,
    *, dry_run: bool = False,
) -> dict:
    """Re-extract calendar entries from ``settlement_entries`` and
    upsert into ``provider_invoice_calendar``.

    Idempotent: keyed on (user_id, provider, invoice_date).  Existing
    rows have their period/transfer/refs **updated** to the latest
    extraction (so re-running after a new file import refreshes
    everything).  Manual rows (``source="manual"``) are left alone.
    """
    extracted = await extract_calendar_from_settlement_entries(
        db, uid, provider)
    inserted, updated, skipped_manual = 0, 0, 0
    for e in extracted:
        existing = await db.provider_invoice_calendar.find_one(
            {"user_id": uid, "provider": provider,
             "invoice_date": e["invoice_date"]},
            {"_id": 0},
        )
        if existing and existing.get("source") == "manual":
            skipped_manual += 1
            continue
        if dry_run:
            if existing:
                updated += 1
            else:
                inserted += 1
            continue
        if existing:
            await db.provider_invoice_calendar.update_one(
                {"id": existing["id"], "user_id": uid},
                {"$set": {
                    "period_start":           e["period_start"],
                    "period_end":             e["period_end"],
                    "expected_transfer_date": e["expected_transfer_date"],
                    "source":                 "settlement_entries",
                    "source_ref":             e["source_ref"],
                    "settlement_dates":       e["settlement_dates"],
                    "updated_at":             _now(),
                }},
            )
            updated += 1
        else:
            doc = {
                "id":          str(uuid.uuid4()),
                "user_id":     uid,
                "provider":    provider,
                **{k: e[k] for k in (
                    "invoice_date", "period_start", "period_end",
                    "expected_transfer_date", "source", "source_ref",
                    "settlement_dates",
                )},
                "created_by":  user.get("id"),
                "created_at":  _now(),
                "updated_at":  _now(),
            }
            await db.provider_invoice_calendar.insert_one(doc)
            inserted += 1
    return {
        "provider":       provider,
        "extracted":      len(extracted),
        "inserted":       inserted,
        "updated":        updated,
        "skipped_manual": skipped_manual,
        "dry_run":        dry_run,
    }


async def get_calendar(
    db, uid: str, provider: str,
    *, from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list[dict]:
    q: dict[str, Any] = {"user_id": uid, "provider": provider}
    if from_date or to_date:
        rng: dict[str, str] = {}
        if from_date:
            rng["$gte"] = from_date
        if to_date:
            rng["$lte"] = to_date
        q["invoice_date"] = rng
    items: list[dict] = []
    async for d in (db.provider_invoice_calendar
                      .find(q, {"_id": 0})
                      .sort([("invoice_date", 1)])):
        items.append(d)
    return items


async def upsert_manual_entry(
    db, uid: str, user: dict, provider: str,
    *, invoice_date: str, period_start: str, period_end: str,
    expected_transfer_date: str,
) -> dict:
    """Allow a merchant to add a forecasted invoice date manually
    (used to project future Tamara invoices that haven't generated a
    settlement file yet)."""
    for d in (invoice_date, period_start, period_end,
              expected_transfer_date):
        try:
            date.fromisoformat(d)
        except Exception:
            raise ValueError(f"تاريخ غير صالح: {d}")
    if period_start > period_end:
        raise ValueError("period_start يجب أن يكون قبل period_end")
    existing = await db.provider_invoice_calendar.find_one(
        {"user_id": uid, "provider": provider,
         "invoice_date": invoice_date},
        {"_id": 0},
    )
    now = _now()
    if existing:
        await db.provider_invoice_calendar.update_one(
            {"id": existing["id"], "user_id": uid},
            {"$set": {
                "period_start":           period_start,
                "period_end":             period_end,
                "expected_transfer_date": expected_transfer_date,
                "source":                 "manual",
                "updated_at":             now,
            }},
        )
        return {**existing, "source": "manual",
                "period_start": period_start,
                "period_end":   period_end,
                "expected_transfer_date": expected_transfer_date,
                "updated_at":   now}
    doc = {
        "id":                     str(uuid.uuid4()),
        "user_id":                uid,
        "provider":               provider,
        "invoice_date":           invoice_date,
        "period_start":           period_start,
        "period_end":             period_end,
        "expected_transfer_date": expected_transfer_date,
        "source":                 "manual",
        "source_ref":             None,
        "settlement_dates":       [],
        "created_by":             user.get("id"),
        "created_at":             now,
        "updated_at":             now,
    }
    await db.provider_invoice_calendar.insert_one(doc)
    return doc


async def delete_entry(db, uid: str, entry_id: str) -> bool:
    res = await db.provider_invoice_calendar.delete_one(
        {"id": entry_id, "user_id": uid})
    return res.deleted_count > 0
