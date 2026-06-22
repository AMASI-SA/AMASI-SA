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


# Iter-251 · Phase 2A.5 v3 — Snap-to-weekday.
#
# Some providers (Tamara) ALWAYS issue invoices on a specific
# weekday (Saturday for Tamara).  But imports often shift the date
# by one day because the source file was timestamped at end-of-day
# in Saudi time (UTC+3) and stored as UTC — so a Saturday invoice
# lands in the DB as Sunday.
#
# When the layout is "invoice_as_start", we snap `settlement_date`
# BACKWARD to the most recent occurrence of the target weekday.
# Overridable via ``settings.calendar_snap_to_weekday_<provider>``:
#     null   → disabled
#     0..6   → Monday..Sunday (Python weekday convention)
_DEFAULT_SNAP_WEEKDAY = {
    "tamara": 5,  # Saturday
}


async def _snap_weekday(
    db, uid: str, provider: str,
) -> Optional[int]:
    s = await db.settings.find_one(
        {"user_id": uid},
        {"_id": 0, f"calendar_snap_to_weekday_{provider}": 1},
    ) or {}
    raw = s.get(f"calendar_snap_to_weekday_{provider}", "__UNSET__")
    if raw == "__UNSET__":
        return _DEFAULT_SNAP_WEEKDAY.get(provider)
    if raw is None:
        return None
    try:
        v = int(raw)
        return v if 0 <= v <= 6 else None
    except Exception:
        return _DEFAULT_SNAP_WEEKDAY.get(provider)


def _snap_to_weekday_backward(d: str, target: int) -> str:
    """Return the most recent date ≤ ``d`` whose weekday equals
    ``target`` (Python convention, Monday=0)."""
    dt = date.fromisoformat(d)
    delta = (dt.weekday() - target) % 7
    return (dt - timedelta(days=delta)).isoformat()


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


