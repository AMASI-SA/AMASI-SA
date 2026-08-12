"""Reconciliation v2 — Salla orders (unified_orders) ↔ local
`qoyod_invoices`.

User directive (2026-07-09): the reconciliation page is the SINGLE
source of truth for Mezan/قيود parity. The comparison is between:

    (A) Salla-side orders from `unified_orders` under the JWT tenant
        — same source as /orders — filtered by:
          • order_date >= 2026-07-01
          • order_status ∈ {completed / in_delivery / delivered}
    (B) Local `qoyod_invoices` — kept fresh by `qoyod_invoices_sync`
        and by the Plan-B write-through hook.

`integration_inbox`, `manual_qoyod_invoice_id`, `qoyod_invoice_id`
are used ONLY as helper signals for the "Repair Marker" hint —
NEVER as the authoritative marker.

Five reconciliation outcomes (per user directive):
    • matched              — مطابق
    • needs_plan_b_send    — يحتاج إرسال Plan B
    • qoyod_only           — موجود في قيود فقط
    • needs_repair_marker  — يحتاج Repair Marker
    • amount_mismatch      — فرق مبلغ

READ-ONLY. No writes to قيود. No writes to `qoyod_invoices` here
(the sync module owns that). No writes to unified_orders.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date,
)
from integrations.qoyod.unsent_orders import _is_real
from integrations.qoyod_manual.missing_diagnostics import (
    _status_key_from_unified, _unified_salla_date,
)

MATCHED             = "مطابق"
NEEDS_PLAN_B_SEND   = "يحتاج إرسال Plan B"
QOYOD_ONLY          = "موجود في قيود فقط"
NEEDS_REPAIR_MARKER = "يحتاج Repair Marker"
AMOUNT_MISMATCH     = "فرق مبلغ"

_ALL_STATUSES = (MATCHED, NEEDS_PLAN_B_SEND, QOYOD_ONLY,
                 NEEDS_REPAIR_MARKER, AMOUNT_MISMATCH)

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)
_TOLERANCE = 0.01

# ── Match-key extractor (user directive 2026-07-09) ──────────────
# Salla order numbers are LONG numeric strings — typically 9 digits,
# always ≥ 8. This pattern is intentionally strict to avoid picking
# up random receipt/customer ids in `notes` / `description`.
import re
_ORDER_NUMBER_RE = re.compile(r"\b(\d{8,12})\b")


def _extract_match_key(inv: dict) -> tuple[Optional[str], str]:
    """Return (order_number, source) — the STRICT reconciliation key
    for this Qoyod invoice.

    User directive 2026-07-09 (final): `order_number` is the SOLE
    match-key between Salla, Mezan, and قيود. The primary path is
    ONLY these authoritative reference fields:

        1. `reference`          — Qoyod's canonical field
        2. `salla_order_number` — alias written by write-through
        3. `external_reference` — Qoyod alias
        4. `source_reference`   — Qoyod alias

    `notes` and `description` are NEVER used as a matching source —
    they carry too much free-text noise. They are surfaced in the
    row-level `debug` bag purely for the operator to inspect why an
    orphan invoice failed to join a Salla order.

    Returns (None, "orphan") when no strict key can be resolved.
    """
    for field in ("reference", "salla_order_number",
                  "external_reference", "source_reference"):
        v = str(inv.get(field) or "").strip()
        if v and _ORDER_NUMBER_RE.fullmatch(v):
            return v, field
    # Last resort: reference itself, even if it doesn't match the
    # strict digit pattern (e.g. legacy invoices with alphanumeric
    # references — still countable, still deduped, but won't join
    # to a Salla order_number).
    ref = str(inv.get("reference") or "").strip()
    if ref:
        return ref, "reference_loose"
    son = str(inv.get("salla_order_number") or "").strip()
    if son:
        return son, "salla_order_number_loose"
    return None, "orphan"



async def _load_eligible_unified(db, *, user_id: str) -> dict[str, dict]:
    """Return `order_number → row` for every Salla order eligible
    for Plan-B (same rules as /orders + Plan-B pending)."""
    q = {
        "user_id": user_id,
        "order_date": {"$gte": _FLOOR_DATE.isoformat()},
    }
    projection = {
        "_id": 0, "order_number": 1, "order_id": 1,
        "order_status": 1, "order_status_slug": 1,
        "order_date": 1, "order_date_inferred": 1, "created_at": 1,
        "payment_method": 1, "total_amount": 1, "currency": 1,
        "customer_name": 1, "customer_mobile": 1,
    }
    out: dict[str, dict] = {}
    cursor = db.unified_orders.find(q, projection).sort("order_date", -1)
    async for u in cursor:
        on = str(u.get("order_number") or "").strip()
        if not on:
            continue
        if u.get("order_date_inferred"):
            continue
        d = _unified_salla_date(u)
        if d is None or d < _FLOOR_DATE:
            continue
        if _status_key_from_unified(u) is None:
            continue
        out.setdefault(on, u)
    return out


async def _load_local_qoyod_invoices(
    db, *, markers_user_id: str,
) -> dict[str, dict]:
    """Return `match_key → newest invoice row` using the strict
    fallback chain from `_extract_match_key`. Orphans (no
    resolvable order_number) get a synthetic key
    `__ORPHAN__:{qid}` so they still surface as `qoyod_only`
    with an explicit marker.
    """
    out: dict[str, dict] = {}
    cursor = db.qoyod_invoices.find(
        {"user_id": markers_user_id},
        {"_id": 0, "qoyod_invoice_id": 1, "invoice_number": 1,
         "reference": 1, "salla_order_number": 1,
         "customer_name": 1, "issue_date": 1,
         "total": 1, "paid_amount": 1, "remaining": 1,
         "status": 1, "source": 1, "last_sync_at": 1,
         "notes": 1, "description": 1},
    ).sort([("issue_date", -1), ("qoyod_invoice_id", -1)])
    async for inv in cursor:
        # DRY/PREVIEW markers are placeholders from non-writing runs, not
        # invoices that exist in Qoyod. They must never affect parity counts
        # or suppress a real Plan-B send.
        qoyod_invoice_id = inv.get("qoyod_invoice_id")
        if not qoyod_invoice_id or not _is_real(qoyod_invoice_id):
            continue
        key, source = _extract_match_key(inv)
        # Stash the resolved key + source on the row for the
        # reconciliation UI to show as a debug badge on qoyod_only
        # rows.
        inv["_match_key"] = key
        inv["_match_source"] = source
        if key is None:
            k = f"__ORPHAN__:{inv.get('qoyod_invoice_id')}"
            out[k] = inv
            continue
        if key in out:
            continue  # keep newest by issue_date sort
        out[key] = inv
    return out


def _normalize_marker_user_ids(marker_user_ids: list[str]) -> list[str]:
    """Return unique, non-empty inbox owner ids without changing order."""
    normalized_user_ids = list(dict.fromkeys(
        str(value).strip() for value in marker_user_ids
        if str(value).strip()
    ))
    return normalized_user_ids


def _marker_owner_query(normalized_user_ids: list[str]) -> str | dict:
    """Build the tenant-isolated owner part of the inbox query."""
    owner_query: str | dict = normalized_user_ids[0]
    if len(normalized_user_ids) > 1:
        owner_query = {"$in": normalized_user_ids}
    return owner_query


async def _load_inbox_marker_rows(
    db, *, marker_user_ids: list[str], order_numbers: list[str],
) -> dict[str, list[dict]]:
    """Bulk-load helper marker rows for exact Salla order numbers.

    Reconciliation can contain thousands of eligible orders.  Loading the
    inbox once keeps this helper signal O(1) database round-trips instead of
    issuing one query per matched invoice.  The query remains strictly scoped
    to the authenticated Qoyod/settings owner and current Salla orders owner.
    """
    normalized_user_ids = _normalize_marker_user_ids(marker_user_ids)
    normalized_order_numbers = list(dict.fromkeys(
        str(value).strip() for value in order_numbers
        if str(value).strip()
    ))
    if not normalized_user_ids or not normalized_order_numbers:
        return {}

    cursor = db.integration_inbox.find(
        {
            "user_id": _marker_owner_query(normalized_user_ids),
            "salla_order_number": {"$in": normalized_order_numbers},
            "$or": [
                {"manual_qoyod_invoice_id": {
                    "$exists": True, "$nin": [None, ""]}},
                {"manual_qoyod_payment_id": {
                    "$exists": True, "$nin": [None, ""]}},
                {"qoyod_invoice_id": {
                    "$exists": True, "$nin": [None, ""]}},
            ],
        },
        {"_id": 0, "salla_order_number": 1,
         "manual_qoyod_invoice_id": 1,
         "manual_qoyod_payment_id": 1, "qoyod_invoice_id": 1},
    )

    rows_by_order: dict[str, list[dict]] = {}
    allowed_order_numbers = set(normalized_order_numbers)
    async for row in cursor:
        order_number = str(row.get("salla_order_number") or "").strip()
        if order_number not in allowed_order_numbers:
            continue
        rows_by_order.setdefault(order_number, []).append(row)
    return rows_by_order


def _resolve_inbox_marker_rows(
    rows: list[dict], *, expected_invoice_id: Any = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Resolve one order's preloaded rows using exact invoice-id semantics."""
    inv_marker: Optional[str] = None
    pay_marker: Optional[str] = None
    expected_id = (
        str(expected_invoice_id).strip()
        if expected_invoice_id not in (None, "") else None
    )
    for r in rows:
        row_invoice_ids = [
            str(value)
            for value in (
                r.get("manual_qoyod_invoice_id"),
                r.get("qoyod_invoice_id"),
            )
            if value and _is_real(value)
        ]
        row_invoice_id: Optional[str] = None
        if expected_id is not None and expected_id in row_invoice_ids:
            row_invoice_id = expected_id
        elif expected_id is None and row_invoice_ids:
            row_invoice_id = row_invoice_ids[0]
        # A stale marker for another Qoyod invoice must not satisfy the
        # reconciliation row merely because it shares an order number.
        if row_invoice_id is None:
            continue
        inv_marker = row_invoice_id
        pid = r.get("manual_qoyod_payment_id")
        if pay_marker is None and pid and _is_real(pid):
            pay_marker = str(pid)
        if inv_marker and (pay_marker or expected_id is not None):
            break
    return (inv_marker is not None), inv_marker, pay_marker


