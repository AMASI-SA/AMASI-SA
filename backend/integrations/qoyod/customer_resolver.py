"""Qoyod Customer Resolution (Step 4a) — `RULES_APPLIED → CUSTOMER_RESOLVED`.

SSOT (Single Source Of Truth) for customers at runtime
──────────────────────────────────────────────────────
The runtime pipeline uses **Mezan + Salla** as the SSOT for customer
data. It does NOT read from the migration snapshot collections
(`qoyod_external_customers`, `qoyod_migration_customers`) — those are
review-only artefacts populated by the «مرحلة الانتقال» page.

Day 4 scope:
    Take the `CustomerDTO` from the canonical SalesOrderDTO and produce
    a Qoyod `contact_id`. Two paths:

      1. **Local mapping hit** (qoyod_customers_mapping)
         → cheap, no API call, immediate return.
      2. **Local mapping miss**
         → POST /contacts to Qoyod (with idempotency key), then persist
           the mapping for next time.

Failure modes (FAILED_CUSTOMER → routed by caller):
    • `missing_customer_data` — DTO has no phone AND no email AND no
      name beyond "ضيف". Without these we cannot create a contact.
    • `qoyod_api_error`       — Qoyod rejected the create call.
    • `credentials_missing`   — API key not configured.

This module returns a structured **ResolutionResult**. The caller
(orchestrator) decides which state-machine transition to apply.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.dto import CustomerDTO
from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.credentials import get_api_key


# ─────────────────────────────────────────────────────────────────────
# Public result shape
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ResolutionResult:
    success:            bool
    qoyod_customer_id:  Optional[str] = None
    lookup_key:         Optional[str] = None
    lookup_kind:        Optional[str] = None     # phone | email | guest_order
    created_new:        bool = False
    error:              Optional[dict] = None
    notes:              Optional[list[str]] = None
    # Forensic payload snapshot — the exact body sent to `POST /customers`.
    # Always populated (even on failure) so the operator can diagnose
    # `Can't be blank`-class errors without needing live debugger access.
    qoyod_request_payload: Optional[dict] = None

    def to_log_dict(self) -> dict:
        return {
            "success":            self.success,
            "qoyod_customer_id":  self.qoyod_customer_id,
            "lookup_key":         self.lookup_key,
            "lookup_kind":        self.lookup_kind,
            "created_new":        self.created_new,
            "error":              self.error,
            "notes":              self.notes,
            "qoyod_request_payload": self.qoyod_request_payload,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Lookup key resolution
# ─────────────────────────────────────────────────────────────────────
def derive_lookup(customer: CustomerDTO) -> tuple[Optional[str], str]:
    """Return (lookup_key, lookup_kind).

    Order of preference: phone (normalised E.164) → email (lowercase)
    → "guest_order" when neither is present. The mapping table is
    keyed on lookup_key so two orders with the same buyer hit the
    same Qoyod contact.
    """
    if customer.phone:
        return customer.phone, "phone"
    if customer.email:
        return customer.email, "email"
    return None, "guest_order"


# ─────────────────────────────────────────────────────────────────────
# Qoyod payload builder
# ─────────────────────────────────────────────────────────────────────
def _safe_guest_name(customer: CustomerDTO) -> str:
    """Last-line-of-defence guest label. Used if both the normalizer's
    fallback AND the DTO's `name` field somehow ended up blank.
    Phone / email used as stable labels before the generic literal."""
    if customer.phone:
        return f"عميل {customer.phone}"
    if customer.email:
        return f"عميل {customer.email}"
    return "ضيف"


def _build_contact_payload(customer: CustomerDTO) -> dict:
    """Map our DTO → Qoyod `POST /customers` body.

    Qoyod's contact schema requires BOTH `name` (business/account name)
    AND `contact_name` (contact person). For B2C orders we use the same
    safe-name string for both (verified against Qoyod's API spec — the
    `contact_name: ["Can't be blank"]` validation fires when only `name`
    is supplied). For business accounts we don't currently distinguish;
    the same value satisfies both columns.

    Belt-and-suspenders: even if the DTO somehow has a blank name (legacy
    rows, edge case), we NEVER send a blank field to Qoyod — phone /
    email used as labels, then a literal "ضيف" as last resort.
    """
    safe_name = (customer.name or "").strip() or _safe_guest_name(customer)
    fields: dict[str, Any] = {
        "name":         safe_name,
        "contact_name": safe_name,
    }
    if customer.phone:
        fields["phone_number"] = customer.phone
    if customer.email:
        fields["email"] = customer.email
    if customer.city:
        fields["city"] = customer.city
    if customer.country:
        fields["country"] = customer.country
    # Qoyod's `/customers` endpoint accepts both the wrapped form
    # (`{contact: {...}}` — symmetric with how POST /products uses
    # `{product: {...}}`) and a flat shape. Empirically the wrapper
    # `contact` (NOT `customer`) is what Rails-side strong_params
    # expects — see the legacy method name `create_contact` and the
    # response-side `_extract_contact_id` which already accepts both
    # shapes. Iter-267 production forensic on Order #268653181 showed
    # `{customer: {...}}` was triggering `contact_name: Can't be blank`
    # because strong_params silently discarded the unknown wrapper.
    return {"contact": fields}


def _extract_contact_id(api_resp: Any) -> Optional[str]:
    """Read the new resource id from Qoyod's response. Tolerant of
    both v2 shapes (`{"customer": {...}}` legacy preferred form, and
    `{"contact": {...}}` older alias) — the lookup walks all options."""
    if not isinstance(api_resp, dict):
        return None
    # Preferred v2 shape on legacy.qoyod.com
    if isinstance(api_resp.get("customer"), dict):
        cid = api_resp["customer"].get("id")
        if cid is not None:
            return str(cid)
    # Legacy/alternate shape kept for resilience.
    if isinstance(api_resp.get("contact"), dict):
        cid = api_resp["contact"].get("id")
        if cid is not None:
            return str(cid)
    cid = api_resp.get("id") or api_resp.get("customer_id") \
          or api_resp.get("contact_id")
    return str(cid) if cid is not None else None


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────
async def resolve_customer(
    db, user_id: str,
    customer: CustomerDTO,
    *,
    trace_id: str,
    default_customer_id: Optional[str] = None,
    api_client: Optional[QoyodAPIClient] = None,
) -> ResolutionResult:
    """Resolve a canonical CustomerDTO into a Qoyod `contact_id`.

    Steps:
        1. Pick a `lookup_key` (phone > email > guest).
        2. Hit `qoyod_customers_mapping`. Return immediately on hit.
        3. (Guest with no key) → use `default_customer_id` if the
           merchant configured one; otherwise FAIL.
        4. Otherwise: build payload, POST /contacts with an
           idempotency key derived from `trace_id` so transient
           retries never double-create. Persist the mapping.

    `api_client` is injectable so tests can pass a fake.
    """
    lookup_key, lookup_kind = derive_lookup(customer)

    # ── Guest path ──────────────────────────────────────────────────
    if lookup_kind == "guest_order":
        if default_customer_id:
            return ResolutionResult(
                success=True,
                qoyod_customer_id=str(default_customer_id),
                lookup_key=None, lookup_kind="guest_order",
                created_new=False,
                notes=["used merchant-configured default_customer_id"],
            )
        return ResolutionResult(
            success=False,
            lookup_kind="guest_order",
            error={
                "code": "missing_customer_data",
                "message": "no phone, no email, and no default_customer_id",
            },
        )

    # ── Local mapping hit ───────────────────────────────────────────
    existing = await db.qoyod_customers_mapping.find_one(
        {"user_id": user_id, "lookup_key": lookup_key},
        {"_id": 0, "qoyod_customer_id": 1, "dry_run_only": 1},
    )
    existing_cid = existing.get("qoyod_customer_id") if existing else None
    # ─── DRY-Run Leak Guard (Iter-268, P0) ─────────────────────────
    # Any customer mapping carrying a `DRY:contact:*` id is a Dry-Run
    # artefact and MUST NOT bind to a production invoice. Mirrors the
    # product_resolver guard. Mapping is quarantined and we fall
    # through to the create-fresh path so a real Qoyod contact is
    # created (or matched via the upstream find/create idempotency).
    if existing_cid and (str(existing_cid).startswith("DRY:")
                         or (existing or {}).get("dry_run_only")):
        await db.qoyod_customers_mapping.update_one(
            {"user_id": user_id, "lookup_key": lookup_key},
            {"$set": {"dry_run_only": True,
                      "quarantined_at": _now(),
                      "quarantine_reason": "dry_run_id_in_production"}},
        )
        existing = None     # fall-through to create-fresh
    elif existing_cid:
        return ResolutionResult(
            success=True,
            qoyod_customer_id=str(existing_cid),
            lookup_key=lookup_key, lookup_kind=lookup_kind,
            created_new=False,
        )

    # ── Need to create in Qoyod ─────────────────────────────────────
    if api_client is None:
        key = await get_api_key(db, user_id)
        if not key:
            return ResolutionResult(
                success=False, lookup_key=lookup_key, lookup_kind=lookup_kind,
                error={"code": "credentials_missing",
                       "message": "Qoyod API key not configured"},
            )
        api_client = QoyodAPIClient(key)

    payload = _build_contact_payload(customer)
    # Idempotency: same trace_id + same lookup_key → same Qoyod result.
    idem = f"mzn-{trace_id}-contact-{lookup_kind}-{lookup_key}"
    try:
        resp = await api_client.create_contact(payload, idem=idem)
    except QoyodAPIError as exc:
        # Attach the payload we DID send so the operator can verify
        # the name/contact_name pair without needing to recreate the run.
        err = exc.to_log_dict()
        return ResolutionResult(
            success=False,
            lookup_key=lookup_key, lookup_kind=lookup_kind,
            error=err,
            qoyod_request_payload=payload,
        )

    cid = _extract_contact_id(resp)
    if not cid:
        return ResolutionResult(
            success=False,
            lookup_key=lookup_key, lookup_kind=lookup_kind,
            error={"code": "qoyod_response_missing_id",
                   "message": "create_contact response had no id",
                   "qoyod_response_excerpt": str(resp)[:300]},
            qoyod_request_payload=payload,
        )

    # ── Persist mapping (idempotent upsert) ─────────────────────────
    await db.qoyod_customers_mapping.update_one(
        {"user_id": user_id, "lookup_key": lookup_key},
        {"$set": {
            "schema_version":     1,
            "user_id":            user_id,
            "lookup_key":         lookup_key,
            "lookup_kind":        lookup_kind,
            "qoyod_customer_id":  cid,
            "customer_name":      customer.name,
            "phone":              customer.phone,
            "email":              customer.email,
            "auto_created":       True,
         },
         "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )

    return ResolutionResult(
        success=True,
        qoyod_customer_id=cid,
        lookup_key=lookup_key, lookup_kind=lookup_kind,
        created_new=True,
        qoyod_request_payload=payload,
    )
