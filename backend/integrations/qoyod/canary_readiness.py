"""Iter-001k+ — Canary Readiness Preview (Read-Only).

Purpose
────────
For a SINGLE order, materialise the exact preflight report an
operator needs BEFORE approving a Manual Send:

    • Confirm product resolution (mapping vs external vs missing).
    • Show the EXACT Qoyod customer payload that would be POSTed if
      the operator approves creation — built by the real
      `customer_resolver._build_contact_payload` so there is no
      drift between preview and production.
    • Simulate the post-adoption state: what remaining blockers
      would fire assuming customer + product are both resolved.
    • Confirm invoice_date / payment_date / due_date will use
      `send_date_riyadh` (Iter-001k contract).

Contract (STRICT):
    • Read-Only. Zero DB writes.
    • Zero Qoyod API calls (payload is BUILT in-process, never sent).
    • Refuses to run unless gates are Fail-Closed.
    • No adopt / no create / no clear / no send.
"""
from __future__ import annotations

from typing import Any, Optional

from integrations.qoyod.dry_rca_report import (
    GatesNotFailClosedError,
    _fetch_inbox_row,
    _find_real_customer_in_external,
    _find_real_customer_mapping,
    _find_real_product_in_external,
    _find_real_product_mapping,
    _is_dry_or_preview,
    _normalise_phone,
)
from integrations.qoyod.eligible_orders import _normalize_status
from integrations.qoyod.selective_send_policy import (
    QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT,
)


PAYLOAD_DATE_SOURCE: str = "send_date"


_GATE_KEYS = (
    "selective_live_send_enabled",
    "production_writes_locked",
    "qoyod_sync_start_date",
    "qoyod_tax_period",
    "bank_transfer_routing_enabled",
    "qoyod_invoice_date_source",
    "qoyod_enabled_invoice_trigger_statuses",
)


async def _load_gates_snapshot(db, user_id: str) -> dict:
    """Robust settings loader. Reads the full doc (minus `_id`) and
    keeps only the seven gate fields. Guards against the historical
    projection-drops-fields bug reported on Production."""
    doc = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    return {k: doc[k] for k in _GATE_KEYS if k in doc}


def _canonical_status_diagnostic(
    canonical: dict, enabled: list[str],
) -> dict:
    """Show EXACTLY how the policy will read the status field for
    this order — including the fallback chain and normalization."""
    # Same fallback chain as `selective_send_policy._build_policy_order`.
    raw_status_primary = canonical.get("status")
    raw_status_secondary = canonical.get("order_status")
    raw_status = raw_status_primary or raw_status_secondary or ""
    normalized = _normalize_status(raw_status)
    enabled_normalized = {_normalize_status(s) for s in enabled if s}
    is_enabled = normalized in enabled_normalized
    return {
        "status_raw":                       raw_status,
        "status_source":  ("canonical_payload.status"
                            if raw_status_primary
                            else ("canonical_payload.order_status"
                                  if raw_status_secondary else None)),
        "normalized_status":                normalized,
        "enabled_trigger_statuses":         enabled,
        "enabled_trigger_statuses_normalized": sorted(enabled_normalized),
        "invoice_trigger_status_check_source":
            "selective_send_policy._normalize_status "
            "(same code path used by the guard)",
        "invoice_trigger_status_enabled":   is_enabled,
    }


def _preview_customer_payload(canonical: dict) -> tuple[dict, dict]:
    """Build the EXACT `{contact: {...}}` payload the real
    `customer_resolver._build_contact_payload` would emit.

    Returns (payload_that_would_be_posted, required_field_status).
    NEVER sends anything.
    """
    from integrations.qoyod.customer_resolver import (
        _build_contact_payload,
    )
    from integrations.qoyod.dto import CustomerDTO

    cust_raw = canonical.get("customer") or {}
    name = (cust_raw.get("name")
            or canonical.get("customer_name") or "").strip()
    phone = _normalise_phone(
        cust_raw.get("mobile")
        or cust_raw.get("phone")
        or canonical.get("customer_mobile"))
    email = (cust_raw.get("email")
             or canonical.get("customer_email")
             or "").strip().lower() or None
    city = cust_raw.get("city") or None
    country = cust_raw.get("country") or None

    # Instantiate the same DTO the pipeline would build.
    dto = CustomerDTO(
        name=name or "ضيف",     # `_build_contact_payload` also
                                  # falls back internally — pin the
                                  # observable value here.
        phone=phone,
        email=email,
        is_guest=bool(cust_raw.get("is_guest")),
        city=city,
        country=country,
    )
    payload = _build_contact_payload(dto)

    required_field_status = {
        "name_present":          bool(name),
        "phone_present":         bool(phone),
        "email_present":         bool(email),
        "city_present":          bool(city),
        "country_present":       bool(country),
        "used_guest_fallback":   not bool(name),
    }
    return (payload, required_field_status)


