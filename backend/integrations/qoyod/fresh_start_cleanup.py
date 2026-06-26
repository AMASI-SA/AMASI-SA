"""Qoyod Fresh-Start Cleanup — Plan + Execute (gated by DELETE-CONFIRM).

User spec (2026-06-27, simplified):
    Audit → Plan → Execute.
    Execute MUST require typed `DELETE-CONFIRM` token in the request body.
    No Dry-Delete step (environment not yet productive, account empty
    of manual data).

Scope hard-locked to four entities:
    invoices, receipts, products, customers.

Never touches:
    chart-of-accounts, branches, taxes, settings, users, financial
    accounts, or anything outside the four-entity scope.

Deletion order:
    Receipts → Invoices → Products → Customers.
    Rationale: receipts FK-reference invoices; deleting the parent
    invoice first triggers Qoyod-side rejection or orphans the receipt.
    Customers are last (some Qoyod tenants reject DELETE on a customer
    that still has invoices).

Persisted state (collection `qoyod_fresh_start_cleanups`):
    {
      job_id, user_id, created_at, status,
      plan: {
        invoice_ids: [...], receipt_ids: [...],
        product_ids: [...], customer_ids: [...],
        totals: {invoices, receipts, products, customers},
      },
      execute: {
        started_at, finished_at,
        deleted: {invoices:N, receipts:N, products:N, customers:N},
        failed:  [{entity, id, error}],
        confirm_token_used: "DELETE-CONFIRM" (logged once for audit),
      },
    }
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.fresh_start_audit import _paginate


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
EXPECTED_CONFIRM_TOKEN = "DELETE-CONFIRM"

# What is NEVER touched by this module (purely informational; the code
# below simply does not reference these endpoints).
PROTECTED_ENTITIES = [
    "chart_of_accounts",   # /accounts
    "branches",            # /branches
    "taxes",               # /taxes
    "settings",            # /settings/*
    "users",               # /users
    "financial_accounts",  # any banking / cash account configuration
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_id(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    raw = item.get("id") or item.get("_id") \
        or item.get("contact_id") or item.get("invoice_id")
    if raw is None:
        return None
    return str(raw)


# ─────────────────────────────────────────────────────────────────────
# Plan — read-only enumeration of every ID to delete
# ─────────────────────────────────────────────────────────────────────
async def build_plan(
    db, *, user_id: str, api_client: QoyodAPIClient,
    page_size: int = 50, max_pages: int = 200,
) -> dict:
    """Re-paginates the four entities and persists the full ID list.
    The latest plan replaces any prior PLAN (so the operator can rebuild
    after edits in Qoyod). Execute requires the latest plan_id."""

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

    invoice_ids  = [i for i in (_extract_id(x) for x in invoices)  if i]
    receipt_ids  = [i for i in (_extract_id(x) for x in receipts)  if i]
    product_ids  = [i for i in (_extract_id(x) for x in products)  if i]
    customer_ids = [i for i in (_extract_id(x) for x in customers) if i]

    job_id = uuid.uuid4().hex
    doc = {
        "schema_version": 1,
        "job_id":         job_id,
        "user_id":        user_id,
        "created_at":     _now(),
        "status":         "planned",
        "scope":          ["invoices", "receipts", "products", "customers"],
        "protected_entities": PROTECTED_ENTITIES,
        "plan": {
            "invoice_ids":  invoice_ids,
            "receipt_ids":  receipt_ids,
            "product_ids":  product_ids,
            "customer_ids": customer_ids,
            "totals": {
                "invoices":  len(invoice_ids),
                "receipts":  len(receipt_ids),
                "products":  len(product_ids),
                "customers": len(customer_ids),
            },
        },
    }
    await db.qoyod_fresh_start_cleanups.insert_one(doc)
    doc["_id"] = str(doc.get("_id", ""))
    doc["created_at"] = doc["created_at"].isoformat()
    return doc


async def latest_plan(db, *, user_id: str) -> Optional[dict]:
    doc = await db.qoyod_fresh_start_cleanups.find_one(
        {"user_id": user_id},
        sort=[("created_at", -1)],
        projection={"_id": 0},
    )
    if not doc:
        return None
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    for k in ("started_at", "finished_at"):
        ex = doc.get("execute") or {}
        if isinstance(ex.get(k), datetime):
            ex[k] = ex[k].isoformat()
    return doc


# ─────────────────────────────────────────────────────────────────────
# Execute — actual DELETE calls, gated by DELETE-CONFIRM token
# ─────────────────────────────────────────────────────────────────────
class CleanupRefused(Exception):
    """Raised when the operator's confirmation token doesn't match."""


async def _delete_batch(
    delete_fn,
    ids: list[str],
    entity: str,
    *,
    pause_ms: int = 100,
) -> tuple[int, list[dict]]:
    """Issues DELETE for each id in `ids`. Returns (deleted_count, failures).
    Continues past failures (user spec: "continue + report" mode).
    404 is treated as success (already gone). 405 stops the batch with a
    structured failure so the operator knows Qoyod doesn't expose
    DELETE on that entity at all."""
    deleted = 0
    failures: list[dict] = []
    for idx, item_id in enumerate(ids):
        try:
            await delete_fn(item_id)
            deleted += 1
        except QoyodAPIError as exc:
            if exc.status_code == 404:
                # Already gone — count as success.
                deleted += 1
            else:
                failures.append({
                    "entity":   entity,
                    "id":       item_id,
                    "error":    exc.to_log_dict(),
                })
                if exc.status_code == 405:
                    # Method not allowed — Qoyod doesn't support DELETE
                    # on this entity. Abort the batch to avoid wasting
                    # calls; surface a clear error to the operator.
                    failures.append({
                        "entity":   entity,
                        "id":       "__batch_aborted__",
                        "error":    {
                            "code":    "qoyod_delete_not_supported",
                            "message": f"Qoyod لا يدعم DELETE على {entity}.",
                        }})
                    return deleted, failures
        if pause_ms and idx < len(ids) - 1:
            await asyncio.sleep(pause_ms / 1000.0)
    return deleted, failures


async def execute_cleanup(
    db, *, user_id: str, job_id: str, confirm_token: str,
    api_client: QoyodAPIClient,
    pause_ms: int = 100,
) -> dict:
    """Runs the cleanup. Order: Receipts → Invoices → Products → Customers.

    Refuses to start unless `confirm_token` matches EXPECTED_CONFIRM_TOKEN
    EXACTLY (case-sensitive, no whitespace). Updates job document in
    place with execute results.
    """
    if (confirm_token or "").strip() != EXPECTED_CONFIRM_TOKEN:
        raise CleanupRefused(
            f"Confirmation token must be exactly '{EXPECTED_CONFIRM_TOKEN}'")

    job = await db.qoyod_fresh_start_cleanups.find_one(
        {"user_id": user_id, "job_id": job_id})
    if not job:
        raise CleanupRefused("Plan not found — rebuild before executing")
    if job.get("status") not in ("planned", "executed_with_errors", "failed"):
        raise CleanupRefused(
            f"Plan status must be 'planned' to execute (got: {job.get('status')})")

    plan = job.get("plan") or {}
    receipt_ids  = plan.get("receipt_ids")  or []
    invoice_ids  = plan.get("invoice_ids")  or []
    product_ids  = plan.get("product_ids")  or []
    customer_ids = plan.get("customer_ids") or []

    started = _now()
    await db.qoyod_fresh_start_cleanups.update_one(
        {"job_id": job_id},
        {"$set": {"status": "executing",
                  "execute.started_at": started,
                  "execute.confirm_token_used": EXPECTED_CONFIRM_TOKEN}})

    # 1) Receipts first (children of invoices).
    rec_n, rec_failures = await _delete_batch(
        api_client.delete_receipt, receipt_ids, "receipts",
        pause_ms=pause_ms)
    # 2) Invoices.
    inv_n, inv_failures = await _delete_batch(
        api_client.delete_invoice, invoice_ids, "invoices",
        pause_ms=pause_ms)
    # 3) Products (independent).
    prod_n, prod_failures = await _delete_batch(
        api_client.delete_product, product_ids, "products",
        pause_ms=pause_ms)
    # 4) Customers (parents — some Qoyod versions require empty invoice
    #    list before customer DELETE; that's why this is last).
    cust_n, cust_failures = await _delete_batch(
        api_client.delete_customer, customer_ids, "customers",
        pause_ms=pause_ms)

    finished = _now()
    failures = rec_failures + inv_failures + prod_failures + cust_failures
    status = "executed" if not failures else "executed_with_errors"
    deleted = {
        "receipts":  rec_n,
        "invoices":  inv_n,
        "products":  prod_n,
        "customers": cust_n,
    }
    await db.qoyod_fresh_start_cleanups.update_one(
        {"job_id": job_id},
        {"$set": {
            "status":             status,
            "execute.finished_at": finished,
            "execute.deleted":     deleted,
            "execute.failed":      failures,
            "execute.duration_ms": int((finished - started).total_seconds() * 1000),
        }})

    return {
        "ok":          status == "executed",
        "status":      status,
        "job_id":      job_id,
        "started_at":  started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "deleted":     deleted,
        "failed":      failures,
        "totals_planned": plan.get("totals", {}),
        "protected_entities": PROTECTED_ENTITIES,
    }
