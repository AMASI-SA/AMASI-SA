"""Qoyod → Mezan invoice sync.

User directive (2026-07-09): the reconciliation page must become the
SOURCE OF TRUTH for the Mezan ↔ Qoyod comparison. Instead of hitting
Qoyod on every reconciliation click, we pull invoices ONCE (or on
demand) into `qoyod_invoices` with a stable schema, then every future
comparison runs against the LOCAL table.

Local schema (`qoyod_invoices`)
────────────────────────────────
Keyed by `qoyod_invoice_id` (Qoyod's own id). Fields per row:

    user_id              — tenant that owns the row
    qoyod_invoice_id     — Qoyod's numeric id (string)
    invoice_number       — invoice # / number / reference
    reference            — Salla order_number (Qoyod field `reference`)
    customer_name        — from Qoyod payload
    issue_date           — invoice issue date
    total                — invoice total (SAR)
    paid_amount          — total paid so far
    remaining            — total − paid
    status               — قيود invoice status ("paid" / "partially_paid" / …)
    source               — "synced_from_qoyod" (this module) OR
                            "plan_b_send" (Plan-B write-through)
    last_sync_at         — UTC ISO of the most recent sync touch
    raw_response         — trimmed Qoyod row for audit
    created_at           — first-seen UTC timestamp

READ-ONLY towards قيود. NEVER PUT/POST — only GET via the legacy
paginator (`api_client.list_invoices`).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date,
)
from integrations.qoyod.fresh_start_audit import _coerce_float, _paginate

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)


def _trim_raw(it: dict) -> dict:
    """Keep only the audit-worthy fields; drop nested `payments`
    receipts and product line items to control the row size."""
    keep = ("id", "invoice_number", "number", "reference",
            "external_reference", "source_reference", "issue_date",
            "due_date", "total", "total_amount",
            "paid_amount", "amount_paid", "outstanding",
            "customer_name", "customer_id", "status",
            "currency", "created_at", "updated_at",
            # Free-text fields used by the reconciliation match-key
            # fallback (user directive 2026-07-09).
            "notes", "description", "internal_notes",
            "salla_order_number")
    return {k: it.get(k) for k in keep if k in it}


def _customer_name(it: dict) -> Optional[str]:
    """Qoyod uses several fields depending on the endpoint version."""
    n = (it.get("customer_name")
         or it.get("customer_display_name")
         or (it.get("customer") or {}).get("name")
         or (it.get("customer") or {}).get("display_name")
         or (it.get("contact") or {}).get("name"))
    return str(n).strip() if n else None


def _paid_and_remaining(it: dict, total: float) -> tuple[float, float]:
    paid = _coerce_float(it.get("paid_amount")
                         or it.get("amount_paid")
                         or it.get("total_paid"))
    if paid is None:
        # Some Qoyod responses expose `outstanding` instead of paid.
        outstanding = _coerce_float(
            it.get("outstanding") or it.get("balance"))
        if outstanding is not None and total is not None:
            paid = max(0.0, round(total - outstanding, 2))
    if paid is None:
        paid = 0.0
    remaining = round((total or 0.0) - (paid or 0.0), 2)
    return round(paid, 2), remaining


def _in_scope(issue_date: Optional[str], sync_start: date) -> bool:
    d = _parse_iso_date(issue_date)
    if d is None:
        return False
    return d >= sync_start


async def sync_qoyod_invoices(
    db, *,
    user_id: str,
    api_client,
    from_date: Optional[date] = None,
    max_pages: int = 200,
    page_size: int = 50,
) -> dict:
    """Pull invoices from Qoyod and upsert them into
    `qoyod_invoices` under `user_id`. Returns a summary dict.

    Defensive: every possible failure path (network, pagination,
    row-level upsert, coercion) is caught. The function NEVER
    raises — it either returns `ok=true` or `ok=false` with an
    `error` field. This keeps the reconciliation endpoint working
    even when قيود is briefly unreachable.
    """
    sync_start = from_date or _FLOOR_DATE
    started_at = datetime.now(timezone.utc)
    fetched = 0
    in_scope = 0
    created = 0
    updated = 0
    skipped = 0
    row_errors = 0

    try:
        items = await _paginate(
            api_client.list_invoices,
            page_size=page_size, max_pages=max_pages,
            extract_keys=("invoices", "data", "items"),
        )
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": (f"تعذر جلب فواتير قيود: "
                       f"{type(e).__name__}: {e}"),
            "fetched": 0, "in_scope": 0,
            "created": 0, "updated": 0, "skipped": 0,
            "row_errors": 0,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    fetched = len(items)

    for it in items:
        try:
            if not isinstance(it, dict):
                skipped += 1
                continue
            qid = str(it.get("id") or "").strip()
            if not qid:
                skipped += 1
                continue
            issue_date = it.get("issue_date") or ""
            if not _in_scope(issue_date, sync_start):
                skipped += 1
                continue
            in_scope += 1

            total_raw = _coerce_float(
                it.get("total") or it.get("total_amount"))
            total = float(total_raw or 0.0)
            paid, remaining = _paid_and_remaining(it, total)
            reference = str(it.get("reference")
                            or it.get("external_reference")
                            or it.get("source_reference") or "").strip()
            inv_number = str(it.get("invoice_number")
                              or it.get("number")
                              or reference or "").strip()
            status = str(it.get("status") or "").strip().lower() or None
            customer = _customer_name(it)
            # Persist the free-text fields at the top level too — the
            # reconciliation match-key fallback (user directive
            # 2026-07-09) reads them without touching `raw_response`.
            notes = str(it.get("notes") or it.get("internal_notes")
                          or "").strip() or None
            description = str(it.get("description") or "").strip() \
                or None

            now_iso = datetime.now(timezone.utc)
            set_fields = {
                "user_id":            user_id,
                "qoyod_invoice_id":   qid,
                "invoice_number":     inv_number,
                "reference":          reference,
                "salla_order_number": reference,
                "customer_name":      customer,
                "issue_date":         issue_date or None,
                "total":              round(total, 2),
                "paid_amount":        paid,
                "remaining":          remaining,
                "status":             status,
                "notes":              notes,
                "description":        description,
                "last_sync_at":       now_iso,
                "raw_response":       _trim_raw(it),
            }
            set_on_insert = {
                "source":     "synced_from_qoyod",
                "created_at": now_iso,
            }

            res = await db.qoyod_invoices.update_one(
                {"user_id": user_id, "qoyod_invoice_id": qid},
                {"$set": set_fields, "$setOnInsert": set_on_insert},
                upsert=True,
            )
            if res.upserted_id is not None:
                created += 1
            elif res.modified_count:
                updated += 1
            else:
                await db.qoyod_invoices.update_one(
                    {"user_id": user_id, "qoyod_invoice_id": qid},
                    {"$set": {"last_sync_at": now_iso}},
                )
        except Exception:  # noqa: BLE001
            # Never let one bad row abort the whole sync. Count and
            # continue — the operator sees `row_errors` in the summary.
            row_errors += 1

    finished_at = datetime.now(timezone.utc)
    return {
        "ok":          True,
        "fetched":     fetched,
        "in_scope":    in_scope,
        "created":     created,
        "updated":     updated,
        "skipped":     skipped,
        "row_errors":  row_errors,
        "sync_start":  sync_start.isoformat(),
        "started_at":  started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds()
                            * 1000),
    }