async def extract_calendar_from_registered_settlements(
    db, uid: str, provider: str,
) -> list[dict]:
    """Read calendar entries directly from registered BNPL
    settlements stored in ``general_ledger``.

    Each registered settlement has the **exact** ``period_from`` and
    ``period_to`` that was used in ``compute_settlement_for_provider``
    when the merchant clicked "Register" on the BNPL settlements
    page.  Those dates are the source of truth — Dry-Run periods
    MUST match them so the merchant sees 1:1 parity with what they
    actually saved.

    Returns ascending list of:
        {invoice_date, period_start, period_end,
         expected_transfer_date, source, source_ref,
         settlement_dates, orders_count_hint, net_hint,
         layout}
    """
    pipeline = [
        {"$match": {
            "user_id":   uid,
            "entry_type": "bnpl_settlement",
            "status":    "posted",
            "side":      "credit",
            "entity_type": "payment_gateway",
            "entity_id":  provider,
            "metadata.period_from": {"$nin": [None, ""]},
            "metadata.period_to":   {"$nin": [None, ""]},
        }},
        {"$sort": {"metadata.period_from": 1}},
    ]
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    async for e in db.general_ledger.aggregate(pipeline):
        meta = e.get("metadata") or {}
        p_from = (meta.get("period_from") or "")[:10]
        p_to   = (meta.get("period_to")   or "")[:10]
        if not p_from or not p_to:
            continue
        if (p_from, p_to) in seen:
            continue
        seen.add((p_from, p_to))
        sd = (meta.get("settlement_date") or p_to)[:10]
        ref = meta.get("settlement_reference")
        out.append({
            "invoice_date":            p_from,  # matches BNPL page's
                                                  # "from" anchor
            "period_start":            p_from,
            "period_end":              p_to,
            "expected_transfer_date":  sd,
            "source":                  "registered_settlement",
            "source_ref":              ref,
            "settlement_dates":        [sd] if sd else [],
            "orders_count_hint":       None,
            "net_hint":                round(float(
                meta.get("transferred_amount") or 0), 2),
            "layout":                  "registered",  # provenance flag
        })
    return out


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

    # Iter-251 v3 — snap raw settlement_date to the configured
    # weekday (e.g. Saturday for Tamara) and merge rows that collide
    # after snapping (handles the +1d timezone drift).
    snap_target = None
    if layout == "invoice_as_start":
        snap_target = await _snap_weekday(db, uid, provider)
    if snap_target is not None:
        merged: dict[str, dict] = {}
        for row in rows:
            snapped = _snap_to_weekday_backward(
                row["settlement_date"], snap_target)
            m = merged.setdefault(snapped, {
                "settlement_date":  snapped,
                "orders_count":     0,
                "net":              0.0,
                "refs":             [],
                "raw_dates":        [],
            })
            m["orders_count"] += row["orders_count"]
            m["net"]          += row["net"]
            m["refs"]         += row["refs"]
            if row["settlement_date"] not in m["raw_dates"]:
                m["raw_dates"].append(row["settlement_date"])
        rows = [
            {**v, "net": round(v["net"], 2)}
            for v in sorted(merged.values(),
                            key=lambda x: x["settlement_date"])
        ]
    else:
        for row in rows:
            row.setdefault("raw_dates", [row["settlement_date"]])

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
                "settlement_dates":        row.get("raw_dates", [inv]),
                "orders_count_hint":       row["orders_count"],
                "net_hint":                row["net"],
                "layout":                  layout,
                "snap_applied":            snap_target is not None
                                            and row.get("raw_dates",
                                                       [inv]) != [inv],
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
    """Re-extract calendar entries and upsert into
    ``provider_invoice_calendar``.

    Two-pass extraction (registered settlements take priority):
      1. Registered settlements in ``general_ledger`` — these carry
         the EXACT ``period_from`` / ``period_to`` that the merchant
         used when registering each settlement on the BNPL page.
         Periods from this source override any derivation.
      2. ``settlement_entries`` — derives invoice dates for windows
         not yet registered (e.g. the latest cycle that's still
         pending registration), using ``_PERIOD_LAYOUTS``.

    Idempotent: keyed on (user_id, provider, invoice_date).  Manual
    rows are left untouched.
    """
    registered = await extract_calendar_from_registered_settlements(
        db, uid, provider)
    derived = await extract_calendar_from_settlement_entries(
        db, uid, provider)

    # Build a span set from registered settlements so we can skip
    # any derived row whose period overlaps a registered one — the
    # registered ones are authoritative.
    reg_spans = [
        (e["period_start"], e["period_end"]) for e in registered
    ]

    def _overlaps(a_start: str, a_end: str) -> bool:
        for b_start, b_end in reg_spans:
            if not (a_end < b_start or a_start > b_end):
                return True
        return False

    merged: list[dict] = []
    seen_invoice_dates: set[str] = set()
    for e in registered:
        merged.append(e)
        seen_invoice_dates.add(e["invoice_date"])
    for e in derived:
        if e["invoice_date"] in seen_invoice_dates:
            continue
        if _overlaps(e["period_start"], e["period_end"]):
            continue
        merged.append(e)
        seen_invoice_dates.add(e["invoice_date"])
    merged.sort(key=lambda x: x["period_start"])

    inserted, updated, skipped_manual = 0, 0, 0
    from_registered, from_derived = 0, 0
    for e in merged:
        if e["source"] == "registered_settlement":
            from_registered += 1
        else:
            from_derived += 1
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
                    "source":                 e["source"],
                    "source_ref":             e["source_ref"],
                    "settlement_dates":       e["settlement_dates"],
                    "layout":                 e.get("layout"),
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
                "layout":      e.get("layout"),
                "created_by":  user.get("id"),
                "created_at":  _now(),
                "updated_at":  _now(),
            }
            await db.provider_invoice_calendar.insert_one(doc)
            inserted += 1
    return {
        "provider":         provider,
        "extracted":        len(merged),
        "from_registered":  from_registered,
        "from_derived":     from_derived,
        "inserted":         inserted,
        "updated":          updated,
        "skipped_manual":   skipped_manual,
        "dry_run":          dry_run,
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