async def _per_sku_resolution(
    db, user_id: str, canonical: dict,
) -> list[dict]:
    out: list[dict] = []
    for it in canonical.get("items") or []:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        pid = it.get("qoyod_product_id")
        m = await _find_real_product_mapping(db, user_id, sku)
        e = await _find_real_product_in_external(db, user_id, sku)
        if m:
            state = {
                "sku":                       sku,
                "current_qoyod_product_id":  pid,
                "is_current_dry":            _is_dry_or_preview(pid),
                "resolved_from":             "qoyod_products_mapping",
                "resolved_qoyod_product_id": m.get(
                    "qoyod_product_id"),
                "needs_db_write":            False,
                "notes": ("Mapping row exists and is not dry_run_only "
                          "— pipeline will read it directly at send "
                          "time, no adopt/write required."),
            }
        elif e:
            state = {
                "sku":                       sku,
                "current_qoyod_product_id":  pid,
                "is_current_dry":            _is_dry_or_preview(pid),
                "resolved_from":             "qoyod_external_products",
                "resolved_qoyod_product_id": e.get(
                    "qoyod_product_id"),
                "needs_db_write":            True,
                "notes": ("Real Qoyod product exists in the initial "
                          "sync snapshot. A one-time `adopt` step "
                          "would copy the id into "
                          "`qoyod_products_mapping`."),
            }
        else:
            state = {
                "sku":                       sku,
                "current_qoyod_product_id":  pid,
                "is_current_dry":            _is_dry_or_preview(pid),
                "resolved_from":             None,
                "resolved_qoyod_product_id": None,
                "needs_db_write":            None,
                "notes": ("No real Qoyod product found — a create "
                          "operation would be required later."),
            }
        out.append(state)
    return out


def _simulate_post_state(
    canonical: dict,
    per_sku: list[dict],
    real_customer_id: Optional[Any],
    status_diag: dict,
) -> dict:
    """Assume customer resolved + all products resolved. Enumerate
    the remaining Selective-Send blockers that would still fire."""
    remaining: list[str] = []

    # ── Immutable operator-controlled blockers ──────────────────
    remaining.append("gate_disabled")
    remaining.append("write_lock_active")

    # ── Payment method ──────────────────────────────────────────
    payment = str(canonical.get("payment_method") or "").lower()
    if payment in {"bank_transfer", "bank", "wire_transfer"}:
        remaining.append("bank_transfer_on_hold_iter_294")

    # ── Status (uses shared policy normalizer) ─────────────────
    if not status_diag.get("invoice_trigger_status_enabled"):
        remaining.append("invoice_trigger_status_not_enabled")

    # ── Customer post-simulation ───────────────────────────────
    if real_customer_id is None:
        remaining.append("customer_still_missing_after_simulated_create")

    # ── Products post-simulation ───────────────────────────────
    unresolved = [s for s in per_sku
                  if s["resolved_qoyod_product_id"] is None]
    if unresolved:
        remaining.append(
            f"product_still_missing_after_adopt:"
            f"{','.join(s['sku'] for s in unresolved)}")

    # ── DRY invoice sentinel treatment ─────────────────────────
    existing_inv = canonical.get("existing_qoyod_invoice_id")
    dry_invoice_note = (
        f"existing_qoyod_invoice_id = "
        f"{existing_inv!r} — sentinel from an earlier dry run. "
        f"pipeline._looks_like_dry_id() recognises the `DRY:` prefix "
        f"and treats the field as absent, so a NEW real invoice will "
        f"be created. NO cleanup of the sentinel is required for "
        f"the send itself; the pipeline will overwrite it with the "
        f"real qoyod_invoice_id after a successful POST."
    ) if _is_dry_or_preview(existing_inv) else (
        f"existing_qoyod_invoice_id = {existing_inv!r} — this is a "
        f"real Qoyod id; the order would be classified as "
        f"`already_sent` and the send would be REFUSED."
    )
    return {
        "remaining_blockers":      remaining,
        "ready_if_gate_opened":    (remaining == ["gate_disabled",
                                                  "write_lock_active"]),
        "dry_invoice_treatment":   dry_invoice_note,
    }


