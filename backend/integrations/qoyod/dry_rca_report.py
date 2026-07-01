"""Iter-001k+ — Admin Read-Only DRY RCA endpoint.

Mirrors `scripts/dry_rca_five_orders.py` behaviour behind an
admin-only HTTP GET so operators without shell access can still
run the diagnostic on Production.

Contract (STRICT):
    • Read-Only. Zero DB writes.
    • Zero Qoyod API calls.
    • Refuses to execute unless gates are Fail-Closed:
        - selective_live_send_enabled = False
        - production_writes_locked    = True
    • No adopt / no create / no send / no gate flip.
    • Emits the SAME per-order fields the script emits.
"""
from __future__ import annotations

import re
from typing import Any, Optional


_BANK_TRANSFER_METHODS = {"bank_transfer", "bank", "wire_transfer"}


def _is_dry_or_preview(v: Any) -> bool:
    if v is None:
        return True
    s = str(v)
    return s.startswith("DRY:") or s.startswith("PREVIEW:")


def _normalise_phone(v: Any) -> Optional[str]:
    if not v:
        return None
    s = re.sub(r"[^\d+]", "", str(v))
    return s or None


async def _fetch_inbox_row(db, user_id: str, order_number: str
                           ) -> Optional[dict]:
    q = {
        "user_id": user_id,
        "$or": [
            {"salla_order_number": order_number},
            {"canonical_payload.order_number": order_number},
        ],
    }
    cur = db.integration_inbox.find(q, {"_id": 0}).sort(
        [("received_at", -1)]).limit(3)
    rows = await cur.to_list(length=3)
    return rows[0] if rows else None


async def _find_real_customer_mapping(
    db, user_id: str, lookup_key: Optional[str],
) -> Optional[dict]:
    if not lookup_key:
        return None
    cur = db.qoyod_customers_mapping.find(
        {"user_id": user_id, "lookup_key": lookup_key},
        {"_id": 0, "qoyod_customer_id": 1,
         "dry_run_only": 1, "lookup_key": 1})
    async for m in cur:
        if m.get("dry_run_only"):
            continue
        if _is_dry_or_preview(m.get("qoyod_customer_id")):
            continue
        return {
            "qoyod_customer_id": m.get("qoyod_customer_id"),
            "lookup_key":        lookup_key,
        }
    return None


async def _find_real_customer_in_external(
    db, user_id: str, phone: Optional[str],
    email: Optional[str],
) -> Optional[dict]:
    ors: list[dict] = []
    if phone:
        ors.extend([{"mobile": phone}, {"phone": phone}])
    if email:
        ors.append({"email": (email or "").lower()})
    if not ors:
        return None
    doc = await db.qoyod_external_customers.find_one(
        {"user_id": user_id, "$or": ors},
        {"_id": 0, "qoyod_customer_id": 1, "name": 1,
         "mobile": 1, "phone": 1, "email": 1})
    if doc and not _is_dry_or_preview(doc.get("qoyod_customer_id")):
        return doc
    return None


async def _find_real_product_mapping(
    db, user_id: str, sku: str,
) -> Optional[dict]:
    m = await db.qoyod_products_mapping.find_one(
        {"user_id": user_id, "sku": sku,
         "dry_run_only": {"$ne": True}},
        {"_id": 0, "qoyod_product_id": 1, "sku": 1})
    if not m:
        return None
    if _is_dry_or_preview(m.get("qoyod_product_id")):
        return None
    return m


async def _find_real_product_in_external(
    db, user_id: str, sku: str,
) -> Optional[dict]:
    doc = await db.qoyod_external_products.find_one(
        {"user_id": user_id, "sku": sku},
        {"_id": 0, "qoyod_product_id": 1, "sku": 1, "name": 1})
    if doc and not _is_dry_or_preview(doc.get("qoyod_product_id")):
        return doc
    return None


