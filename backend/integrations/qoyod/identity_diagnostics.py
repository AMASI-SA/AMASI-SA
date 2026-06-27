"""Qoyod tenant-identity diagnostics.

User concern (2026-02-26): QYD-GO reported "38 products in Qoyod" but
the Qoyod UI for the same account showed none. This module probes the
Qoyod API directly and returns enough evidence for the operator to
verify that the API key Mezan is using belongs to the SAME Qoyod
tenant they see in the Qoyod web UI.

What we surface (no cache, no migration data, no local collection):
    • Mezan-side: `base_url`, `api_key_fingerprint` (sha256 prefix, never
                  the raw key).
    • Qoyod-side identity hints: `/branches` typically returns the
      organisation name + branch list (Qoyod doesn't expose a single
      `/me` endpoint, so we use branches as the tenant fingerprint).
    • First 5 products with id/name/sku.
    • First 5 customers with id/name.
    • Raw `meta` from each list call so the user can compare counts.

If the products/customers shown here do NOT match what the operator
sees in the Qoyod UI, the API key is connected to the wrong tenant
and Go-Live MUST be blocked until corrected.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.credentials import get_api_key


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key_fingerprint(api_key: str) -> str:
    """A short stable fingerprint of the API key so the operator can
    tell `which` key Mezan is using without ever exposing the raw
    value. SHA-256 of the key, first 12 hex chars."""
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def _sample(rows: Any, picker) -> list[dict]:
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:5]:
        if isinstance(row, dict):
            out.append(picker(row))
    return out


async def _call(api_client: QoyodAPIClient, fn, endpoint: str) -> dict:
    """Wrap a single Qoyod call with consistent error handling."""
    try:
        body = await fn()
        return {"ok": True, "endpoint": endpoint, "response": body}
    except QoyodAPIError as exc:
        return {"ok": False, "endpoint": endpoint,
                "error": exc.to_log_dict()}
    except Exception as exc:   # pragma: no cover — defensive
        return {"ok": False, "endpoint": endpoint,
                "error": {"code": "exception",
                          "message": exc.__class__.__name__}}


async def run_identity_diagnostics(db, user_id: str) -> dict:
    """Probe Qoyod with the current API key and return everything the
    operator needs to verify tenant identity.

    Always populates `mezan` (what Mezan is using) and `qoyod` (what
    Qoyod sees), even on partial failure — the operator should be able
    to read partial diagnostics when SOME endpoints are forbidden.
    """
    queried_at = _now_iso()
    base_url = os.environ.get("QOYOD_API_BASE", "")

    api_key = await get_api_key(db, user_id)
    mezan = {
        "base_url":            base_url,
        "user_id":             user_id,
        "api_key_present":     bool(api_key),
        "api_key_fingerprint": _key_fingerprint(api_key) if api_key else None,
        "queried_at":          queried_at,
    }
    if not api_key:
        return {"ok": False,
                "mezan": mezan,
                "qoyod": None,
                "summary": "no_api_key",
                "next_step": ("احفظ مفتاح Qoyod API في صفحة الإعدادات أولاً، "
                              "ثم أعد تشغيل التشخيص.")}

    client = QoyodAPIClient(api_key)

    # 1) Tenant fingerprint — /branches typically returns the
    #    organisation + branch list. Cheap and rarely permission-gated.
    branches = await _call(
        client, lambda: client.list_branches(),
        endpoint="GET /branches")

    # 2) First 5 products — the smoking gun. If the user sees no
    #    products in the Qoyod UI but we get rows here, the key is on
    #    a different tenant.
    products = await _call(
        client, lambda: client.list_products(page=1, limit=5),
        endpoint="GET /products?page=1&limit=5")
    products_sample = _sample(
        (products.get("response") or {}).get("products")
        if products.get("ok") else [],
        lambda r: {"id": r.get("id"),
                   "name": r.get("name"),
                   "sku":  r.get("sku") or r.get("reference"),
                   "price": r.get("price")})
    products_meta = (products.get("response") or {}).get("meta") \
                    if products.get("ok") else None

    # 3) First 5 customers.
    customers = await _call(
        client, lambda: client.list_contacts(page=1, limit=5),
        endpoint="GET /customers?page=1&limit=5")
    customers_root = (customers.get("response") or {}) if customers.get("ok") else {}
    customers_list = (customers_root.get("contacts")
                      or customers_root.get("customers")
                      or [])
    customers_sample = _sample(
        customers_list,
        lambda r: {"id": r.get("id"),
                   "name": r.get("name") or r.get("contact_name"),
                   "phone": r.get("phone_number") or r.get("phone"),
                   "email": r.get("email")})
    customers_meta = customers_root.get("meta")

    # 4) Tenant identity hints — best-effort. Qoyod doesn't expose a
    #    single canonical "/me" endpoint so we collate hints from
    #    branches (org name) + any account-level fields the responses
    #    expose. Surface them all so the operator can compare.
    tenant_hints = {}
    if branches.get("ok"):
        br_resp = branches.get("response") or {}
        # Common shapes — keep keys flexible
        if isinstance(br_resp, dict):
            for k in ("organisation", "organization", "company",
                      "company_name", "account", "tenant"):
                if br_resp.get(k):
                    tenant_hints[k] = br_resp[k]
        bl = (br_resp.get("branches") if isinstance(br_resp, dict) else None) or []
        if isinstance(bl, list) and bl:
            tenant_hints["branches"] = [
                {"id": b.get("id"),
                 "name": b.get("name"),
                 "code": b.get("code"),
                 "organisation": b.get("organisation") or b.get("organization")}
                for b in bl[:5] if isinstance(b, dict)
            ]

    qoyod = {
        "tenant_hints":     tenant_hints,
        "branches": {
            "ok":       branches.get("ok"),
            "endpoint": branches["endpoint"],
            "error":    branches.get("error"),
        },
        "products": {
            "ok":       products.get("ok"),
            "endpoint": products["endpoint"],
            "error":    products.get("error"),
            "meta":     products_meta,
            "sample":   products_sample,
        },
        "customers": {
            "ok":       customers.get("ok"),
            "endpoint": customers["endpoint"],
            "error":    customers.get("error"),
            "meta":     customers_meta,
            "sample":   customers_sample,
        },
    }

    # Summary line for the UI.
    summary_bits = []
    if products.get("ok") and products_meta:
        summary_bits.append(
            f"المنتجات: {products_meta.get('total', '?')}")
    if customers.get("ok") and customers_meta:
        summary_bits.append(
            f"العملاء: {customers_meta.get('total', '?')}")
    summary = " · ".join(summary_bits) or "تعذّر الاستعلام"

    # Overall ok: we managed to do at least one successful call.
    ok = any(x.get("ok") for x in (branches, products, customers))

    return {
        "ok":         ok,
        "mezan":      mezan,
        "qoyod":      qoyod,
        "summary":    summary,
        "next_step": (
            "قارن المنتجات/العملاء أعلاه مع ما تراه في واجهة قيود. "
            "إن لم تتطابق → المفتاح مربوط بحساب قيود مختلف، "
            "وأوقف Go-Live حتى يتم تصحيح المفتاح."
        ),
    }
