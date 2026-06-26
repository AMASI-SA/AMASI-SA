"""Qoyod Existing-Data Migration — read-only matching pre-flight.

Purpose (per 2026-06-26 user spec)
─────────────────────────────────
Before any Dry Run on real data, we need a one-shot reconciliation
between what already exists in Qoyod and the entities Mezan will
soon send (extracted from local Salla orders).

This module is STRICTLY read-only against Qoyod. It:
  1. Pulls every product/customer from Qoyod (paginated GET).
  2. Distils the distinct SKUs / customers from local Salla orders.
  3. Matches them according to the policy below.
  4. Persists local mapping rows AND a reconciliation report.

Matching policy (locked by user 2026-06-26)
───────────────────────────────────────────
Products
  • SKU match (exact, case-insensitive trim)     → auto_mapped
  • SKU match BUT name OR price differs          → mapped_with_warning
  • No SKU match, name match (case-insensitive)  → candidate_match
                                                   (NO auto mapping)
  • Anything else                                → unmapped

Customers
  • Phone match (E.164 normalized)               → auto_mapped
  • Email match (lower-cased)                    → auto_mapped
  • Name-only match                              → candidate_match
                                                   (NO auto mapping)
  • Anything else                                → unmapped

ADR-001 compliance
──────────────────
  #1  Additive   — new collections only; never mutates Qoyod.
  #10 Idempotency — re-running the migration is safe; mapping rows
                    are upserted by (user_id, mezan_key).
  #14 Secrets    — API key handled via the existing client; never
                    persisted in mapping rows.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError


# ─────────────────────────────────────────────────────────────────────
# Normalisation helpers
# ─────────────────────────────────────────────────────────────────────
def normalize_sku(sku: Any) -> str:
    """Trim + upper. Empty/None → ''."""
    if sku is None:
        return ""
    return str(sku).strip().upper()


def normalize_name(name: Any) -> str:
    """Trim + collapse whitespace + lower. Empty/None → ''."""
    if name is None:
        return ""
    return re.sub(r"\s+", " ", str(name)).strip().lower()


def normalize_phone(phone: Any) -> str:
    """Best-effort Saudi/E.164 normalisation.

    Returns '' when the input cannot be confidently turned into a
    canonical phone (we'd rather skip the match than produce a false
    positive that links two different customers).
    """
    if phone is None:
        return ""
    raw = re.sub(r"\D+", "", str(phone))  # digits only
    if not raw:
        return ""
    # Already includes country code
    if raw.startswith("00966"):
        raw = raw[2:]                       # → 966...
    if raw.startswith("966") and len(raw) >= 12:
        return "+" + raw[:12]
    # Local 05XXXXXXXX  → +9665XXXXXXXX
    if raw.startswith("05") and len(raw) == 10:
        return "+966" + raw[1:]
    # Bare 5XXXXXXXX (missing leading 0) → +9665XXXXXXXX
    if raw.startswith("5") and len(raw) == 9:
        return "+966" + raw
    # Non-Saudi but unambiguous (already + prefixed length 10-15)
    if len(raw) >= 10 and len(raw) <= 15:
        return "+" + raw
    return ""


def normalize_email(email: Any) -> str:
    if email is None:
        return ""
    s = str(email).strip().lower()
    return s if "@" in s else ""


# ─────────────────────────────────────────────────────────────────────
# Run / report data shapes
# ─────────────────────────────────────────────────────────────────────
@dataclass
class MigrationCounts:
    products_mapped: int = 0
    products_mapped_with_warning: int = 0
    products_candidate: int = 0
    products_unmapped: int = 0
    products_sku_mismatch_warnings: int = 0
    customers_mapped: int = 0
    customers_candidate: int = 0
    customers_unmapped: int = 0
    needs_manual_review: int = 0          # candidate_match (both kinds)
    qoyod_products_imported: int = 0
    qoyod_customers_imported: int = 0
    mezan_products_distinct: int = 0
    mezan_customers_distinct: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ─────────────────────────────────────────────────────────────────────
# Step 1 — Pull Qoyod catalogues into snapshot collections
# ─────────────────────────────────────────────────────────────────────
async def import_qoyod_products(
    db, *, user_id: str, api_client: QoyodAPIClient,
    page_size: int = 50, max_pages: int = 200,
) -> int:
    """Paginate GET /products and upsert into `qoyod_external_products`.

    Returns the number of distinct Qoyod products written.
    Safe to re-run: upserts by (user_id, qoyod_id).
    """
    now = datetime.now(timezone.utc)
    seen = 0
    for page in range(1, max_pages + 1):
        resp = await api_client.list_products(page=page, limit=page_size)
        items = _extract_list(resp, ("products", "data", "items"))
        if not items:
            break
        for it in items:
            qid = str(it.get("id") or it.get("product_id") or "")
            if not qid:
                continue
            doc = {
                "schema_version":  1,
                "user_id":         user_id,
                "qoyod_id":        qid,
                "sku":             it.get("sku") or it.get("code") or "",
                "name":            it.get("name") or it.get("name_ar") or "",
                "price":           _coerce_float(
                    it.get("price") or it.get("sale_price")
                    or it.get("unit_price")),
                "currency":        it.get("currency") or "SAR",
                "snapshot_at":     now,
                # Normalised lookup fields (indexed for matcher)
                "sku_norm":        normalize_sku(
                    it.get("sku") or it.get("code")),
                "name_norm":       normalize_name(
                    it.get("name") or it.get("name_ar")),
            }
            await db.qoyod_external_products.update_one(
                {"user_id": user_id, "qoyod_id": qid},
                {"$set": doc}, upsert=True)
            seen += 1
        if len(items) < page_size:
            break
    return seen


async def import_qoyod_customers(
    db, *, user_id: str, api_client: QoyodAPIClient,
    page_size: int = 50, max_pages: int = 200,
) -> int:
    """Paginate GET /customers and upsert into `qoyod_external_customers`."""
    now = datetime.now(timezone.utc)
    seen = 0
    for page in range(1, max_pages + 1):
        resp = await api_client.list_contacts(page=page, limit=page_size)
        items = _extract_list(resp, ("customers", "contacts", "data", "items"))
        if not items:
            break
        for it in items:
            qid = str(it.get("id") or it.get("customer_id") or "")
            if not qid:
                continue
            phone_raw = (it.get("phone") or it.get("mobile")
                         or it.get("phone_number") or "")
            email_raw = it.get("email") or ""
            name_raw  = it.get("name") or it.get("display_name") or ""
            doc = {
                "schema_version":  1,
                "user_id":         user_id,
                "qoyod_id":        qid,
                "name":            name_raw,
                "phone":           phone_raw,
                "email":           email_raw,
                "snapshot_at":     now,
                "phone_norm":      normalize_phone(phone_raw),
                "email_norm":      normalize_email(email_raw),
                "name_norm":       normalize_name(name_raw),
            }
            await db.qoyod_external_customers.update_one(
                {"user_id": user_id, "qoyod_id": qid},
                {"$set": doc}, upsert=True)
            seen += 1
        if len(items) < page_size:
            break
    return seen


# ─────────────────────────────────────────────────────────────────────
# Step 2 — Distil Mezan side (from local Salla orders)
# ─────────────────────────────────────────────────────────────────────
async def extract_mezan_products(db, *, user_id: str) -> list[dict]:
    """Distinct products from `order_items` keyed by SKU (or by name when
    no SKU is present). Returns a list of dicts with normalised fields.

    `last_order_date` is the most recent order date the SKU appears on.
    We use `unified_orders.order_date` as the authoritative signal,
    falling back to `received_at` and finally to `order_items.created_at`.
    """
    # Build {order_number: best_date_string} once.
    order_dates: dict[str, str] = {}
    cursor = db.unified_orders.find(
        {"user_id": user_id},
        {"order_number": 1, "order_date": 1, "received_at": 1, "_id": 0})
    async for o in cursor:
        on = o.get("order_number")
        if not on:
            continue
        order_dates[on] = (o.get("order_date") or o.get("received_at") or "")

    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id":         {"$ifNull": ["$sku", None]},
            "sku":         {"$first": "$sku"},
            "name":        {"$first": "$product_name"},
            "unit_price":  {"$first": "$unit_price"},
            "occurrences": {"$sum": 1},
            "order_numbers": {"$addToSet": "$order_number"},
            "item_created_max": {"$max": "$created_at"},
        }},
    ]
    rows = await db.order_items.aggregate(pipeline).to_list(length=10000)
    out: list[dict] = []
    for r in rows:
        sku  = r.get("sku") or ""
        name = r.get("name") or ""
        if not sku and not name:
            continue
        # Pick the max order_date over the SKU's order_numbers
        best_date = ""
        for on in (r.get("order_numbers") or []):
            d = order_dates.get(on) or ""
            if d and d > best_date:
                best_date = d
        last_order_date = best_date or (r.get("item_created_max") or "")
        out.append({
            "sku":              sku,
            "name":             name,
            "unit_price":       _coerce_float(r.get("unit_price")),
            "occurrences":      r.get("occurrences", 0),
            "sku_norm":         normalize_sku(sku),
            "name_norm":        normalize_name(name),
            "last_order_date":  last_order_date or None,
        })
    return out


async def extract_mezan_customers(db, *, user_id: str) -> list[dict]:
    """Distinct customers from `unified_orders.raw` + `custom_app_customers`.

    De-duplication priority: phone (E.164) > email (lower) > name.
    `last_order_date` is the most recent `order_date` (falling back to
    `received_at`) the customer appears on. For `custom_app_customers`
    rows that have no order, we use `updated_at`/`created_at`.
    """
    bucket: dict[str, dict] = {}

    def _add(name: str, phone: str, email: str, *,
             when: str = "", occurrences: int = 1):
        p_norm = normalize_phone(phone)
        e_norm = normalize_email(email)
        n_norm = normalize_name(name)
        if p_norm:
            key = "P:" + p_norm
        elif e_norm:
            key = "E:" + e_norm
        elif n_norm:
            key = "N:" + n_norm
        else:
            return
        cur = bucket.get(key)
        if cur:
            cur["occurrences"] += occurrences
            if when and (not cur.get("last_order_date")
                          or when > cur["last_order_date"]):
                cur["last_order_date"] = when
            return
        bucket[key] = {
            "name":             name or "",
            "phone":            phone or "",
            "email":            email or "",
            "occurrences":      occurrences,
            "phone_norm":       p_norm,
            "email_norm":       e_norm,
            "name_norm":        n_norm,
            "last_order_date":  when or None,
        }

    # Source 1: unified_orders — authoritative for "last order date"
    cursor = db.unified_orders.find(
        {"user_id": user_id},
        {"customer_name": 1, "raw.customer_mobile": 1,
         "raw.customer_email": 1, "raw.customer_name": 1,
         "order_date": 1, "received_at": 1, "_id": 0})
    async for o in cursor:
        raw = o.get("raw") or {}
        when = o.get("order_date") or o.get("received_at") or ""
        _add(
            o.get("customer_name") or raw.get("customer_name") or "",
            raw.get("customer_mobile") or raw.get("customer_phone") or "",
            raw.get("customer_email") or "",
            when=when,
        )

    # Source 2: custom_app_customers (manually entered, no order linked)
    cursor2 = db.custom_app_customers.find(
        {"user_id": user_id},
        {"name": 1, "mobile": 1, "email": 1,
         "updated_at": 1, "created_at": 1, "_id": 0})
    async for c in cursor2:
        when = c.get("updated_at") or c.get("created_at") or ""
        if hasattr(when, "isoformat"):
            when = when.isoformat()
        _add(c.get("name") or "",
             c.get("mobile") or "",
             c.get("email") or "",
             when=str(when) if when else "")

    return list(bucket.values())


# ─────────────────────────────────────────────────────────────────────
# Step 3 — Match
# ─────────────────────────────────────────────────────────────────────
def _classify_product_match(
    mz: dict, qoyod_by_sku: dict, qoyod_by_name: dict,
) -> dict:
    sku_norm  = mz["sku_norm"]
    name_norm = mz["name_norm"]
    if sku_norm and sku_norm in qoyod_by_sku:
        q = qoyod_by_sku[sku_norm]
        warnings: list[str] = []
        if name_norm and q["name_norm"] and name_norm != q["name_norm"]:
            warnings.append("name_differs")
        if mz["unit_price"] and q["price"] and \
                abs(mz["unit_price"] - q["price"]) > 0.009:
            warnings.append("price_differs")
        return {
            "status":        "mapped_with_warning" if warnings else "auto_mapped",
            "qoyod_id":      q["qoyod_id"],
            "matched_on":    "sku",
            "warnings":      warnings,
            "qoyod_snapshot": q,
        }
    if name_norm and name_norm in qoyod_by_name:
        q = qoyod_by_name[name_norm]
        return {
            "status":        "candidate_match",
            "qoyod_id":      None,                  # NO auto mapping
            "candidate_qoyod_id": q["qoyod_id"],
            "matched_on":    "name_only",
            "warnings":      ["name_only_match_requires_manual_review"],
            "qoyod_snapshot": q,
        }
    return {
        "status":     "unmapped",
        "qoyod_id":   None,
        "matched_on": None,
        "warnings":   [],
        "qoyod_snapshot": None,
    }


def _classify_customer_match(
    mz: dict, qoyod_by_phone: dict, qoyod_by_email: dict, qoyod_by_name: dict,
) -> dict:
    p, e, n = mz["phone_norm"], mz["email_norm"], mz["name_norm"]
    if p and p in qoyod_by_phone:
        q = qoyod_by_phone[p]
        return {"status": "auto_mapped", "qoyod_id": q["qoyod_id"],
                "matched_on": "phone", "warnings": [], "qoyod_snapshot": q}
    if e and e in qoyod_by_email:
        q = qoyod_by_email[e]
        return {"status": "auto_mapped", "qoyod_id": q["qoyod_id"],
                "matched_on": "email", "warnings": [], "qoyod_snapshot": q}
    if n and n in qoyod_by_name:
        q = qoyod_by_name[n]
        return {"status":     "candidate_match",
                "qoyod_id":   None,
                "candidate_qoyod_id": q["qoyod_id"],
                "matched_on": "name_only",
                "warnings":   ["name_only_match_requires_manual_review"],
                "qoyod_snapshot": q}
    return {"status": "unmapped", "qoyod_id": None,
            "matched_on": None, "warnings": [], "qoyod_snapshot": None}


async def match_products(db, *, user_id: str, run_id: str) -> dict:
    """Build qoyod_external_products lookup, classify each Mezan SKU,
    and upsert into `qoyod_migration_products`. Returns counts."""
    qoyod_by_sku:  dict[str, dict] = {}
    qoyod_by_name: dict[str, dict] = {}
    async for q in db.qoyod_external_products.find({"user_id": user_id}):
        if q.get("sku_norm"):
            qoyod_by_sku.setdefault(q["sku_norm"], q)
        if q.get("name_norm"):
            qoyod_by_name.setdefault(q["name_norm"], q)

    mezan = await extract_mezan_products(db, user_id=user_id)
    counts = {"auto_mapped": 0, "mapped_with_warning": 0,
              "candidate_match": 0, "unmapped": 0,
              "sku_mismatch_warnings": 0}
    now = datetime.now(timezone.utc)
    for mz in mezan:
        cls = _classify_product_match(mz, qoyod_by_sku, qoyod_by_name)
        counts[cls["status"]] += 1
        if "name_differs" in cls["warnings"] or \
                "price_differs" in cls["warnings"]:
            counts["sku_mismatch_warnings"] += 1
        key = ("SKU:" + mz["sku_norm"]) if mz["sku_norm"] \
            else ("NAME:" + mz["name_norm"])
        # Only sanitise the Qoyod snapshot for storage (no full doc).
        snap = cls["qoyod_snapshot"] or {}
        snap_clean = {
            "qoyod_id": snap.get("qoyod_id"),
            "sku":      snap.get("sku"),
            "name":     snap.get("name"),
            "price":    snap.get("price"),
        } if snap else None
        await db.qoyod_migration_products.update_one(
            {"user_id": user_id, "mezan_key": key},
            {"$set": {
                "schema_version":     1,
                "user_id":            user_id,
                "run_id":             run_id,
                "mezan_key":          key,
                "mezan_sku":          mz["sku"],
                "mezan_name":         mz["name"],
                "mezan_unit_price":   mz["unit_price"],
                "occurrences":        mz["occurrences"],
                "last_order_date":    mz.get("last_order_date"),
                "status":             cls["status"],
                "qoyod_product_id":   cls["qoyod_id"],
                "candidate_qoyod_id": cls.get("candidate_qoyod_id"),
                "matched_on":         cls["matched_on"],
                "warnings":           cls["warnings"],
                "qoyod_snapshot":     snap_clean,
                "updated_at":         now,
             },
             "$setOnInsert": {"created_at": now}},
            upsert=True)
    counts["mezan_distinct"] = len(mezan)
    return counts


async def match_customers(db, *, user_id: str, run_id: str) -> dict:
    qoyod_by_phone: dict[str, dict] = {}
    qoyod_by_email: dict[str, dict] = {}
    qoyod_by_name:  dict[str, dict] = {}
    async for q in db.qoyod_external_customers.find({"user_id": user_id}):
        if q.get("phone_norm"):
            qoyod_by_phone.setdefault(q["phone_norm"], q)
        if q.get("email_norm"):
            qoyod_by_email.setdefault(q["email_norm"], q)
        if q.get("name_norm"):
            qoyod_by_name.setdefault(q["name_norm"], q)

    mezan = await extract_mezan_customers(db, user_id=user_id)
    counts = {"auto_mapped": 0, "candidate_match": 0, "unmapped": 0}
    now = datetime.now(timezone.utc)
    for mz in mezan:
        cls = _classify_customer_match(
            mz, qoyod_by_phone, qoyod_by_email, qoyod_by_name)
        counts[cls["status"]] += 1
        if mz["phone_norm"]:
            key = "P:" + mz["phone_norm"]
        elif mz["email_norm"]:
            key = "E:" + mz["email_norm"]
        else:
            key = "N:" + mz["name_norm"]
        snap = cls["qoyod_snapshot"] or {}
        snap_clean = {
            "qoyod_id": snap.get("qoyod_id"),
            "name":     snap.get("name"),
            "phone":    snap.get("phone"),
            "email":    snap.get("email"),
        } if snap else None
        await db.qoyod_migration_customers.update_one(
            {"user_id": user_id, "mezan_key": key},
            {"$set": {
                "schema_version":     1,
                "user_id":            user_id,
                "run_id":             run_id,
                "mezan_key":          key,
                "mezan_name":         mz["name"],
                "mezan_phone":        mz["phone"],
                "mezan_email":        mz["email"],
                "occurrences":        mz["occurrences"],
                "last_order_date":    mz.get("last_order_date"),
                "status":             cls["status"],
                "qoyod_customer_id":  cls["qoyod_id"],
                "candidate_qoyod_id": cls.get("candidate_qoyod_id"),
                "matched_on":         cls["matched_on"],
                "warnings":           cls["warnings"],
                "qoyod_snapshot":     snap_clean,
                "updated_at":         now,
             },
             "$setOnInsert": {"created_at": now}},
            upsert=True)
    counts["mezan_distinct"] = len(mezan)
    return counts


# ─────────────────────────────────────────────────────────────────────
# Step 4 — Orchestrator
# ─────────────────────────────────────────────────────────────────────
async def run_migration(
    db, *, user_id: str, api_client: QoyodAPIClient,
) -> dict:
    """End-to-end read-only migration. Returns the run summary doc."""
    run_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    await db.qoyod_migration_runs.insert_one({
        "schema_version": 1,
        "run_id":     run_id,
        "user_id":    user_id,
        "started_at": started,
        "status":     "running",
    })

    error: Optional[dict] = None
    try:
        q_products = await import_qoyod_products(
            db, user_id=user_id, api_client=api_client)
        q_customers = await import_qoyod_customers(
            db, user_id=user_id, api_client=api_client)
        prod_counts = await match_products(
            db, user_id=user_id, run_id=run_id)
        cust_counts = await match_customers(
            db, user_id=user_id, run_id=run_id)
        status = "completed"
    except QoyodAPIError as exc:
        error = exc.to_log_dict()
        q_products = q_customers = 0
        prod_counts = {"auto_mapped": 0, "mapped_with_warning": 0,
                       "candidate_match": 0, "unmapped": 0,
                       "sku_mismatch_warnings": 0, "mezan_distinct": 0}
        cust_counts = {"auto_mapped": 0, "candidate_match": 0,
                       "unmapped": 0, "mezan_distinct": 0}
        status = "failed"

    finished = datetime.now(timezone.utc)
    summary = {
        "qoyod_products_imported":         q_products,
        "qoyod_customers_imported":        q_customers,
        "mezan_products_distinct":         prod_counts.get("mezan_distinct", 0),
        "mezan_customers_distinct":        cust_counts.get("mezan_distinct", 0),
        "products_mapped":                 prod_counts.get("auto_mapped", 0),
        "products_mapped_with_warning":    prod_counts.get(
                                              "mapped_with_warning", 0),
        "products_candidate":              prod_counts.get(
                                              "candidate_match", 0),
        "products_unmapped":               prod_counts.get("unmapped", 0),
        "products_sku_mismatch_warnings":  prod_counts.get(
                                              "sku_mismatch_warnings", 0),
        "customers_mapped":                cust_counts.get("auto_mapped", 0),
        "customers_candidate":             cust_counts.get(
                                              "candidate_match", 0),
        "customers_unmapped":              cust_counts.get("unmapped", 0),
        "needs_manual_review": (
            prod_counts.get("candidate_match", 0)
            + cust_counts.get("candidate_match", 0)),
    }
    await db.qoyod_migration_runs.update_one(
        {"run_id": run_id},
        {"$set": {
            "finished_at": finished,
            "status":      status,
            "summary":     summary,
            "error":       error,
        }})
    return {"run_id": run_id, "status": status,
            "summary": summary, "error": error,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat()}


async def latest_run(db, *, user_id: str) -> Optional[dict]:
    doc = await db.qoyod_migration_runs.find_one(
        {"user_id": user_id}, sort=[("started_at", -1)], projection={"_id": 0})
    return doc


# ─────────────────────────────────────────────────────────────────────
# Manual confirmation for candidate matches
# ─────────────────────────────────────────────────────────────────────
async def confirm_candidate(
    db, *, user_id: str, kind: str, mezan_key: str,
    qoyod_id: str, confirmed_by: str = "user",
) -> dict:
    """Manually accept a candidate (or override an existing mapping).

    Only collections owned by the migration layer are touched. The
    runtime resolver mapping tables (`qoyod_products_mapping` /
    `qoyod_customers_mapping`) are populated at first Dry Run, not here.
    """
    if kind not in ("products", "customers"):
        raise ValueError("kind must be 'products' or 'customers'")
    coll = db[f"qoyod_migration_{kind}"]
    now = datetime.now(timezone.utc)
    field_id = "qoyod_product_id" if kind == "products" else "qoyod_customer_id"
    res = await coll.update_one(
        {"user_id": user_id, "mezan_key": mezan_key},
        {"$set": {
            "status":        "auto_mapped",
            field_id:        qoyod_id,
            "matched_on":    "manual_confirmation",
            "confirmed_by":  confirmed_by,
            "confirmed_at":  now,
            "updated_at":    now,
        }})
    return {"matched": res.matched_count, "modified": res.modified_count}


# ─────────────────────────────────────────────────────────────────────
# Internal utilities
# ─────────────────────────────────────────────────────────────────────
def _extract_list(resp: Any, keys: tuple[str, ...]) -> list:
    """Qoyod responses sometimes use {products: [...]}, sometimes raw lists."""
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []
    for k in keys:
        v = resp.get(k)
        if isinstance(v, list):
            return v
    return []


def _coerce_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