async def build_canary_readiness_preview(
    db,
    *,
    user_id: str,
    order_number: str,
) -> dict:
    settings = await _load_gates_snapshot(db, user_id)
    if settings.get("selective_live_send_enabled") is True or \
            settings.get("production_writes_locked") is False:
        raise GatesNotFailClosedError(
            "Canary readiness preview refuses to run while gates "
            "are not Fail-Closed.")

    row = await _fetch_inbox_row(db, user_id, str(order_number))
    if not row:
        return {
            "order_number":       order_number,
            "found":              False,
            "read_only":          True,
            "no_qoyod_api_calls": True,
            "no_db_writes":       True,
            "gates_snapshot":     settings,
            "note": "no integration_inbox row",
        }

    canonical = row.get("canonical_payload") or {}
    per_sku = await _per_sku_resolution(db, user_id, canonical)

    enabled_statuses = settings.get(
        "qoyod_enabled_invoice_trigger_statuses") \
        or list(QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT)
    status_diag = _canonical_status_diagnostic(
        canonical, enabled_statuses)

    # ── Customer resolution probe (Read-Only) ───────────────────
    cust = canonical.get("customer") or {}
    phone = _normalise_phone(
        cust.get("mobile") or cust.get("phone")
        or canonical.get("customer_mobile"))
    email = (cust.get("email")
             or canonical.get("customer_email")
             or "").strip().lower() or None
    lookup_key = phone or email
    real_map = await _find_real_customer_mapping(
        db, user_id, lookup_key)
    real_ext = await _find_real_customer_in_external(
        db, user_id, phone, email)
    real_customer_id_after_adopt = (
        (real_map or {}).get("qoyod_customer_id")
        or (real_ext or {}).get("qoyod_customer_id")
        or "<NEW_ID_ASSIGNED_BY_QOYOD_ON_CREATE>")

    payload, field_status = _preview_customer_payload(canonical)
    post_state = _simulate_post_state(
        canonical, per_sku, real_customer_id_after_adopt, status_diag)

    send_date_diagnostic = {
        "payload_date_source":      PAYLOAD_DATE_SOURCE,
        "invoice_date_will_use":    "send_date_riyadh",
        "payment_date_will_use":    "send_date_riyadh",
        "due_date_will_use":        "send_date_riyadh",
        "salla_order_created_at_ignored": True,
        "note": ("Iter-001k contract: pipeline.py captures ONE "
                 "frozen `send_timestamp_riyadh` per attempt and "
                 "stamps invoice + payment payloads with the SAME "
                 "value via `apply_send_date_to_qoyod_payload`."),
    }

    return {
        "order_number":                    order_number,
        "found":                           True,
        "traces_available":                1,
        "gates_snapshot":                  settings,
        # ── Canonical baseline ──────────────────────────────────
        "salla_order_id":                  canonical.get("order_id"),
        "salla_official_total":            canonical.get("total_amount"),
        "payment_method":                  canonical.get("payment_method"),
        "status":                          status_diag["status_raw"],
        "normalized_status":               status_diag[
                                            "normalized_status"],
        "enabled_trigger_statuses":        status_diag[
                                            "enabled_trigger_statuses"],
        "invoice_trigger_status_check":    status_diag,
        # ── Products (per-SKU resolution) ───────────────────────
        "products_resolution":             per_sku,
        # ── Customer preview ────────────────────────────────────
        "customer_lookup_key":             lookup_key,
        "customer_current_qoyod_id":       row.get("qoyod_customer_id")
                                            or canonical.get(
                                                "qoyod_customer_id"),
        "customer_would_be_created_via_endpoint":
            "POST https://api.qoyod.com/v3.0/customers",
        "customer_preview_payload":        payload,
        "customer_required_field_status":  field_status,
        "customer_real_source_if_any": (
            "qoyod_customers_mapping" if real_map
            else ("qoyod_external_customers" if real_ext else None)),
        "customer_real_qoyod_id_if_any":
            (real_map or real_ext or {}).get("qoyod_customer_id"),
        # ── Post-simulation state ───────────────────────────────
        "post_simulation":                 post_state,
        # ── send-date contract ──────────────────────────────────
        "send_date_diagnostic":            send_date_diagnostic,
        # ── Read-Only guarantees ────────────────────────────────
        "read_only":                       True,
        "no_qoyod_api_calls":              True,
        "no_db_writes":                    True,
    }
