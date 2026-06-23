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

    Iter-251 v8 — Real-world flexibility:
      • Match by ``metadata.provider`` (string) when ``entity_id``
        is a UUID pointing at a payment_gateway document.
      • De-duplicate by ``txn_group_id``.
      • Period extraction priority:
          1. ``metadata.period_from`` / ``period_to``
          2. Nested ``metadata.period.{from,to}``
          3. Regex pull from ``metadata.settlement_reference``
          4. 7-day window ending on ``settlement_date``.
      • **Cross-row inference**: when periods are extracted from
        references and ``period_to`` is missing, derive it as
        ``next_period_from − 1 day`` so consecutive periods are
        contiguous and non-overlapping.  For the LAST registered
        settlement we infer width from the gap to the previous one
        (or fall back to settlement_date / placeholder).
    """
    pipeline = [
        {"$match": {
            "user_id":     uid,
            "entry_type":  "bnpl_settlement",
            "status":      "posted",
        }},
        {"$sort": {"posted_at": 1}},
    ]
    raw: list[dict] = []
    seen_groups: set[str] = set()
    async for e in db.general_ledger.aggregate(pipeline):
        meta = e.get("metadata") or {}
        prov_l = provider.lower()
        if not (
            (e.get("entity_id") or "").lower() == prov_l
            or (meta.get("provider")    or "").lower() == prov_l
            or (meta.get("provider_id") or "").lower() == prov_l
        ):
            continue
        grp = e.get("txn_group_id") or e.get("entry_no")
        if not grp or grp in seen_groups:
            continue
        seen_groups.add(grp)

        p_from = (meta.get("period_from") or "")[:10]
        p_to   = (meta.get("period_to")   or "")[:10]
        if not (p_from and p_to):
            nested = meta.get("period") or {}
            p_from = p_from or (nested.get("from") or "")[:10]
            p_to   = p_to   or (nested.get("to")   or "")[:10]
        ref = meta.get("settlement_reference") or ""
        sd = (meta.get("settlement_date") or "")[:10]
        if not p_from and ref:
            import re as _re
            m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", ref)
            if m:
                p_from = m.group(0)
            else:
                m2 = _re.search(r"(\d{4})(\d{2})(\d{2})", ref)
                if m2:
                    p_from = (f"{m2.group(1)}-{m2.group(2)}"
                              f"-{m2.group(3)}")
        if not p_from and sd:
            try:
                p_from = (date.fromisoformat(sd) -
                          timedelta(days=6)).isoformat()
            except Exception:
                pass
        if not p_from:
            continue
        had_period_to = bool(p_to)
        raw.append({
            "p_from":               p_from,
            "p_to":                 p_to,
            "had_period_to":        had_period_to,
            "ref":                  ref or None,
            "sd":                   sd,
            "txn_group_id":         grp,
            "entity_id":            e.get("entity_id"),
            "side":                 e.get("side"),
            "meta_provider":        meta.get("provider"),
            "transferred_amount":   meta.get("transferred_amount"),
            "had_period_metadata":  bool(
                meta.get("period_from")
                or (meta.get("period") or {}).get("from")),
        })

    raw.sort(key=lambda x: x["p_from"])

    # Cross-row inference of missing period_to to ensure contiguous
    # non-overlapping periods.
    for i, r in enumerate(raw):
        if r["had_period_to"]:
            continue
        # Use next row's period_from − 1 day when available.
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        if nxt:
            try:
                r["p_to"] = _add_days(nxt["p_from"], -1)
                continue
            except Exception:
                pass
        # Last row: infer from previous gap (cycle width).
        prv = raw[i - 1] if i > 0 else None
        if prv:
            try:
                gap_days = (date.fromisoformat(r["p_from"]) -
                            date.fromisoformat(prv["p_from"])).days
                if gap_days > 0:
                    r["p_to"] = _add_days(r["p_from"], gap_days - 1)
                    continue
            except Exception:
                pass
        # Fall back to settlement_date or +6 placeholder.
        r["p_to"] = (r["sd"] if r["sd"] and r["sd"] >= r["p_from"]
                     else _add_days(r["p_from"], 6))

    out: list[dict] = []
    seen_periods: set[tuple[str, str]] = set()
    for r in raw:
        if (r["p_from"], r["p_to"]) in seen_periods:
            continue
        seen_periods.add((r["p_from"], r["p_to"]))
        out.append({
            "invoice_date":            r["p_from"],
            "period_start":            r["p_from"],
            "period_end":              r["p_to"],
            "expected_transfer_date":  r["sd"] or r["p_to"],
            "source":                  "registered_settlement",
            "source_ref":              r["ref"],
            "settlement_dates":        [r["sd"]] if r["sd"] else [],
            "orders_count_hint":       None,
            "net_hint":                round(float(
                r["transferred_amount"] or 0), 2),
            "layout":                  "registered",
            "txn_group_id":            r["txn_group_id"],
            "_raw_match": {
                "entity_id":     r["entity_id"],
                "side":          r["side"],
                "meta_provider": r["meta_provider"],
                "had_period_metadata": r["had_period_metadata"],
            },
        })
    return out


async def extract_calendar_from_settlement_entries(
    db, uid: str, provider: str,
    *, template: Optional[dict] = None,
) -> list[dict]:
    """Group ``settlement_entries`` by ``settlement_date`` (the day
    each row was booked by the provider) to discover real invoice
    dates.

    Iter-251 v7 — When ``template`` is provided (learned from
    registered settlements), it OVERRIDES the static
    ``_PERIOD_LAYOUTS`` for this provider.  The template captures
    the merchant's actual settlement cycle (anchor weekday + period
    width) so derived rows use the SAME boundaries as registered
    ones.

    Template schema::

        {
          "anchor_weekday":  int  # weekday of period_from (0=Mon)
          "period_width":    int  # period_to - period_from + 1
        }

    Period boundaries (without template):
      * ``invoice_as_start``: period_start = invoice_date,
        period_end = invoice_date + 6  (Tamara: Sat → Fri).
      * ``invoice_as_end``:   period_end = invoice_date,
        period_start = previous_invoice_date + 1
        (or invoice_date − 6 for the first one).
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

    # Iter-251 v7 — Template override (learned from registered
    # settlements).  Anchors the derived period to the merchant's
    # actual settlement cycle.
    if template and template.get("anchor_weekday") is not None \
       and template.get("period_width"):
        anchor_wd: int = int(template["anchor_weekday"])
        width:     int = int(template["period_width"])  # inclusive
        for row in rows:
            sd = row["settlement_date"]
            # Find the period_from = most recent occurrence of
            # anchor_weekday that is ≤ settlement_date.
            period_start = _snap_to_weekday_backward(sd, anchor_wd)
            period_end   = _add_days(period_start, width - 1)
            # If settlement_date falls AFTER period_end (e.g. invoice
            # issued a day late), shift one cycle forward so it lands
            # inside the period.
            if sd > period_end:
                period_start = _add_days(period_start, width)
                period_end   = _add_days(period_start, width - 1)
            expected_transfer_date = sd
            ref = row["refs"][0] if row["refs"] else None
            out.append({
                "invoice_date":            period_start,
                "period_start":            period_start,
                "period_end":              period_end,
                "expected_transfer_date":  expected_transfer_date,
                "source":                  "settlement_entries",
                "source_ref":              ref,
                "settlement_dates":        row.get("raw_dates", [sd]),
                "orders_count_hint":       row["orders_count"],
                "net_hint":                row["net"],
                "layout":                  "templated_from_registered",
                "template_used":           {
                    "anchor_weekday": anchor_wd,
                    "period_width":   width,
                },
            })
        return out

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

    # Iter-251 v8 — Learn the cycle template from ALL registered
    # settlements using median statistics (robust to outliers /
    # inconsistent metadata).  If widths vary by > 1 day across
    # registrations, surface a warning so the merchant can review.
    template: Optional[dict] = None
    template_warning: Optional[str] = None
    if registered:
        widths: list[int] = []
        anchors: list[int] = []
        for r in registered:
            try:
                ps = date.fromisoformat(r["period_start"])
                pe = date.fromisoformat(r["period_end"])
                w  = (pe - ps).days + 1
                if 1 <= w <= 14:  # sanity guard
                    widths.append(w)
                    anchors.append(ps.weekday())
            except Exception:
                continue
        if widths:
            widths_sorted = sorted(widths)
            median_width = widths_sorted[len(widths_sorted) // 2]
            # Most common anchor weekday
            from collections import Counter
            anchor_mode = Counter(anchors).most_common(1)[0][0]
            template = {
                "anchor_weekday":  anchor_mode,
                "period_width":    median_width,
                "registered_count": len(widths),
                "all_widths":      widths_sorted,
                "all_anchors":     sorted(set(anchors)),
            }
            if max(widths) - min(widths) > 1:
                template_warning = (
                    f"⚠️ التسويات المسجَّلة ذات أطوال فترة متضاربة "
                    f"({min(widths)} – {max(widths)} يوماً). تم اعتماد "
                    f"الوسيط = {median_width} يوماً. راجع تقرير التدقيق."
                )
            if len(set(anchors)) > 1:
                template_warning = (template_warning or "") + (
                    f" · أيام بداية مختلفة بين التسويات: "
                    f"{sorted(set(anchors))}"
                )

    derived = await extract_calendar_from_settlement_entries(
        db, uid, provider, template=template)

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

    # Iter-251 v7 — Clean up stale non-manual rows whose period
    # now overlaps a registered-settlement period (so the rebuild
    # truly converges).  Without this, an older row created when
    # `invoice_date = settlement_date` (e.g. 2026-05-04 with period
    # 2026-04-28 → 2026-05-04) would stay in the DB even after a
    # new registered-settlement row with `invoice_date = period_from`
    # (e.g. 2026-04-27 with period 2026-04-27 → 2026-05-04) is added.
    deleted_stale = 0
    if not dry_run and registered:
        async for c in db.provider_invoice_calendar.find(
            {"user_id": uid, "provider": provider,
             "source": {"$ne": "manual"}},
            {"_id": 0, "id": 1, "period_start": 1, "period_end": 1,
             "invoice_date": 1, "source": 1},
        ):
            cf = c.get("period_start") or ""
            ct = c.get("period_end")   or ""
            inv = c.get("invoice_date")
            # If this calendar row overlaps a registered period AND
            # is NOT itself a registered_settlement covering the
            # SAME period, drop it.
            for rf, rt in reg_spans:
                if not (ct < rf or cf > rt):
                    # Keep only if THIS row is the registered one
                    if cf == rf and ct == rt and inv == rf:
                        break
                    await db.provider_invoice_calendar.delete_one(
                        {"id": c["id"], "user_id": uid})
                    deleted_stale += 1
                    break

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
        "provider":           provider,
        "extracted":          len(merged),
        "from_registered":    from_registered,
        "from_derived":       from_derived,
        "inserted":           inserted,
        "updated":            updated,
        "skipped_manual":     skipped_manual,
        "deleted_stale":      deleted_stale,
        "template":           template,
        "template_warning":   template_warning,
        "dry_run":            dry_run,
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
    return {k: v for k, v in doc.items() if k != "_id"}


async def delete_entry(db, uid: str, entry_id: str) -> bool:
    res = await db.provider_invoice_calendar.delete_one(
        {"id": entry_id, "user_id": uid})
    return res.deleted_count > 0