async def _has_marker_in_inbox(
    db, *, marker_user_ids: list[str], order_number: str,
    expected_invoice_id: Any = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Helper signal ONLY. Is there ANY real marker for this order?

    Kept as a single-order compatibility helper for focused diagnostics and
    tests.  The reconciliation report itself uses `_load_inbox_marker_rows`
    once for all matched orders.
    """
    order_number = str(order_number or "").strip()
    rows_by_order = await _load_inbox_marker_rows(
        db,
        marker_user_ids=marker_user_ids,
        order_numbers=[order_number],
    )
    return _resolve_inbox_marker_rows(
        rows_by_order.get(order_number, []),
        expected_invoice_id=expected_invoice_id,
    )


def _fmt(v):
    return None if v is None else round(float(v), 2)


async def run_reconciliation_v2(
    db, *,
    orders_user_id: str,
    markers_user_id: Optional[str] = None,
) -> dict:
    """The reconciliation report itself. Read-only."""
    if markers_user_id is None:
        markers_user_id = orders_user_id
    marker_user_ids = list(dict.fromkeys(
        value for value in (
            str(markers_user_id or "").strip(),
            str(orders_user_id or "").strip(),
        ) if value
    ))

    unified = await _load_eligible_unified(db, user_id=orders_user_id)
    local_inv = await _load_local_qoyod_invoices(
        db, markers_user_id=markers_user_id)

    # Marker rows are only relevant when both sides already have an exact
    # order-number match.  Fetch all of them in one tenant-isolated query;
    # never perform an inbox query from inside the reconciliation loop.
    matched_order_numbers = [
        order_number for order_number in unified
        if order_number in local_inv
    ]
    inbox_marker_rows = await _load_inbox_marker_rows(
        db,
        marker_user_ids=marker_user_ids,
        order_numbers=matched_order_numbers,
    )

    counts: dict[str, int] = {k: 0 for k in _ALL_STATUSES}
    rows: list[dict] = []
    claimed_refs: set[str] = set()

    for on, u in unified.items():
        inv = local_inv.get(on)
        salla_total = _fmt(u.get("total_amount"))
        salla_date = u.get("order_date")
        customer = u.get("customer_name")
        salla_status = u.get("order_status") or u.get("order_status_slug")

        base = {
            "order_number":     on,
            "salla_date":       salla_date,
            "salla_status":     salla_status,
            "customer_name":    customer,
            "salla_total":      salla_total,
            "qoyod_invoice_id": None,
            "invoice_number":   None,
            "qoyod_date":       None,
            "qoyod_total":      None,
            "paid_amount":      None,
            "remaining":        None,
            "qoyod_status":     None,
            "match":            None,
            "note":             None,
        }

        if inv is None:
            counts[NEEDS_PLAN_B_SEND] += 1
            rows.append({**base,
                         "match": NEEDS_PLAN_B_SEND,
                         "note": ("طلب موجود في سلة (ضمن النطاق) "
                                  "لكن لا يوجد فاتورة مقابلة في "
                                  "قيود — يحتاج إرسال يدوي عبر Plan B"),
                         "debug": {
                             "order_number":     on,
                             "qoyod_reference":  None,
                             "invoice_id":       None,
                             "payment_id":       None,
                             "remaining":        None,
                             "match_source":     "none",
                         }})
            continue

        claimed_refs.add(on)
        qoyod_total = _fmt(inv.get("total"))
        qoyod_paid = _fmt(inv.get("paid_amount"))
        qoyod_remaining = _fmt(inv.get("remaining"))
        qoyod_status = inv.get("status")
        qoyod_date = inv.get("issue_date")
        qoyod_invoice_id = inv.get("qoyod_invoice_id")
        invoice_number = inv.get("invoice_number") or qoyod_invoice_id

        # Marker check — helper signal only. If invoice exists in
        # قيود but NO marker in inbox → Mezan needs a Repair Marker.
        has_marker, inv_marker, pay_marker = _resolve_inbox_marker_rows(
            inbox_marker_rows.get(on, []),
            expected_invoice_id=qoyod_invoice_id,
        )

        # Debug bag (user directive 2026-07-09): STRICT match by
        # `order_number == reference` — proves the match without
        # ambiguity. `match_source` is always populated.
        _match_source = inv.get("_match_source") or "reference"
        debug = {
            "order_number":     on,
            "qoyod_reference":  inv.get("reference"),
            "invoice_id":       qoyod_invoice_id,
            "payment_id":       pay_marker,
            "remaining":        qoyod_remaining,
            "match_source":     _match_source,
        }

        base.update({
            "qoyod_invoice_id": qoyod_invoice_id,
            "invoice_number":   invoice_number,
            "qoyod_date":       qoyod_date,
            "qoyod_total":      qoyod_total,
            "paid_amount":      qoyod_paid,
            "remaining":        qoyod_remaining,
            "qoyod_status":     qoyod_status,
            "debug":            debug,
        })

        if not has_marker:
            counts[NEEDS_REPAIR_MARKER] += 1
            rows.append({**base,
                         "match": NEEDS_REPAIR_MARKER,
                         "note": ("فاتورة موجودة في قيود لكن لا يوجد "
                                  "marker في ميزان — شغّل "
                                  "repair-recon-markers")})
            continue

        diff = None
        if salla_total is not None and qoyod_total is not None:
            diff = round(salla_total - qoyod_total, 2)

        if diff is None or abs(diff) <= _TOLERANCE:
            counts[MATCHED] += 1
            note = "مطابق تماماً" if diff is not None else \
                "مطابق (المبلغ غير محدد)"
            rows.append({**base, "match": MATCHED, "note": note,
                         "difference": diff})
        else:
            counts[AMOUNT_MISMATCH] += 1
            rows.append({
                **base,
                "match": AMOUNT_MISMATCH,
                "difference": diff,
                "note": (f"فرق {diff:+.2f} ريال بين سلة وقيود — "
                         "يحتاج مراجعة"),
            })

    # قيود invoices that had NO matching Salla order eligible.
    for ref, inv in local_inv.items():
        if ref.startswith("__ORPHAN__:") or ref not in claimed_refs:
            if not ref.startswith("__ORPHAN__:") and ref in claimed_refs:
                continue
            counts[QOYOD_ONLY] += 1
            match_key = inv.get("_match_key")
            match_source = inv.get("_match_source", "orphan")
            # Debug bag — surfaces WHY we couldn't join this invoice
            # to a Salla order, so the operator can decide whether
            # to backfill the reference on the قيود side.
            notes_snippet = (
                (inv.get("notes") or "")[:120] or None)
            desc_snippet = (
                (inv.get("description") or "")[:120] or None)
            rows.append({
                "order_number":     (match_key if not ref.startswith(
                    "__ORPHAN__:") else None),
                "salla_date":       None,
                "salla_status":     None,
                "customer_name":    inv.get("customer_name"),
                "salla_total":      None,
                "qoyod_invoice_id": inv.get("qoyod_invoice_id"),
                "invoice_number":   inv.get("invoice_number"),
                "qoyod_date":       inv.get("issue_date"),
                "qoyod_total":      _fmt(inv.get("total")),
                "paid_amount":      _fmt(inv.get("paid_amount")),
                "remaining":        _fmt(inv.get("remaining")),
                "qoyod_status":     inv.get("status"),
                "match":            QOYOD_ONLY,
                "debug": {
                    "reference":            inv.get("reference"),
                    "salla_order_number":   inv.get("salla_order_number"),
                    "match_key":            match_key,
                    "match_source":         match_source,
                    "notes_snippet":        notes_snippet,
                    "description_snippet":  desc_snippet,
                },
                "note": (
                    "فاتورة موجودة في قيود بلا مرجع سلة صالح "
                    f"(source={match_source}) — لا يمكن ربطها بطلب"
                    if ref.startswith("__ORPHAN__:") or match_source
                    in ("reference_loose", "salla_order_number_loose",
                        "orphan") else
                    ("فاتورة قيود موجودة لكن لا يوجد طلب مقابل في سلة "
                     "ضمن النطاق — قد يكون طلب خارج النطاق أو تم "
                     "إنشاؤه من مسار خارجي")
                ),
            })

    all_matched = all(counts[k] == 0 for k in _ALL_STATUSES
                       if k != MATCHED)

    return {
        "ok":                    True,
        "run_at":                datetime.now(timezone.utc).isoformat(),
        "sync_start_date":       _FLOOR_DATE.isoformat(),
        "counts":                counts,
        "salla_orders_total":    len(unified),
        "qoyod_invoices_total":  len(local_inv),
        "all_matched":           all_matched,
        "rows":                  rows,
        "outcome_labels":        list(_ALL_STATUSES),
    }
