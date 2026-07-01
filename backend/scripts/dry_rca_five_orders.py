#!/usr/bin/env python3
"""Iter-001k+ — DRY RCA for 5 Production orders (Read-Only).

Purpose
────────
For each of the five orders currently blocked on
`readiness_blockers = [dry_invoice_id, dry_customer_id,
dry_product_id, …]`, produce a compact table showing:

    • The DRY sentinel(s) present.
    • The customer lookup key (phone or email).
    • The SKU list.
    • Whether a REAL (non-DRY) Qoyod customer already exists for
      the same phone/email (adoption candidate).
    • Whether a REAL (non-DRY) Qoyod product already exists for
      the same SKU (adoption candidate).
    • A recommended_action.

Hard guarantees:
    • Read-Only. No writes. No Qoyod API. No send.
    • Doesn't touch settings / gates.
    • Doesn't create/adopt anything — this is a report only.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


# ── Target orders (Production, Iter-001k+ dashboard) ────────────────
ORDERS: list[str] = [
    "269629400",
    "269632660",
    "269604656",
    "269579732",
    "269640154",
]
USER_ID = "main"

# Payment methods that stay blocked until Iter-294 (Bank Transfer).
_BANK_TRANSFER_METHODS = {"bank_transfer", "bank", "wire_transfer"}


# ── Small utils ─────────────────────────────────────────────────────
def _is_dry_or_preview(v) -> bool:
    if v is None:
        return True
    s = str(v)
    return s.startswith("DRY:") or s.startswith("PREVIEW:")


def _normalise_phone(v) -> str | None:
    if not v:
        return None
    s = re.sub(r"[^\d+]", "", str(v))
    return s or None


async def _fetch_inbox_row(db, order_number: str) -> dict | None:
    """Latest inbox row for the order (all trace stages)."""
    q = {
        "user_id": USER_ID,
        "$or": [
            {"salla_order_number": order_number},
            {"canonical_payload.order_number": order_number},
        ],
    }
    cur = db.integration_inbox.find(q, {"_id": 0}).sort(
        [("received_at", -1)]).limit(3)
    rows = await cur.to_list(length=3)
    return rows[0] if rows else None


async def _find_real_customer_mapping(db, lookup_key: str | None) -> dict | None:
    if not lookup_key:
        return None
    cur = db.qoyod_customers_mapping.find(
        {"user_id": USER_ID, "lookup_key": lookup_key},
        {"_id": 0, "qoyod_customer_id": 1,
         "dry_run_only": 1, "lookup_key": 1})
    async for m in cur:
        cid = m.get("qoyod_customer_id")
        if m.get("dry_run_only"):
            continue
        if _is_dry_or_preview(cid):
            continue
        return {"qoyod_customer_id": cid, "lookup_key": lookup_key}
    return None


async def _find_real_customer_in_external(
        db, phone: str | None, email: str | None,
        display_name: str | None) -> dict | None:
    """Scan `qoyod_external_customers` — the snapshot of pre-existing
    Qoyod contacts from initial sync — for a match by phone / email
    / name."""
    ors: list[dict] = []
    if phone:
        ors.extend([{"mobile": phone}, {"phone": phone}])
    if email:
        ors.append({"email": (email or "").lower()})
    if not ors:
        return None
    doc = await db.qoyod_external_customers.find_one(
        {"user_id": USER_ID, "$or": ors},
        {"_id": 0, "qoyod_customer_id": 1, "name": 1,
         "mobile": 1, "phone": 1, "email": 1})
    if doc and not _is_dry_or_preview(doc.get("qoyod_customer_id")):
        return doc
    return None


async def _find_real_product_mapping(db, sku: str) -> dict | None:
    m = await db.qoyod_products_mapping.find_one(
        {"user_id": USER_ID, "sku": sku,
         "dry_run_only": {"$ne": True}},
        {"_id": 0, "qoyod_product_id": 1, "sku": 1})
    if not m:
        return None
    pid = m.get("qoyod_product_id")
    if _is_dry_or_preview(pid):
        return None
    return m


async def _find_real_product_in_external(db, sku: str) -> dict | None:
    """Scan `qoyod_external_products` for a match by SKU."""
    doc = await db.qoyod_external_products.find_one(
        {"user_id": USER_ID, "sku": sku},
        {"_id": 0, "qoyod_product_id": 1, "sku": 1, "name": 1})
    if doc and not _is_dry_or_preview(doc.get("qoyod_product_id")):
        return doc
    return None


async def _rca_one(db, order_number: str) -> dict:
    row = await _fetch_inbox_row(db, order_number)
    if not row:
        return {
            "order_number":     order_number,
            "found":            False,
            "note":             "no integration_inbox row",
        }

    canonical      = row.get("canonical_payload") or {}
    dry_invoice_id = row.get("existing_qoyod_invoice_id") \
        or canonical.get("existing_qoyod_invoice_id")
    dry_customer_id = row.get("qoyod_customer_id") \
        or canonical.get("qoyod_customer_id")
    payment_method = str(
        canonical.get("payment_method")
        or row.get("payment_method") or "").lower()

    # Customer lookup key mirrors `_check_customer_mapping` in eligible_orders.
    customer = canonical.get("customer") or {}
    phone = _normalise_phone(
        customer.get("mobile")
        or customer.get("phone")
        or canonical.get("customer_mobile"))
    email = (
        customer.get("email")
        or canonical.get("customer_email")
        or "").strip().lower() or None
    display_name = (customer.get("name")
                    or canonical.get("customer_name") or "")[:80]
    lookup_key = phone or email

    # Adoption candidates for the customer.
    real_map = await _find_real_customer_mapping(db, lookup_key)
    real_ext = await _find_real_customer_in_external(
        db, phone, email, display_name)
    has_real_customer_elsewhere = bool(real_map or real_ext)

    # Product side.
    skus: list[str] = []
    dry_product_ids: list[str] = []
    real_product_map_hits: list[dict] = []
    real_product_ext_hits: list[dict] = []
    for it in canonical.get("items") or []:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        skus.append(sku)
        pid = it.get("qoyod_product_id")
        if _is_dry_or_preview(pid):
            dry_product_ids.append(f"{sku}={pid}")
        m = await _find_real_product_mapping(db, sku)
        if m:
            real_product_map_hits.append({"sku": sku, **m})
        e = await _find_real_product_in_external(db, sku)
        if e:
            real_product_ext_hits.append({"sku": sku, **e})

    has_real_product_elsewhere = bool(
        real_product_map_hits or real_product_ext_hits)

    # ── Recommended action ─────────────────────────────────────
    recs: list[str] = []
    if payment_method in _BANK_TRANSFER_METHODS:
        recs.append("blocked_bank_transfer_iter_294")
    if _is_dry_or_preview(dry_invoice_id):
        # DRY invoice sentinel is a stale marker from earlier dry runs.
        # It should be ignored, NOT sent as an update to Qoyod.
        recs.append("ignore_dry_invoice_sentinel")
    if _is_dry_or_preview(dry_customer_id):
        if has_real_customer_elsewhere:
            recs.append("adopt_existing_customer")
        else:
            recs.append("needs_create_customer_later")
    if dry_product_ids:
        if has_real_product_elsewhere:
            recs.append("adopt_existing_product")
        else:
            recs.append("needs_create_product_later")
    if not recs:
        recs.append("no_dry_sentinels_detected")

    return {
        "order_number":                    order_number,
        "found":                           True,
        "payment_method":                  payment_method,
        "dry_invoice_id":                  dry_invoice_id,
        "dry_customer_id":                 dry_customer_id,
        "dry_product_ids":                 dry_product_ids,
        "customer_lookup_key":             lookup_key,
        "customer_display_name":           display_name,
        "product_skus":                    skus,
        "has_real_customer_mapping":       bool(real_map),
        "has_real_customer_in_external":   bool(real_ext),
        "real_customer_candidate": (
            real_map or real_ext or None),
        "has_real_product_mapping_any":    bool(real_product_map_hits),
        "has_real_product_in_external_any": bool(real_product_ext_hits),
        "real_product_map_hits":           real_product_map_hits,
        "real_product_ext_hits":           real_product_ext_hits,
        "recommended_action":              recs,
    }


async def main():
    load_dotenv("/app/backend/.env")
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]

    # ── Safety re-check: settings must be Fail-Closed ──────────
    s = await db.qoyod_settings.find_one(
        {"user_id": USER_ID},
        {"_id": 0, "selective_live_send_enabled": 1,
         "production_writes_locked": 1})
    if not s or s.get("selective_live_send_enabled") is True \
            or s.get("production_writes_locked") is False:
        sys.stderr.write(
            "REFUSING TO RUN — gates are not fail-closed.\n"
            f"settings snapshot: {json.dumps(s)}\n")
        sys.exit(2)

    n = await db.qoyod_write_lock_attempts.count_documents({})
    print(f"# gates snapshot: {json.dumps(s)}", file=sys.stderr)
    print(f"# write_lock_attempts count: {n}", file=sys.stderr)

    rows = []
    for order_number in ORDERS:
        r = await _rca_one(db, order_number)
        rows.append(r)

    print(json.dumps({
        "read_only":         True,
        "no_qoyod_api_calls": True,
        "no_db_writes":      True,
        "gates_snapshot":    s,
        "reports":           rows,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
