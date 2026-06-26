"""Qoyod Fresh-Start Audit — strict READ-ONLY pre-cleanup snapshot.

Purpose (2026-06-27, user spec)
───────────────────────────────
Before Mezan becomes the SOLE source of truth for Qoyod data, we need
a forensic audit of what already exists in Qoyod (entirely produced
by the legacy direct-Salla integration). The audit MUST:

  • READ-ONLY — never DELETE, PATCH, PUT, or POST against Qoyod.
  • Touch ONLY four entities: invoices, receipts, products, customers.
  • Never query/modify: chart of accounts, branches, taxes, financial
    accounts, or any settings endpoint.
  • Produce counts, histograms, and risk-flags the operator can review
    before deciding what (if anything) to clean up.

Outputs persisted in `qoyod_fresh_start_audits` (one row per run):
    {
      run_id, user_id, started_at, finished_at, status,
      invoices:  {total, by_month, with_external_ref, without_ref,
                  with_receipt, without_receipt, ...},
      receipts:  {total, by_month, linked_invoice_ids, ...},
      products:  {total, by_month, with_sku, without_sku, ...},
      customers: {total, by_month, with_phone, with_email, guests,
                  has_invoices, no_invoices, ...},
      flags:     [{code, severity, message, count}]
    }

NO DELETE LOGIC HERE. Cleanup is a separate, gated module.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _month_bucket(value: Any) -> str:
    """Return YYYY-MM from a date-ish field, or 'unknown' if unparseable."""
    if not value:
        return "unknown"
    s = str(value)
    # ISO date prefix — accept "2024-10-15", "2024-10-15T...", "2024/10/15"
    m = re.match(r"^(\d{4})[-/](\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return "unknown"


def _extract_list(resp: Any, keys: tuple[str, ...]) -> list:
    """Qoyod responses come in various shapes."""
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []
    for k in keys:
        v = resp.get(k)
        if isinstance(v, list):
            return v
    # Sometimes nested in `data.items` or similar.
    data = resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def _looks_like_salla_ref(ref: Any) -> bool:
    """Heuristic: legacy Salla integration sets `external_reference` or
    `reference` to the Salla order id (long numeric). Anything that
    matches a pure 6+ digit number, or contains 'salla', counts."""
    if not ref:
        return False
    s = str(ref).strip().lower()
    if "salla" in s:
        return True
    if re.fullmatch(r"\d{6,}", s):
        return True
    return False


def _coerce_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────
# Pagination helpers (defensive — Qoyod may rate-limit)
# ─────────────────────────────────────────────────────────────────────
async def _paginate(
    fetch, *, page_size: int, max_pages: int,
    extract_keys: tuple[str, ...],
) -> list:
    """Generic paginator. Stops on empty page or when `max_pages` reached.
    Sleeps briefly between calls to be polite to Qoyod."""
    out: list = []
    for page in range(1, max_pages + 1):
        try:
            resp = await fetch(page=page, limit=page_size)
        except QoyodAPIError:
            raise
        items = _extract_list(resp, extract_keys)
        if not items:
            break
        out.extend(items)
        if len(items) < page_size:
            break
        # 100ms cushion between pages — keeps us under most rate limits.
        await asyncio.sleep(0.1)
    return out


# ─────────────────────────────────────────────────────────────────────
# Per-entity analysers
# ─────────────────────────────────────────────────────────────────────
def _analyse_products(items: list) -> dict:
    by_month: Counter = Counter()
    with_sku = 0
    without_sku = 0
    samples_with_sku: list[dict] = []
    samples_without_sku: list[dict] = []
    for it in items:
        by_month[_month_bucket(it.get("created_at") or it.get("date"))] += 1
        sku = (it.get("sku") or it.get("code") or "").strip()
        if sku:
            with_sku += 1
            if len(samples_with_sku) < 5:
                samples_with_sku.append({
                    "id":   str(it.get("id") or ""),
                    "sku":  sku,
                    "name": it.get("name") or it.get("name_ar") or ""})
        else:
            without_sku += 1
            if len(samples_without_sku) < 5:
                samples_without_sku.append({
                    "id":   str(it.get("id") or ""),
                    "name": it.get("name") or it.get("name_ar") or ""})
    return {
        "total":       len(items),
        "with_sku":    with_sku,
        "without_sku": without_sku,
        "by_month":    dict(sorted(by_month.items())),
        "samples": {
            "with_sku":    samples_with_sku,
            "without_sku": samples_without_sku,
        },
    }


def _analyse_customers(items: list,
                       invoice_contact_ids: set[str]) -> dict:
    by_month: Counter = Counter()
    with_phone = 0
    with_email = 0
    guests = 0
    has_invoices = 0
    samples_no_invoice: list[dict] = []
    samples_guest: list[dict] = []
    for it in items:
        cid = str(it.get("id") or it.get("contact_id") or "")
        by_month[_month_bucket(it.get("created_at"))] += 1
        phone = (it.get("phone") or it.get("mobile") or "").strip()
        email = (it.get("email") or "").strip()
        if phone:
            with_phone += 1
        if email:
            with_email += 1
        name = (it.get("name") or it.get("display_name") or "").strip()
        is_guest = (not phone and not email) or \
                   "ضيف" in name.lower() or "guest" in name.lower()
        if is_guest:
            guests += 1
            if len(samples_guest) < 5:
                samples_guest.append({"id": cid, "name": name})
        if cid in invoice_contact_ids:
            has_invoices += 1
        elif len(samples_no_invoice) < 5:
            samples_no_invoice.append({
                "id": cid, "name": name, "phone": phone, "email": email})
    return {
        "total":        len(items),
        "with_phone":   with_phone,
        "with_email":   with_email,
        "guests":       guests,
        "has_invoices": has_invoices,
        "no_invoices":  len(items) - has_invoices,
        "by_month":     dict(sorted(by_month.items())),
        "samples": {
            "no_invoice": samples_no_invoice,
            "guest":      samples_guest,
        },
    }


def _analyse_invoices(items: list,
                      receipt_invoice_ids: set[str]) -> dict:
    by_month: Counter = Counter()
    by_status: Counter = Counter()
    total_amount = 0.0
    with_external_ref = 0
    without_external_ref = 0
    matches_salla_pattern = 0
    with_receipt = 0
    without_receipt = 0
    contact_ids: set[str] = set()
    samples_no_receipt: list[dict] = []
    samples_no_ref: list[dict] = []
    for it in items:
        iid = str(it.get("id") or "")
        by_month[_month_bucket(
            it.get("issue_date") or it.get("created_at") or it.get("date")
        )] += 1
        by_status[(it.get("status") or "unknown")] += 1
        total_amount += _coerce_float(
            it.get("total") or it.get("total_amount")
            or it.get("amount"))
        ext_ref = (it.get("external_reference") or it.get("reference")
                   or it.get("source_reference") or "")
        if ext_ref:
            with_external_ref += 1
            if _looks_like_salla_ref(ext_ref):
                matches_salla_pattern += 1
        else:
            without_external_ref += 1
            if len(samples_no_ref) < 5:
                samples_no_ref.append({
                    "id":     iid,
                    "number": it.get("reference") or it.get("number") or "",
                    "issue_date": it.get("issue_date") or "",
                    "total":  _coerce_float(it.get("total"))})
        cid = it.get("contact_id") or it.get("customer_id")
        if cid:
            contact_ids.add(str(cid))
        if iid in receipt_invoice_ids:
            with_receipt += 1
        else:
            without_receipt += 1
            if len(samples_no_receipt) < 5:
                samples_no_receipt.append({
                    "id":         iid,
                    "issue_date": it.get("issue_date") or "",
                    "total":      _coerce_float(it.get("total")),
                    "status":     it.get("status") or ""})
    return {
        "total":                 len(items),
        "total_amount":          round(total_amount, 2),
        "with_external_ref":     with_external_ref,
        "without_external_ref":  without_external_ref,
        "matches_salla_pattern": matches_salla_pattern,
        "with_receipt":          with_receipt,
        "without_receipt":       without_receipt,
        "by_month":              dict(sorted(by_month.items())),
        "by_status":             dict(by_status),
        "contact_ids_referenced": len(contact_ids),
        "samples": {
            "no_receipt": samples_no_receipt,
            "no_ref":     samples_no_ref,
        },
        "_contact_ids": contact_ids,  # consumed by customer analyser
    }


def _analyse_receipts(items: list) -> dict:
    by_month: Counter = Counter()
    total_amount = 0.0
    invoice_ids: set[str] = set()
    orphan = 0  # receipt without invoice_id
    by_account: Counter = Counter()
    for it in items:
        by_month[_month_bucket(
            it.get("date") or it.get("created_at"))] += 1
        total_amount += _coerce_float(
            it.get("amount") or it.get("total") or it.get("total_amount"))
        iid = it.get("invoice_id") or it.get("invoice")
        if iid:
            invoice_ids.add(str(iid))
        else:
            orphan += 1
        acc = it.get("account_id") or it.get("account")
        if acc:
            by_account[str(acc)] += 1
    return {
        "total":           len(items),
        "total_amount":    round(total_amount, 2),
        "invoice_ids":     len(invoice_ids),
        "orphan":          orphan,
        "by_month":        dict(sorted(by_month.items())),
        "by_account_id":   dict(by_account),
        "_invoice_ids":    invoice_ids,
    }


# ─────────────────────────────────────────────────────────────────────
# Cross-entity risk flags
# ─────────────────────────────────────────────────────────────────────
def _build_flags(inv: dict, rec: dict, prods: dict, cust: dict) -> list[dict]:
    flags: list[dict] = []

    # Invoices without receipts → revenue without recorded payment.
    if inv["without_receipt"] > 0:
        flags.append({
            "code": "invoices_without_receipts",
            "severity": "warning",
            "count": inv["without_receipt"],
            "message": (f"{inv['without_receipt']} فاتورة بدون سند قبض مرتبط. "
                        "قد تكون مدفوعات غير مسجَّلة."),
        })

    # Invoices without external_reference → possibly manual.
    if inv["without_external_ref"] > 0:
        flags.append({
            "code": "invoices_without_external_ref",
            "severity": "info",
            "count": inv["without_external_ref"],
            "message": (f"{inv['without_external_ref']} فاتورة ليس لها "
                        "external_reference يطابق نمط Salla — قد تكون "
                        "مُدخَلة يدوياً وليست من الربط القديم."),
        })

    # Orphan receipts → receipt without an invoice link.
    if rec["orphan"] > 0:
        flags.append({
            "code": "orphan_receipts",
            "severity": "warning",
            "count": rec["orphan"],
            "message": (f"{rec['orphan']} سند قبض بدون فاتورة مرتبطة — "
                        "يجب فحصها قبل أي حذف."),
        })

    # Products without SKU → cannot be matched to Mezan.
    if prods["without_sku"] > 0:
        flags.append({
            "code": "products_without_sku",
            "severity": "info",
            "count": prods["without_sku"],
            "message": (f"{prods['without_sku']} منتج بدون SKU — ميزان لن "
                        "يستطيع مطابقتها مع منتجات سلة. قد تكون مُدخَلة يدوياً."),
        })

    # Customers without invoices → potentially manual.
    if cust["no_invoices"] > 0:
        flags.append({
            "code": "customers_without_invoices",
            "severity": "info",
            "count": cust["no_invoices"],
            "message": (f"{cust['no_invoices']} عميل ليس له أي فاتورة في "
                        "Qoyod — قد يكون مُدخَل يدوياً أو حُذفت فواتيره."),
        })

    # Sanity: orphan receipts > invoices total is impossible.
    if rec["invoice_ids"] > inv["total"]:
        flags.append({
            "code": "receipt_invoice_mismatch",
            "severity": "warning",
            "count": rec["invoice_ids"] - inv["total"],
            "message": ("بعض سندات القبض مرتبطة بفواتير غير ظاهرة في النتائج "
                        "— ربما توجد فواتير محذوفة سابقاً."),
        })

    return flags


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────
async def run_fresh_start_audit(
    db, *, user_id: str, api_client: QoyodAPIClient,
    page_size: int = 50, max_pages: int = 200,
) -> dict:
    """End-to-end READ-ONLY audit. Persists the summary and returns it."""
    run_id = uuid.uuid4().hex
    started = _now()
    await db.qoyod_fresh_start_audits.insert_one({
        "schema_version": 1,
        "run_id":         run_id,
        "user_id":        user_id,
        "started_at":     started,
        "status":         "running",
        "scope":          ["invoices", "receipts", "products", "customers"],
        "read_only":      True,
    })

    error: Optional[dict] = None
    try:
        # Pull all four entities in parallel where possible. We run
        # them sequentially below for clearer rate-limit behaviour.
        invoices = await _paginate(
            api_client.list_invoices, page_size=page_size, max_pages=max_pages,
            extract_keys=("invoices", "data", "items"))
        receipts = await _paginate(
            api_client.list_receipts, page_size=page_size, max_pages=max_pages,
            extract_keys=("receipts", "data", "items"))
        products = await _paginate(
            api_client.list_products, page_size=page_size, max_pages=max_pages,
            extract_keys=("products", "data", "items"))
        customers = await _paginate(
            api_client.list_contacts, page_size=page_size, max_pages=max_pages,
            extract_keys=("customers", "contacts", "data", "items"))

        # Analyse — receipts first to feed the invoice analyser, then
        # invoices to feed the customer analyser.
        rec = _analyse_receipts(receipts)
        inv = _analyse_invoices(invoices, rec["_invoice_ids"])
        contact_ids_with_inv = inv.pop("_contact_ids", set())
        prods = _analyse_products(products)
        cust  = _analyse_customers(customers, contact_ids_with_inv)
        rec.pop("_invoice_ids", None)
        flags = _build_flags(inv, rec, prods, cust)
        status = "completed"
    except QoyodAPIError as exc:
        error = exc.to_log_dict()
        inv = rec = prods = cust = {}
        flags = []
        status = "failed"

    finished = _now()
    summary = {
        "invoices":  inv,
        "receipts":  rec,
        "products":  prods,
        "customers": cust,
        "flags":     flags,
        "totals": {
            "invoices":  (inv or {}).get("total", 0),
            "receipts":  (rec or {}).get("total", 0),
            "products":  (prods or {}).get("total", 0),
            "customers": (cust or {}).get("total", 0),
        },
    }
    await db.qoyod_fresh_start_audits.update_one(
        {"run_id": run_id},
        {"$set": {
            "finished_at":  finished,
            "status":       status,
            "summary":      summary,
            "error":        error,
        }})

    return {
        "ok":          status == "completed",
        "run_id":      run_id,
        "status":      status,
        "started_at":  started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "summary":     summary,
        "error":       error,
    }


async def latest_audit(db, *, user_id: str) -> Optional[dict]:
    """Returns the most recent audit snapshot for the tenant."""
    doc = await db.qoyod_fresh_start_audits.find_one(
        {"user_id": user_id}, sort=[("started_at", -1)],
        projection={"_id": 0})
    if not doc:
        return None
    for k in ("started_at", "finished_at"):
        if k in doc and hasattr(doc[k], "isoformat"):
            doc[k] = doc[k].isoformat()
    return doc