async def _rca_one(db, user_id: str, order_number: str) -> dict:
    row = await _fetch_inbox_row(db, user_id, order_number)
    if not row:
        return {
            "order_number":     order_number,
            "found":            False,
            "note":             "no integration_inbox row",
        }
    canonical = row.get("canonical_payload") or {}
    dry_invoice_id = row.get("existing_qoyod_invoice_id") \
        or canonical.get("existing_qoyod_invoice_id")
    dry_customer_id = row.get("qoyod_customer_id") \
        or canonical.get("qoyod_customer_id")
    payment_method = str(
        canonical.get("payment_method")
        or row.get("payment_method") or "").lower()
    status = str(canonical.get("status")
                 or canonical.get("order_status")
                 or row.get("pipeline_stage") or "")

    customer = canonical.get("customer") or {}
    phone = _normalise_phone(
        customer.get("mobile")
        or customer.get("phone")
        or canonical.get("customer_mobile"))
    email = (
        customer.get("email")
        or canonical.get("customer_email")
        or "").strip().lower() or None
    display_name = (
        customer.get("name")
        or canonical.get("customer_name") or "")[:80]
    lookup_key = phone or email

    real_map = await _find_real_customer_mapping(
        db, user_id, lookup_key)
    real_ext = await _find_real_customer_in_external(
        db, user_id, phone, email)

    skus: list[str] = []
    product_names: dict[str, str] = {}
    dry_product_ids: list[str] = []
    real_product_map_hits: list[dict] = []
    real_product_ext_hits: list[dict] = []
    per_sku_status: list[dict] = []
    for it in canonical.get("items") or []:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        skus.append(sku)
        name = (it.get("name") or "")[:80]
        if name:
            product_names[sku] = name
        pid = it.get("qoyod_product_id")
        if _is_dry_or_preview(pid):
            dry_product_ids.append(f"{sku}={pid}")
        m = await _find_real_product_mapping(db, user_id, sku)
        if m:
            real_product_map_hits.append({"sku": sku, **m})
        e = await _find_real_product_in_external(db, user_id, sku)
        if e:
            real_product_ext_hits.append({"sku": sku, **e})
        # ── Iter-001k+ fix #2: per-SKU recommendation ─────────
        if m:
            sku_rec = "adopt_existing_product"
            real_target: Any = m.get("qoyod_product_id")
            real_source = "qoyod_products_mapping"
        elif e:
            sku_rec = "adopt_existing_product"
            real_target = e.get("qoyod_product_id")
            real_source = "qoyod_external_products"
        else:
            sku_rec = "needs_create_product_later"
            real_target = None
            real_source = None
        per_sku_status.append({
            "sku":                    sku,
            "name":                   name or None,
            "current_qoyod_product_id_on_order": pid,
            "is_dry":                 _is_dry_or_preview(pid),
            "real_qoyod_product_id":  real_target,
            "real_source":            real_source,
            "recommendation":         sku_rec,
        })

    has_real_customer_elsewhere = bool(real_map or real_ext)
    # Product-level aggregate: mixed / adopt_all / create_all.
    any_needs_create = any(
        s["recommendation"] == "needs_create_product_later"
        for s in per_sku_status)
    any_adopt = any(
        s["recommendation"] == "adopt_existing_product"
        for s in per_sku_status)

    recs: list[str] = []
    if payment_method in _BANK_TRANSFER_METHODS:
        recs.append("blocked_bank_transfer_iter_294")
    if _is_dry_or_preview(dry_invoice_id):
        recs.append("ignore_dry_invoice_sentinel")
    if _is_dry_or_preview(dry_customer_id):
        recs.append("adopt_existing_customer"
                    if has_real_customer_elsewhere
                    else "needs_create_customer_later")
    if dry_product_ids or any_needs_create or any_adopt:
        if any_adopt:
            recs.append("adopt_existing_product")
        if any_needs_create:
            recs.append("needs_create_product_later")

    return {
        "order_number":                     order_number,
        "found":                            True,
        "payment_method":                   payment_method,
        "status":                           status,
        "dry_invoice_id":                   dry_invoice_id,
        "dry_customer_id":                  dry_customer_id,
        "dry_product_ids":                  dry_product_ids,
        "customer_lookup_key":              lookup_key,
        "customer_display_name":            display_name,
        "product_skus":                     skus,
        "product_names":                    product_names,
        "has_real_customer_mapping":        bool(real_map),
        "real_customer_mapping":            real_map,
        "has_real_customer_in_external":    bool(real_ext),
        "real_customer_candidate":          (real_map or real_ext),
        "has_real_product_mapping_any":     bool(real_product_map_hits),
        "real_product_mappings":            real_product_map_hits,
        "has_real_product_in_external_any": bool(real_product_ext_hits),
        "real_product_ext_hits":            real_product_ext_hits,
        "per_sku_recommendation":           per_sku_status,
        "recommended_action":               recs,
    }


class GatesNotFailClosedError(RuntimeError):
    """Raised when this endpoint is asked to run while the gates are
    open — the diagnostic refuses to expose data unless the system
    is in strict Fail-Closed."""


async def build_dry_rca_report(
    db,
    *,
    user_id: str,
    order_numbers: list[str],
) -> dict:
    """Read-Only DRY-sentinel RCA for `order_numbers`. Refuses to
    execute unless gates are Fail-Closed."""
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id},
        {"_id": 0,
         "selective_live_send_enabled":            1,
         "production_writes_locked":               1,
         "qoyod_sync_start_date":                  1,
         "qoyod_tax_period":                       1,
         "bank_transfer_routing_enabled":          1,
         "qoyod_invoice_date_source":              1,
         "qoyod_enabled_invoice_trigger_statuses": 1,
         }) or {}
    if settings.get("selective_live_send_enabled") is True or \
            settings.get("production_writes_locked") is False:
        raise GatesNotFailClosedError(
            "DRY RCA endpoint refuses to run while gates are not "
            "Fail-Closed. Current: "
            f"selective_live_send_enabled="
            f"{settings.get('selective_live_send_enabled')}, "
            f"production_writes_locked="
            f"{settings.get('production_writes_locked')}.")

    reports = []
    for n in order_numbers:
        r = await _rca_one(db, user_id, str(n).strip())
        reports.append(r)

    return {
        "read_only":         True,
        "no_qoyod_api_calls": True,
        "no_db_writes":      True,
        "gates_snapshot":    settings,
        "reports":           reports,
    }
