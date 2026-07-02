"""Iter-001k+ — Canary Live Send (single-order, one-shot).

Scope:  ONLY order 269629400.  Everything else is refused.

Contract (STRICT):
    • Every one of the 14 guards MUST pass BEFORE any Qoyod call.
    • `qoyod_settings.selective_live_send_enabled` and
      `qoyod_settings.production_writes_locked` are NEVER mutated
      in the DB. The scoped bypass lives inside the execution
      context of a single `reprocess_one_order` call.
    • Every attempt (accepted OR refused) writes a row into
      `canary_send_audit_log` with the guard that failed (if any),
      the timestamp, and the outcome.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional
import uuid


# ── Immutable contract constants ────────────────────────────────────
CANARY_ORDER_NUMBER:      str = "269629400"
CANARY_APPROVAL_PHRASE:   str = (
    "Approved live Qoyod canary send for order 269629400 only")
REQUIRED_PAYMENT_METHOD:  str = "tabby_installment"
REQUIRED_STATUS:          str = "completed"
# Iter-2026-02.canary-manual — the same canary target order
# (269629400) may drift to `جاري التوصيل` before the operator can
# fire the send. That status is business-eligible per
# `eligible_orders.ELIGIBLE_STATUSES` but does NOT trigger auto-send
# (invoice_trigger_status_enabled=false in Production for this
# status). Canary treats it as an explicit MANUAL send — signalled
# via `manual_send_requested=true` in the response — but ONLY for
# the canary target order. `_ACCEPTED_CANARY_STATUSES_NORMALIZED` is
# the exhaustive whitelist; anything else refuses at Guard 4.
_MANUAL_SEND_STATUSES_NORMALIZED: frozenset[str] = frozenset({
    "جاري التوصيل",       # normalized form of جاري_التوصيل + جاري التوصيل
})
_ACCEPTED_CANARY_STATUSES_NORMALIZED: frozenset[str] = frozenset(
    {REQUIRED_STATUS} | _MANUAL_SEND_STATUSES_NORMALIZED)
Q3_CUTOFF_ISO:            str = "2026-07-01"
REQUIRED_SKU:             str = "AMS11237"
REQUIRED_QOYOD_PRODUCT_ID: int = 45
REQUIRED_MOBILE:          str = "+966557951913"
REQUIRED_EMAIL:           str = "suziyousif9@gmail.com"


class CanaryGuardFailed(Exception):
    def __init__(self, guard_no: int, code: str, detail: str,
                 extra: Optional[dict] = None):
        super().__init__(f"guard#{guard_no} {code}: {detail}")
        self.guard_no = guard_no
        self.code     = code
        self.detail   = detail
        self.extra    = extra or {}


# ── Scoped dry_run override — canary-only DB proxy ─────────────────
# Rationale: `one_shot_reprocess.py` L666 rejects when
#     `is_dry_run_mode(settings) is True`  (settings.get("dry_run_mode"))
# Pipeline stages also branch on `settings.get("dry_run_mode")`. We
# want a scoped override that:
#   • NEVER writes to qoyod_settings.
#   • NEVER leaks outside this single canary call (constructed fresh
#     per attempt, discarded on return).
#   • Applies ONLY to reads of `qoyod_settings.find_one`.
#   • Leaves every other collection and every write untouched.
# Approach: a thin proxy that intercepts only
#     db.qoyod_settings.find_one(...)
# and overlays `dry_run_mode=False` on the returned document. All
# other attributes / methods forward to the real db unchanged. Two
# static invariants (tests) pin these guarantees.
class _CanaryDryRunSettingsProxy:
    """Proxy for `db.qoyod_settings` that overrides FOUR fields
    (`dry_run_mode`, `selective_live_send_enabled`,
    `production_writes_locked`, `qoyod_enabled_invoice_trigger_statuses`)
    on read. Writes / updates pass through untouched.

    Iter-2026-02.rev9: extends the overlay to include the enabled
    trigger-status whitelist so `selective_send_policy` accepts the
    manual-canary status `جاري التوصيل` in addition to the
    tenant's on-disk list (typically `["completed", "تم التنفيذ"]`).
    The DB row is NEVER mutated. Guards 11/12 in `_run_guards`
    already assert against the REAL DB values BEFORE the proxy is
    built (Fail-Closed on disk), and Guard 4 in `_row_matches_canary_criteria`
    only lets the LATEST row's status pass if it is in the tight
    canary whitelist `{"completed", "جاري التوصيل"}`. So the enabled-
    triggers overlay only widens the set to precisely match the
    LATEST canary-eligible status — no broader impact."""
    __slots__ = ("_coll",)

    _CANARY_TRIGGER_STATUS_OVERLAY = ("جاري التوصيل",)

    def __init__(self, real_coll):
        self._coll = real_coll

    async def find_one(self, *a, **kw):
        doc = await self._coll.find_one(*a, **kw)
        if isinstance(doc, dict):
            # Widen the enabled trigger-status list to include the
            # canary manual-send status, PRESERVING every value the
            # tenant already has (defence-in-depth — never shrinks).
            raw_enabled = doc.get(
                "qoyod_enabled_invoice_trigger_statuses")
            if isinstance(raw_enabled, (list, tuple, set,
                                        frozenset)):
                base = list(raw_enabled)
            elif raw_enabled is None:
                base = []
            else:
                base = [str(raw_enabled)]
            # Preserve order + dedupe.
            widened_enabled = list(dict.fromkeys(
                base + list(self._CANARY_TRIGGER_STATUS_OVERLAY)))
            # Also widen `invoice_trigger_statuses` (business-rules
            # eligibility field) with the same guard.
            raw_trigger = doc.get("invoice_trigger_statuses")
            if isinstance(raw_trigger, (list, tuple, set,
                                        frozenset)):
                base_t = list(raw_trigger)
            elif raw_trigger is None:
                base_t = []
            else:
                base_t = [str(raw_trigger)]
            widened_trigger = list(dict.fromkeys(
                base_t + list(self._CANARY_TRIGGER_STATUS_OVERLAY)))
            return {
                **doc,
                # Canary-scope overlay — four fields.
                "dry_run_mode":                False,
                "selective_live_send_enabled": True,
                "production_writes_locked":    False,
                "qoyod_enabled_invoice_trigger_statuses":
                    widened_enabled,
                "invoice_trigger_statuses":    widened_trigger,
            }
        return doc

    def __getattr__(self, name):
        return getattr(self._coll, name)


class _CanaryDBProxy:
    """Proxy for the `db` object that ONLY intercepts access to
    `qoyod_settings` (wrapped in `_CanaryDryRunSettingsProxy`).
    Every other collection is returned as-is."""
    __slots__ = ("_db",)

    def __init__(self, real_db):
        object.__setattr__(self, "_db", real_db)

    def __getattr__(self, name):
        if name == "qoyod_settings":
            return _CanaryDryRunSettingsProxy(self._db.qoyod_settings)
        return getattr(self._db, name)

    def __getitem__(self, name):
        # Some code accesses collections via db["collection"]; forward
        # with the same override policy.
        if name == "qoyod_settings":
            return _CanaryDryRunSettingsProxy(self._db["qoyod_settings"])
        return self._db[name]


async def _write_audit(
    db, *, attempt_id: str, phase: str, status: str,
    guard_no: Optional[int] = None, code: Optional[str] = None,
    detail: Optional[str] = None,
    result_payload: Optional[dict] = None,
) -> None:
    """Insert an audit row. This is the ONLY write this module makes,
    and it writes to a dedicated collection (`canary_send_audit_log`)
    — never to `qoyod_settings`, never to `qoyod_per_order_approvals`
    (that one is owned by `reprocess_one_order`)."""
    await db.canary_send_audit_log.insert_one({
        "attempt_id":    attempt_id,
        "order_number":  CANARY_ORDER_NUMBER,
        "phase":         phase,
        "status":        status,
        "guard_no":      guard_no,
        "code":          code,
        "detail":        detail,
        "result_payload": result_payload,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    })


def _row_summary(row: dict) -> dict:
    """Non-PII summary of a candidate inbox row for debug output.
    Never includes customer name / email / phone / raw_payload."""
    from integrations.qoyod.eligible_orders import _normalize_status
    can = row.get("canonical_payload") or {}
    raw_status = can.get("status") or can.get("order_status")
    norm_status = _normalize_status(raw_status)
    return {
        "trace_id":         row.get("trace_id"),
        "received_at":      (row.get("received_at").isoformat()
                             if hasattr(row.get("received_at"),
                                        "isoformat")
                             else row.get("received_at")),
        "pipeline_stage":   row.get("pipeline_stage"),
        "status":           raw_status,
        "normalized_status": norm_status,
        "manual_send_requested": (
            norm_status in _MANUAL_SEND_STATUSES_NORMALIZED),
        "payment_method":   can.get("payment_method"),
        "created_at":       (can.get("salla_order_created_at")
                             or can.get("created_at")
                             or can.get("order_date")),
        "existing_qoyod_invoice_id": (
            can.get("existing_qoyod_invoice_id")
            or row.get("existing_qoyod_invoice_id")),
        "qoyod_invoice_id": row.get("qoyod_invoice_id"),
        "outcome":          row.get("outcome"),
    }


def _row_matches_canary_criteria(row: dict) -> tuple[bool, Optional[str]]:
    """Deterministic per-row check for the strict canary contract.
    Returns (True, None) iff the row satisfies ALL row-level criteria
    (payment method, status, created_at ≥ Q3 cutoff, no real existing
    invoice, customer phone, customer email, Mezan-VAT totals).
    Returns (False, reason_code) otherwise. This mirrors Guards
    3, 4, 5, 6, 8, 9, 10 but does NOT raise — so it can be used to
    filter multiple candidate rows before choosing which one to send.
    """
    from integrations.qoyod.eligible_orders import (
        _check_totals, _extract_order_created_at, _normalize_status,
    )
    can = row.get("canonical_payload") or {}
    # Payment method.
    if str(can.get("payment_method") or "").lower() \
            != REQUIRED_PAYMENT_METHOD:
        return (False, "payment_method_mismatch")
    # Status: accept 'completed' OR 'جاري التوصيل' (canary-only
    # extension — the latter triggers manual_send semantics).
    raw_status = can.get("status") or can.get("order_status") or ""
    if _normalize_status(raw_status) \
            not in _ACCEPTED_CANARY_STATUSES_NORMALIZED:
        return (False, "status_not_completed")
    # Created_at ≥ Q3 cutoff.
    pseudo = {
        "created_at": (can.get("salla_order_created_at")
                       or can.get("created_at")),
        "order_date": can.get("order_date"),
        "order_date_inferred": (can.get("order_date_inferred")
                                or row.get("order_date_inferred")
                                or False),
        "_inbox_row": {"raw_payload": row.get("raw_payload")},
    }
    d = _extract_order_created_at(pseudo)
    if d is None:
        return (False, "created_at_missing")
    if d < date.fromisoformat(Q3_CUTOFF_ISO):
        return (False, "created_before_q3_cutoff")
    # No real existing invoice.
    existing = can.get("existing_qoyod_invoice_id") \
        or row.get("existing_qoyod_invoice_id") \
        or row.get("qoyod_invoice_id")
    if existing is not None:
        s = str(existing)
        if not (s.startswith("DRY:") or s.startswith("PREVIEW:")):
            return (False, "real_existing_invoice_id_present")
    # Partial-invoice-created safety: if pipeline_stage indicates an
    # invoice was already created (INVOICE_CREATED) or the row was
    # skipped (SKIPPED) but the invoice_id itself is real, refuse —
    # even without an `existing_qoyod_invoice_id` field.
    if row.get("pipeline_stage") in ("INVOICE_CREATED", "SKIPPED"):
        qid_direct = row.get("qoyod_invoice_id")
        if qid_direct is not None:
            qs = str(qid_direct)
            if qs and not (qs.startswith("DRY:")
                           or qs.startswith("PREVIEW:")):
                return (False, "partial_real_invoice_state")
    # Customer phone.
    import re as _re
    cust = can.get("customer") or {}
    phone = _re.sub(r"[^\d+]", "",
                    str(cust.get("mobile") or cust.get("phone")
                        or ""))
    if phone != REQUIRED_MOBILE:
        return (False, "customer_mobile_mismatch")
    # Customer email.
    email = (cust.get("email") or "").strip().lower()
    if email != REQUIRED_EMAIL:
        return (False, "customer_email_mismatch")
    # Mezan-VAT totals.
    t = _check_totals(can)
    if not t["valid"]:
        return (False, "totals_mismatch_gt_0_01")
    return (True, None)


async def _run_guards(
    db, *, order_number: str, approval_phrase: str,
    user_id: str = "main",
) -> tuple[dict, dict, dict, dict]:
    """Runs all 14 guards. Returns (settings_snapshot, canonical,
    settings_debug, chosen_row) when every guard passes; raises
    `CanaryGuardFailed` (carrying settings_debug) otherwise.

    When multiple inbox rows exist for the same order_number,
    canary applies its strict row-level criteria to EVERY row and
    picks the unique passing row (deterministic). Ambiguity or
    zero matches → refuse."""
    # Guard 1 — approval_phrase must match EXACTLY.
    if approval_phrase != CANARY_APPROVAL_PHRASE:
        raise CanaryGuardFailed(1, "approval_phrase_mismatch",
                                "approval_phrase does not match "
                                "the canary contract phrase.")

    # Guard 2 — order_number must be EXACTLY the canary target.
    if str(order_number) != CANARY_ORDER_NUMBER:
        raise CanaryGuardFailed(
            2, "order_number_not_canary",
            f"Canary endpoint accepts only order "
            f"{CANARY_ORDER_NUMBER}.")

    # Guard 11+12 — gates must remain Fail-Closed in DB.
    # NOTE: use identical read + default semantics as
    # `build_selective_send_policy_report` so both endpoints agree on
    # what "Fail-Closed" means. Missing field → Fail-Closed default
    # (selective=False, writes_locked=True).
    raw_settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    raw_selective = raw_settings.get("selective_live_send_enabled")
    raw_writes    = raw_settings.get("production_writes_locked")
    raw_dry_run   = raw_settings.get("dry_run_mode")
    raw_enabled_triggers = raw_settings.get(
        "qoyod_enabled_invoice_trigger_statuses")
    raw_invoice_triggers = raw_settings.get(
        "invoice_trigger_statuses")
    # Fail-Closed defaults (identical to policy report).
    selective_flag = bool(raw_selective) if raw_selective is not None \
        else False
    writes_locked_flag = bool(raw_writes) if raw_writes is not None \
        else True
    settings_debug = {
        "settings_source":                  "qoyod_settings",
        "settings_user_id":                 user_id,
        "settings_doc_present":             bool(raw_settings),
        "raw_selective_live_send_enabled":  raw_selective,
        "raw_selective_live_send_enabled_type":
            type(raw_selective).__name__,
        "effective_selective_live_send_enabled": selective_flag,
        "raw_production_writes_locked":     raw_writes,
        "raw_production_writes_locked_type":
            type(raw_writes).__name__,
        "effective_production_writes_locked": writes_locked_flag,
        "raw_dry_run_mode":                 raw_dry_run,
        "raw_dry_run_mode_type":            type(raw_dry_run).__name__,
        # Trigger-status whitelist snapshot + overlay (rev9).
        "raw_qoyod_enabled_invoice_trigger_statuses":
            raw_enabled_triggers,
        "raw_invoice_trigger_statuses":     raw_invoice_triggers,
        "effective_qoyod_enabled_invoice_trigger_statuses_for_canary":
            list(dict.fromkeys(
                (list(raw_enabled_triggers)
                 if isinstance(raw_enabled_triggers,
                               (list, tuple, set, frozenset))
                 else [])
                + ["جاري التوصيل"])),
        "canary_status_overlay":            ["جاري التوصيل"],
        # Canary scoped policy overlay (rev8) — DB is untouched.
        "effective_dry_run_mode_for_canary":                 False,
        "effective_selective_live_send_enabled_for_canary":  True,
        "effective_production_writes_locked_for_canary":     False,
        "policy_override_scope":
            f"canary_order_{CANARY_ORDER_NUMBER}_only",
        "dry_run_mode_scope":
            f"canary_order_{CANARY_ORDER_NUMBER}_only",
        "default_semantics":
            "identical to selective_send_policy_report "
            "(missing field → Fail-Closed default)",
    }
    if selective_flag is not False:
        raise CanaryGuardFailed(
            11, "selective_live_send_enabled_not_false",
            "Master gate must remain FALSE. Refusing to run.")
    if writes_locked_flag is not True:
        raise CanaryGuardFailed(
            12, "production_writes_locked_not_true",
            "Write lock must remain TRUE (scoped bypass only).")

    # Fetch ALL candidate rows for this order.
    # Salla persists order ids as int or str; match both.
    on_str = str(order_number)
    candidates_val: list[Any] = [on_str]
    try:
        candidates_val.append(int(on_str))
    except (TypeError, ValueError):
        pass
    inbox_or: list[dict] = []
    for v in candidates_val:
        inbox_or.extend([
            {"salla_order_number":            v},
            {"salla_order_id":                v},
            {"canonical_payload.order_number": v},
            {"canonical_payload.order_id":     v},
        ])
    all_rows: list[dict] = await db.integration_inbox.find(
        {"user_id": user_id, "$or": inbox_or},
        {"_id": 0}
    ).sort([("received_at", -1)]).to_list(length=20)
    if not all_rows:
        raise CanaryGuardFailed(
            2, "order_not_found",
            f"No inbox row for order {order_number}.")

    # Guard 7 — AMS11237 must resolve to qoyod_product_id=45.
    # (Order-independent; run before per-row selection so its diagnostic
    # is not shadowed by ambiguity.)
    m = await db.qoyod_products_mapping.find_one(
        {"user_id": user_id, "sku": REQUIRED_SKU,
         "dry_run_only": {"$ne": True}},
        {"_id": 0, "qoyod_product_id": 1})
    if not m or int(m.get("qoyod_product_id") or 0) != \
            REQUIRED_QOYOD_PRODUCT_ID:
        raise CanaryGuardFailed(
            7, "product_mapping_mismatch",
            f"Expected {REQUIRED_SKU} → "
            f"{REQUIRED_QOYOD_PRODUCT_ID}, got {m}.")

    # ── Selection policy: LATEST-ONLY, no fallback ──────────────────
    # `all_rows` is already sorted by received_at DESC. The latest
    # row is the sole source of truth for the order's current state.
    # Rationale (Iter-2026-02.rev6): older rows may show status=
    # completed while the newest reflects the current status (e.g.
    # جاري_التوصيل). Falling back to an older matching row would
    # send the invoice against a stale status. Never allowed.
    latest_row = all_rows[0]
    latest_trace_id = latest_row.get("trace_id")
    latest_norm_status = _row_summary(latest_row).get(
        "normalized_status")

    # Evaluate criteria on ALL rows for debug transparency, but
    # SELECTION uses only the latest.
    per_row_reasons: list[dict] = []
    for r in all_rows:
        ok, reason = _row_matches_canary_criteria(r)
        per_row_reasons.append({
            **_row_summary(r),
            "row_matches_canary_criteria": ok,
            "row_reject_reason":           reason,
        })

    latest_ok, latest_reason = _row_matches_canary_criteria(latest_row)

    duplicate_debug = {
        "duplicate_rows_count":     len(all_rows),
        "duplicate_trace_ids":      [r.get("trace_id")
                                     for r in all_rows],
        "duplicate_rows_summary":   per_row_reasons,
        "latest_trace_id":          latest_trace_id,
        "latest_normalized_status": latest_norm_status,
        "latest_matches_canary_criteria": latest_ok,
        "latest_reject_reason":     latest_reason,
        "selection_policy":
            "canary uses LATEST row only (received_at DESC). No "
            "fallback to older rows even if they would pass. If the "
            "latest fails any row-level criterion, refuse.",
    }

    if not latest_ok:
        # Reproduce the SPECIFIC guard on the LATEST row (not any
        # older row) so the operator sees exactly why the latest
        # state is not eligible.
        row = latest_row
        canonical = row.get("canonical_payload") or {}

        # Guard 3 — payment method.
        payment = str(canonical.get("payment_method") or "").lower()
        if payment != REQUIRED_PAYMENT_METHOD:
            raise CanaryGuardFailed(
                3, "payment_method_mismatch",
                f"Expected '{REQUIRED_PAYMENT_METHOD}', "
                f"got '{payment}'.",
                extra={"duplicate_debug": duplicate_debug})

        # Guard 4 — normalised status.
        from integrations.qoyod.eligible_orders import _normalize_status
        raw_status = canonical.get("status") \
            or canonical.get("order_status") or ""
        norm = _normalize_status(raw_status)
        if norm not in _ACCEPTED_CANARY_STATUSES_NORMALIZED:
            raise CanaryGuardFailed(
                4, "status_not_completed",
                f"Latest row status {norm!r} not in canary-accepted "
                f"set {sorted(_ACCEPTED_CANARY_STATUSES_NORMALIZED)}.",
                extra={"duplicate_debug": duplicate_debug})

        # Guard 5 — created_at cutoff (with date_debug).
        from integrations.qoyod.eligible_orders import (
            _extract_order_created_at,
        )
        pseudo_order = {
            "created_at": (canonical.get("salla_order_created_at")
                           or canonical.get("created_at")),
            "order_date": canonical.get("order_date"),
            "order_date_inferred": (
                canonical.get("order_date_inferred")
                or row.get("order_date_inferred")
                or False),
            "_inbox_row": {"raw_payload": row.get("raw_payload")},
        }
        created = _extract_order_created_at(pseudo_order)
        raw_pl = row.get("raw_payload") or {}
        raw_data = (raw_pl.get("data") if isinstance(raw_pl, dict)
                    else {}) or {}
        raw_data_date = raw_data.get("date") \
            if isinstance(raw_data, dict) else None
        date_debug = {
            "available_date_fields": {
                "canonical_payload.salla_order_created_at":
                    canonical.get("salla_order_created_at"),
                "canonical_payload.order_date":
                    canonical.get("order_date"),
                "canonical_payload.created_at":
                    canonical.get("created_at"),
                "row.salla_order_created_at":
                    row.get("salla_order_created_at"),
                "raw_payload.created_at": (
                    raw_pl.get("created_at")
                    if isinstance(raw_pl, dict) else None),
                "raw_payload.data.date.date": (
                    raw_data_date.get("date")
                    if isinstance(raw_data_date, dict)
                    else raw_data_date),
                "raw_payload.data.created_at":
                    raw_data.get("created_at"),
            },
            "extracted_salla_order_created_at":
                created.isoformat() if created else None,
            "extraction_source": (
                "eligible_orders._extract_order_created_at"),
            "q3_cutoff_iso": Q3_CUTOFF_ISO,
        }
        if created is None:
            raise CanaryGuardFailed(
                5, "created_at_missing",
                "No usable salla order created_at across all "
                "supported fields (see extra.date_debug).",
                extra={"date_debug": date_debug,
                       "duplicate_debug": duplicate_debug})
        if created < date.fromisoformat(Q3_CUTOFF_ISO):
            raise CanaryGuardFailed(
                5, "created_before_q3_cutoff",
                f"{created} < {Q3_CUTOFF_ISO}.",
                extra={"date_debug": date_debug,
                       "duplicate_debug": duplicate_debug})

        # Guard 6 — no real existing Qoyod invoice.
        existing = canonical.get("existing_qoyod_invoice_id") \
            or row.get("existing_qoyod_invoice_id") \
            or row.get("qoyod_invoice_id")
        if existing is not None:
            s = str(existing)
            if not (s.startswith("DRY:")
                    or s.startswith("PREVIEW:")):
                raise CanaryGuardFailed(
                    6, "real_existing_invoice_id_present",
                    f"existing_qoyod_invoice_id = {existing!r} "
                    f"looks real. Refusing.",
                    extra={"duplicate_debug": duplicate_debug})
        # Guard 6b — partial invoice-created / skipped with real id.
        if row.get("pipeline_stage") in ("INVOICE_CREATED", "SKIPPED"):
            qid_direct = row.get("qoyod_invoice_id")
            if qid_direct is not None:
                qs = str(qid_direct)
                if qs and not (qs.startswith("DRY:")
                               or qs.startswith("PREVIEW:")):
                    raise CanaryGuardFailed(
                        6, "partial_real_invoice_state",
                        f"pipeline_stage={row.get('pipeline_stage')!r} "
                        f"with a real qoyod_invoice_id — refusing to "
                        f"re-create.",
                        extra={"duplicate_debug": duplicate_debug,
                               "qoyod_invoice_id": qid_direct})

        # Guard 8 — customer phone match.
        cust = canonical.get("customer") or {}
        import re
        phone = re.sub(r"[^\d+]", "",
                       str(cust.get("mobile")
                           or cust.get("phone") or ""))
        if phone != REQUIRED_MOBILE:
            raise CanaryGuardFailed(
                8, "customer_mobile_mismatch",
                f"Expected {REQUIRED_MOBILE}, got {phone!r}.",
                extra={"duplicate_debug": duplicate_debug})

        # Guard 9 — customer email match.
        email = (cust.get("email") or "").strip().lower()
        if email != REQUIRED_EMAIL:
            raise CanaryGuardFailed(
                9, "customer_email_mismatch",
                f"Expected {REQUIRED_EMAIL}, got {email!r}.",
                extra={"duplicate_debug": duplicate_debug})

        # Guard 10 — Mezan-VAT totals guard.
        from integrations.qoyod.eligible_orders import _check_totals
        totals = _check_totals(canonical)
        if not totals["valid"]:
            raise CanaryGuardFailed(
                10, "totals_mismatch_gt_0_01",
                f"Mezan-VAT-15% diff={totals['diff']} > 0.01.",
                extra={"duplicate_debug": duplicate_debug})

        # If we reach here without a specific guard firing but
        # `latest_ok` is False, something is inconsistent.
        raise CanaryGuardFailed(
            2, "latest_row_criteria_inconsistent",
            f"Latest row rejected ({latest_reason!r}) but no "
            f"specific guard fired — refusing conservatively.",
            extra={"duplicate_debug": duplicate_debug})

    # Latest row passes. Use it exclusively (no fallback).
    row = latest_row
    canonical = row.get("canonical_payload") or {}
    selected_trace_id = row.get("trace_id")
    if not selected_trace_id:
        raise CanaryGuardFailed(
            2, "selected_row_missing_trace_id",
            "The latest canary-eligible row has no trace_id; "
            "cannot disambiguate against reprocess pipeline.",
            extra={"duplicate_debug": duplicate_debug})

    # Selection metadata for the response.
    selection_debug = {
        "selected_trace_id":         selected_trace_id,
        "latest_trace_id":           latest_trace_id,
        "selected_is_latest":        True,
        "latest_normalized_status":  latest_norm_status,
        **duplicate_debug,
    }
    return (raw_settings, canonical, settings_debug, selection_debug)


async def execute_canary_live_send(
    db,
    *,
    order_number: str,
    approval_phrase: str,
    actor: str = "operator",
    user_id: str = "main",
) -> dict:
    """Executes the one-shot canary. Read-heavy; writes only into
    `canary_send_audit_log` (audit) and delegates the actual Qoyod
    calls to the existing `reprocess_one_order` (audited by
    `qoyod_per_order_approvals` on its own)."""
    attempt_id = str(uuid.uuid4())
    await _write_audit(db, attempt_id=attempt_id,
                       phase="attempt_received", status="pending",
                       detail=f"actor={actor} user_id={user_id}")
    try:
        settings, canonical, settings_debug, selection_debug = \
            await _run_guards(
                db, order_number=order_number,
                approval_phrase=approval_phrase,
                user_id=user_id)
    except CanaryGuardFailed as g:
        # Best-effort re-read of settings debug for refusal response
        # even when guards 1/2 short-circuit before settings load.
        debug_snapshot: dict = {}
        try:
            _raw = await db.qoyod_settings.find_one(
                {"user_id": user_id}, {"_id": 0}) or {}
            _rs = _raw.get("selective_live_send_enabled")
            _rw = _raw.get("production_writes_locked")
            _rd = _raw.get("dry_run_mode")
            _renabled = _raw.get(
                "qoyod_enabled_invoice_trigger_statuses")
            _rinv_trig = _raw.get("invoice_trigger_statuses")
            _base_enabled = (list(_renabled)
                             if isinstance(_renabled,
                                           (list, tuple, set,
                                            frozenset))
                             else [])
            debug_snapshot = {
                "settings_source":                  "qoyod_settings",
                "settings_user_id":                 user_id,
                "settings_doc_present":             bool(_raw),
                "raw_selective_live_send_enabled":  _rs,
                "raw_selective_live_send_enabled_type":
                    type(_rs).__name__,
                "raw_production_writes_locked":     _rw,
                "raw_production_writes_locked_type":
                    type(_rw).__name__,
                "raw_dry_run_mode":                 _rd,
                "raw_dry_run_mode_type":            type(_rd).__name__,
                # Trigger-status whitelist (rev9).
                "raw_qoyod_enabled_invoice_trigger_statuses":
                    _renabled,
                "raw_invoice_trigger_statuses":     _rinv_trig,
                "effective_qoyod_enabled_invoice_trigger_statuses_for_canary":
                    list(dict.fromkeys(
                        _base_enabled + ["جاري التوصيل"])),
                "canary_status_overlay":            ["جاري التوصيل"],
                # Canary scoped policy overlay (rev8) — three fields.
                "effective_dry_run_mode_for_canary":                 False,
                "effective_selective_live_send_enabled_for_canary":  True,
                "effective_production_writes_locked_for_canary":     False,
                "policy_override_scope":
                    f"canary_order_{CANARY_ORDER_NUMBER}_only",
                "dry_run_mode_scope":
                    f"canary_order_{CANARY_ORDER_NUMBER}_only",
            }
        except Exception:
            debug_snapshot = {"settings_source_error": True}
        await _write_audit(db, attempt_id=attempt_id,
                           phase="guard_check", status="refused",
                           guard_no=g.guard_no, code=g.code,
                           detail=g.detail)
        return {
            "attempt_id":  attempt_id,
            "outcome":     "REFUSED",
            "guard_no":    g.guard_no,
            "code":        g.code,
            "detail":      g.detail,
            "no_qoyod_api_calls": True,
            "no_db_writes_to_qoyod_settings": True,
            "settings_debug": debug_snapshot,
            **(g.extra or {}),
        }

    await _write_audit(db, attempt_id=attempt_id,
                       phase="guards_passed", status="dispatching",
                       detail="all 14 guards passed")

    # ── Dispatch to the existing per-order pipeline ─────────────
    # `reprocess_one_order` handles: scoped write-lock bypass +
    # policy assert + api_client build + full pipeline invocation.
    # Its contract requires TWO parameters:
    #   • confirm         = "REPROCESS-<order_number>"
    #   • approval_phrase = "Approved to send order <n> only"
    # Both are synthesised INTERNALLY from the canary contract phrase
    # (which the operator already verified via Guard 1). The
    # operator NEVER supplies these directly.
    from integrations.qoyod.one_shot_reprocess import (
        CONFIRM_TOKEN_TEMPLATE, APPROVAL_PHRASE_TEMPLATE,
        reprocess_one_order,
    )
    internal_confirm = CONFIRM_TOKEN_TEMPLATE.format(
        order_number=CANARY_ORDER_NUMBER)
    internal_phrase = APPROVAL_PHRASE_TEMPLATE.format(
        order_number=CANARY_ORDER_NUMBER)
    selected_trace_id = selection_debug.get("selected_trace_id")
    # If the selected row is stuck at INVOICE_CREATED without a real
    # Qoyod invoice_id, ask reprocess_one_order to reset the partial
    # state and rebuild the invoice from scratch. Guarded above:
    # `_row_matches_canary_criteria` already refused rows with a
    # REAL qoyod_invoice_id at this stage.
    _stage = None
    _qid   = None
    for _r in selection_debug.get("duplicate_rows_summary", []):
        if _r.get("trace_id") == selected_trace_id:
            _stage = _r.get("pipeline_stage")
            _qid   = _r.get("existing_qoyod_invoice_id")
            break
    _allow_partial_reset = (
        _stage in ("INVOICE_CREATED", "SKIPPED")
        and (_qid is None
             or str(_qid).startswith(("DRY:", "PREVIEW:"))))
    # Build the scoped DB proxy: `qoyod_settings.find_one` returns a
    # copy with `dry_run_mode=False`. Everything else forwards.
    canary_db = _CanaryDBProxy(db)
    try:
        result = await reprocess_one_order(
            canary_db,
            user_id=user_id,
            order_number=CANARY_ORDER_NUMBER,
            trace_id=selected_trace_id,
            confirm=internal_confirm,
            approval_phrase=internal_phrase,
            actor=f"canary:{actor}",
            allow_reset_from_partial_invoice_created=(
                _allow_partial_reset))
    except Exception as e:
        # Extract structured OneShotRefused.extra for diagnostics
        # (attributes may include: current_stage, resume_stage,
        # reset_path_attempted, permit_partial_invoice_created,
        # needs_retry_hop, state_machine_allowed_edges_for_current_stage).
        _extra = {}
        try:
            _extra = getattr(e, "extra", None) or {}
        except Exception:
            _extra = {}
        await _write_audit(
            db, attempt_id=attempt_id,
            phase="pipeline_exception", status="error",
            code=type(e).__name__, detail=str(e)[:500])
        return {
            "attempt_id": attempt_id,
            "outcome":    "PIPELINE_ERROR",
            "code":       type(e).__name__,
            "detail":     str(e)[:500],
            "internal_confirm_used":         internal_confirm,
            "internal_confirm_template":     CONFIRM_TOKEN_TEMPLATE,
            "internal_approval_phrase_used": internal_phrase,
            "internal_approval_phrase_template":
                APPROVAL_PHRASE_TEMPLATE,
            "selected_trace_id":             selected_trace_id,
            "selection_debug":               selection_debug,
            "allow_reset_from_partial_invoice_created":
                _allow_partial_reset,
            "one_shot_refused_extra":        _extra,
            # Convenience alias for the ONE field the operator asked
            # for most: what path did the reset attempt?
            "reset_path_attempted":
                _extra.get("reset_path_attempted"),
        }

    await _write_audit(
        db, attempt_id=attempt_id, phase="pipeline_result",
        status=result.get("outcome") or "unknown",
        result_payload={
            "outcome":     result.get("outcome"),
            "invoice_id":  result.get("qoyod_invoice_id"),
            "customer_id": result.get("qoyod_customer_id"),
            "product_used": {
                "sku": REQUIRED_SKU,
                "qoyod_product_id": REQUIRED_QOYOD_PRODUCT_ID,
            },
        })
    # Determine manual_send_requested for the SELECTED row from
    # selection_debug (populated by _row_summary).
    _selected_manual = False
    _selected_normalized_status = None
    for _r in selection_debug.get("duplicate_rows_summary", []):
        if _r.get("trace_id") == selected_trace_id:
            _selected_manual = bool(_r.get("manual_send_requested"))
            _selected_normalized_status = _r.get("normalized_status")
            break
    return {
        "attempt_id":         attempt_id,
        "outcome":            result.get("outcome"),
        "qoyod_invoice_id":   result.get("qoyod_invoice_id"),
        "qoyod_customer_id":  result.get("qoyod_customer_id"),
        "qoyod_receipt_id":   result.get("qoyod_receipt_id"),
        "product_used":       {"sku": REQUIRED_SKU,
                               "qoyod_product_id":
                               REQUIRED_QOYOD_PRODUCT_ID},
        "invoice_date_source": "send_date_riyadh",
        "selected_trace_id":   selected_trace_id,
        "latest_trace_id":     selection_debug.get("latest_trace_id"),
        "selected_is_latest":
            selection_debug.get("selected_is_latest"),
        "latest_normalized_status":
            selection_debug.get("latest_normalized_status"),
        "selected_normalized_status": _selected_normalized_status,
        "manual_send_requested":      _selected_manual,
        "selection_debug":     selection_debug,
        "raw_pipeline_result": result,
    }
