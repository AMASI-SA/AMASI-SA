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
from typing import Optional

from pymongo import UpdateOne

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date,
)
from integrations.qoyod.fresh_start_audit import _coerce_float, _paginate

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)
_INVOICE_WRITE_BATCH_SIZE = 500


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


def _sync_salla_order_id(qoyod_invoice_id: str) -> str:
    """Return a stable unique identity for invoices imported from Qoyod.

    The legacy collection has a unique (user_id, salla_order_id)
    index. Synced invoices do not always have a Salla id, so omitting
    the field makes every subsequent insert collide on null.
    """
    return f"qoyod-sync:{qoyod_invoice_id}"


async def _write_invoice_batches(collection, rows: list[dict]) -> dict:
    """Persist prepared invoice upserts with bounded MongoDB round trips.

    A full Qoyod refresh currently contains more than two thousand rows.
    Writing them sequentially keeps the HTTP request open long enough to hit
    the production proxy timeout.  Bulk writes preserve the same upsert
    semantics while reducing the hot path to a handful of database calls.

    If a bulk batch is rejected, retry its rows individually so one malformed
    invoice cannot discard the rest of the refresh.
    """
    created = 0
    updated = 0
    row_errors = 0
    batches = 0
    fallback_batches = 0

    for offset in range(0, len(rows), _INVOICE_WRITE_BATCH_SIZE):
        batch = rows[offset:offset + _INVOICE_WRITE_BATCH_SIZE]
        operations = [
            UpdateOne(row["filter"], row["update"], upsert=True)
            for row in batch
        ]
        batches += 1
        try:
            result = await collection.bulk_write(
                operations,
                ordered=False,
            )
            created += int(result.upserted_count or 0)
            updated += int(result.modified_count or 0)
            continue
        except Exception:  # noqa: BLE001
            fallback_batches += 1

        for row in batch:
            try:
                result = await collection.update_one(
                    row["filter"],
                    row["update"],
                    upsert=True,
                )
                if result.upserted_id is not None:
                    created += 1
                elif result.modified_count:
                    updated += 1
            except Exception:  # noqa: BLE001
                row_errors += 1

    return {
        "created": created,
        "updated": updated,
        "row_errors": row_errors,
        "write_batches": batches,
        "bulk_fallback_batches": fallback_batches,
    }


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
    prepared_rows: list[dict] = []

    try:
        items = await _paginate(
            api_client.list_invoices,
            page_size=page_size, max_pages=max_pages,
            extract_keys=("invoices", "data", "items"),
        )
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": (
                f"تعذر جلب فواتير قيود: {type(e).__name__}: {e}"
            ),
            "fetched": 0, "in_scope": 0,
            "created": 0, "updated": 0, "skipped": 0,
            "row_errors": 0,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    fetched = len(items)

    # The historical unique (user_id, salla_order_id) index allowed at
    # most one imported row with a missing identity. Repair that legacy
    # row once before the loop. New rows receive their identity through
    # $setOnInsert below, so the hot path stays at one database write per
    # invoice and completes within the proxy timeout.
    legacy_missing_identity = await db.qoyod_invoices.find_one(
        {
            "user_id": user_id,
            "$or": [
                {"salla_order_id": {"$exists": False}},
                {"salla_order_id": None},
                {"salla_order_id": ""},
            ],
        },
        {"_id": 1, "qoyod_invoice_id": 1},
    )
    if legacy_missing_identity and legacy_missing_identity.get(
        "qoyod_invoice_id"
    ):
        await db.qoyod_invoices.update_one(
            {"_id": legacy_missing_identity["_id"]},
            {"$set": {
                "salla_order_id": _sync_salla_order_id(
                    str(legacy_missing_identity["qoyod_invoice_id"])
                )
            }},
        )

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
            reference = str(
                it.get("reference")
                or it.get("external_reference")
                or it.get("source_reference")
                or ""
            ).strip()
            inv_number = str(
                it.get("invoice_number")
                or it.get("number")
                or reference
                or ""
            ).strip()
            status = str(it.get("status") or "").strip().lower() or None
            customer = _customer_name(it)
            # Persist the free-text fields at the top level too — the
            # reconciliation match-key fallback (user directive
            # 2026-07-09) reads them without touching `raw_response`.
            notes = str(
                it.get("notes") or it.get("internal_notes") or ""
            ).strip() or None
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
            sync_salla_order_id = _sync_salla_order_id(qid)
            set_on_insert = {
                "source":         "synced_from_qoyod",
                "created_at":     now_iso,
                # Required by the legacy unique
                # (user_id, salla_order_id) index. Keep existing real
                # Plan-B order ids untouched; only new synced rows get
                # this Qoyod-specific stable identity.
                "salla_order_id": sync_salla_order_id,
            }

            prepared_rows.append({
                "filter": {
                    "user_id": user_id,
                    "qoyod_invoice_id": qid,
                },
                "update": {
                    "$set": set_fields,
                    "$setOnInsert": set_on_insert,
                },
            })
        except Exception:  # noqa: BLE001
            # Never let one bad row abort the whole sync. Count and
            # continue — the operator sees `row_errors` in the summary.
            row_errors += 1

    write_summary = await _write_invoice_batches(
        db.qoyod_invoices,
        prepared_rows,
    )
    created += write_summary["created"]
    updated += write_summary["updated"]
    row_errors += write_summary["row_errors"]

    finished_at = datetime.now(timezone.utc)
    return {
        "ok":          True,
        "fetched":     fetched,
        "in_scope":    in_scope,
        "created":     created,
        "updated":     updated,
        "skipped":     skipped,
        "row_errors":  row_errors,
        "write_batches": write_summary["write_batches"],
        "bulk_fallback_batches": write_summary["bulk_fallback_batches"],
        "sync_start":  sync_start.isoformat(),
        "started_at":  started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int(
            (finished_at - started_at).total_seconds() * 1000
        ),
    }
