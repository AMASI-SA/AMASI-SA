"""Qoyod Settings & Catalogs API routes — Day 2.

Endpoints (all mounted under /api/integrations/qoyod):
    GET  /settings                  current config
    PUT  /settings                  update config (incl. capability flags)
    POST /credentials               save API key (encrypted)
    DELETE /credentials             remove API key
    POST /test-connection           verify API key against Qoyod /me
    GET  /qoyod-branches            proxy → Qoyod /branches
    GET  /qoyod-accounts            proxy → Qoyod /accounts
    GET  /qoyod-taxes               proxy → Qoyod /taxes
    GET  /health                    connector liveness summary

ADR-001 compliance:
    #3  Feature Flag — POST /credentials does NOT auto-enable. The
        merchant must explicitly PUT /settings {enabled: true}.
    #11 Tenant       — user_id is always the authenticated user. The
        MVP forces "main" until multi-tenant is enabled platform-wide.
    #14 Secrets      — credentials never returned. /settings response
        carries `credentials.fingerprint` only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.write_lock import (
    QoyodWriteLockedError, is_locked, fail_closed_default_enabled,
    list_blocked_attempts, count_blocked_attempts_by_action,
    set_write_lock_context, reset_write_lock_context,
)
from integrations.qoyod.product_resolver import adopt_qoyod_product
from integrations.qoyod.customer_resolver import adopt_qoyod_customer
from integrations.qoyod.credentials import (
    save_api_key, get_api_key, get_fingerprint, delete_api_key, mark_verified,
)
from integrations.qoyod.models import (
    QoyodSettings, QoyodCapabilityFlags,
)
from integrations.qoyod.compliance import (
    list_orphan_orders, compliance_summary, reconciliation_check,
)
from integrations.qoyod.webhook import attach_webhook_routes
from integrations.qoyod.pipeline import (
    process_pending_normalized, process_pending_customer_resolved, day4_report,
)
from integrations.qoyod.go_live import (
    go_live_checklist, go_live_report,
    activate_production_mode, ActivationBlocked,
)
from integrations.qoyod.migration_routes import attach_migration_routes
from integrations.qoyod.fresh_start_audit import (
    run_fresh_start_audit, latest_audit,
)
from integrations.qoyod.fresh_start_cleanup import (
    build_plan, latest_plan, execute_cleanup,
    CleanupRefused, EXPECTED_CONFIRM_TOKEN, PROTECTED_ENTITIES,
)
from integrations.qoyod.first_sync_monitor import (
    list_recent_for_monitor, get_row_for_monitor,
    get_monitor_stats, archive_failed_dry_run_tests, ArchiveRefused,
    ARCHIVE_CONFIRM_TOKEN,
    find_duplicate_groups, archive_duplicate_attempts,
    DuplicateMergeRefused, DUPLICATE_CONFIRM_TOKEN,
)
from integrations.qoyod.dead_letter_requeue import (
    find_requeue_candidates, auto_requeue_known_fixed, requeue_one,
    MAX_REQUEUE_ATTEMPTS, KNOWN_FIXED_PATTERNS,
)
from integrations.qoyod.one_shot_reprocess import (
    reprocess_one_order, OneShotRefused, CONFIRM_TOKEN_TEMPLATE,
)
from integrations.qoyod.preview_reprocess import (
    preview_reprocess_one_order,
)
from integrations.qoyod.state_machine import transition, InvalidTransition
from salla_integration.service import call_salla, SallaError
from integrations.qoyod.setup_validation import (
    collect_used_payment_methods,
    validate_settings_for_setup,
    CANONICAL_PAYMENT_METHODS,
)
from integrations.qoyod.webhook_token_store import (
    generate_token,
    save_webhook_token,
    get_webhook_token_meta,
    revoke_webhook_token,
)
from integrations.qoyod.orders_owner import orders_owner_id


# MVP runs single-tenant; we still derive user_id from the auth layer
# so the schema stays multi-tenant ready (ADR-001 #11).
_MVP_TENANT_ID = "main"

logger = logging.getLogger(__name__)


def _tenant_id(user) -> str:
    """Resolve the tenant id we attach to every Qoyod document.
    Today this is fixed to 'main' regardless of the authenticated
    user — multi-tenant policy is deferred per Day-1 review."""
    return _MVP_TENANT_ID


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Iter-294 — Global Qoyod Write Lock helpers
# ─────────────────────────────────────────────────────────────────────
async def _build_qoyod_client_for(db, tenant: str, key: str) -> QoyodAPIClient:
    """Construct a QoyodAPIClient with the global write lock snapshotted
    from the current `production_writes_locked` flag in qoyod_settings.

    Iter-293.4 Fail-Closed:
        When the flag is missing AND env `QOYOD_FAIL_CLOSED_DEFAULT=true`,
        the lock defaults to True. Production deploys land already-safe.

    Any write (POST/PUT/PATCH/DELETE) through this client will be
    refused with `QoyodWriteLockedError` when locked, AND the attempt
    is recorded to `qoyod_write_lock_attempts` for audit.
    """
    settings_doc = await db.qoyod_settings.find_one(
        {"user_id": tenant}, {"_id": 0, "production_writes_locked": 1}) or {}
    return QoyodAPIClient(
        key,
        db=db, user_id=tenant,
        write_lock_enabled=is_locked(settings_doc),
    )


# ─────────────────────────────────────────────────────────────────────
# Request/Response shapes
# ─────────────────────────────────────────────────────────────────────
class SettingsPatch(BaseModel):
    """All fields optional — partial update."""
    model_config = ConfigDict(extra="forbid")
    enabled:              Optional[bool] = None
    auto_send:            Optional[bool] = None
    auto_receipt:         Optional[bool] = None
    dry_run_mode:         Optional[bool] = None
    # Day 4 Invoice Trigger Policy
    invoice_trigger_statuses: Optional[list[str]] = None
    invoice_date_source:  Optional[str] = None
    trigger_once_only:    Optional[bool] = None
    # Legacy alias (single string). If only this is provided, the API
    # layer expands it to invoice_trigger_statuses = [<value>].
    invoice_trigger_status: Optional[str] = None
    default_branch_id:    Optional[str] = None
    default_tax_id:       Optional[str] = None
    inventory_account_id: Optional[str] = None
    cost_account_id:      Optional[str] = None
    default_customer_id:  Optional[str] = None
    default_product_type: Optional[str] = None
    payment_method_mapping: Optional[list] = None
    capabilities:         Optional[QoyodCapabilityFlags] = None
    # Legacy-Adapter enrichment toggle (Salla-API fallback when items
    # are missing from the incoming webhook). Default False — opt-in.
    enrichment_fallback_enabled: Optional[bool] = None
    # Backfill mode — controls whether the worker drains pre-activation
    # rows after Go-Live (Iter-267). Default = "now_forward_only".
    backfill_mode:        Optional[str] = None
    # Iter-287 — Qoyod-required product creation defaults.
    default_product_category_id:   Optional[str] = None
    default_product_tax_id:        Optional[str] = None
    default_product_unit_type_id:  Optional[str] = None
    default_sales_account_id:      Optional[str] = None
    # Iter-285 — tax mode (customer_first | mezan_fixed_15).
    tax_mode:                      Optional[str] = None
    zero_tax_id:                   Optional[str] = None
    # Iter-288 — auto-adopt existing Qoyod products by SKU.
    auto_adopt_existing_qoyod_products: Optional[bool] = None
    # Iter-290 — Qoyod-required warehouse id on every invoice line.
    default_inventory_id:          Optional[str] = None
    # Iter-290e — Qoyod 15% Match Salla Total policy
    invoice_total_policy:          Optional[str] = None      # "match_salla_total" | "legacy_passthrough"
    qoyod_tax_percent:             Optional[float] = None    # default 15
    # Iter-290f — Shipping product id (Qoyod requires product_id on every line).
    default_shipping_product_id:   Optional[str] = None
    # Iter-293.1 — COD-fee product id (Qoyod product representing the
    # "رسوم الدفع عند الاستلام" service charge). Required ONLY when
    # incoming orders carry `amounts.cash_on_delivery > 0`. Without
    # this set, the pre-POST totals guard refuses to send the invoice
    # rather than silently dropping the fee on the floor.
    default_cod_fee_product_id:    Optional[str] = None
    # Iter-293.3 — Production Writes Kill Switch. When True, the live
    # webhook pipeline runs every stage (normalize/preflight/builders)
    # but STOPS before any POST to api.qoyod.com. Each order is parked
    # in stage LOCKED_AWAITING_APPROVAL with its fully-built payload
    # so the operator can review via Preview-Reprocess, then approve
    # per-order via one_shot_reprocess. Independent of dry_run_mode.
    production_writes_locked:      Optional[bool] = None


class CredentialsRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


class FreshStartExecutePayload(BaseModel):
    job_id:  str = Field(..., description="The plan job_id to execute")
    confirm: str = Field(..., description="Must equal DELETE-CONFIRM")


class ArchiveFailedTestsBody(BaseModel):
    confirm: str = Field(..., description="Must equal 'CLEAN'.")
    model_config = ConfigDict(extra="forbid")


class ArchiveDuplicateAttemptsBody(BaseModel):
    """Iter-280 — merge duplicate inbox rows of the same logical
    webhook delivery into a single row. Operator picks which trace
    to keep; all others go to `integration_inbox_archive`."""
    model_config = ConfigDict(extra="forbid")
    order_number:    str = Field(..., description="Salla order number")
    event:           str = Field(..., description="event_type from raw payload")
    status_slug:     str = Field(..., description="canonical status slug")
    keep_trace_id:   str = Field(..., description="trace_id to KEEP")
    confirm:         str = Field(..., description="Must equal 'MERGE'.")


class DeadLetterRequeueOneBody(BaseModel):
    """Manual single-row requeue payload. Either `row_id` or `trace_id`
    must be provided. `force=True` bypasses MAX_REQUEUE_ATTEMPTS
    (operator override — logged in audit trail)."""
    model_config = ConfigDict(extra="forbid")
    row_id:   Optional[str] = None
    trace_id: Optional[str] = None
    force:    bool = False


class DeadLetterAutoRequeueBody(BaseModel):
    """Manual auto-requeue trigger payload. Defaults match the worker
    behaviour (production rows only)."""
    model_config = ConfigDict(extra="forbid")
    include_dry_run: bool = False


class AdoptProductBody(BaseModel):
    """SSOT Trust Gate adoption — operator explicitly onboards a
    legacy Qoyod product into Mezan's local mapping. After adoption
    the resolver stops blocking orders that need this SKU."""
    model_config = ConfigDict(extra="forbid")
    sku: str
    qoyod_product_id: str
    qoyod_product_name: Optional[str] = None
    note: Optional[str] = None


class AdoptCustomerBody(BaseModel):
    """Iter-293.5-rev4 — Local-only customer adoption.

    The operator has manually verified / created the buyer in Qoyod
    and wants Mezan to bind their `lookup_key` (phone / email) to
    that Qoyod `contact_id`. This does NOT hit Qoyod's API; it only
    upserts the local `qoyod_customers_mapping` row.

    `lookup_key` gets E.164-normalised for `lookup_kind='phone'` so
    it matches the runtime pipeline's derivation from Salla.
    """
    model_config = ConfigDict(extra="forbid")
    lookup_key: str
    lookup_kind: str = Field(..., pattern="^(phone|email)$")
    qoyod_contact_id: str
    qoyod_contact_name: Optional[str] = None
    note: Optional[str] = None


class OneShotReprocessBody(BaseModel):
    """Strict single-order reprocess payload. The operator MUST supply
    `order_number` (the human-readable Salla order id) plus a
    `confirm` token that equals `REPROCESS-<order_number>`. The
    optional `trace_id` disambiguates when multiple inbox rows exist
    for the same order_number (e.g. multiple status webhooks).

    Iter-293.4-rev3 — Per-Order Approval Phrase:
    When `production_writes_locked=True` (the global kill switch is
    on), the operator MUST also supply an `approval_phrase` equal to
    exactly:
        "Approved to send order <order_number> only"
    The phrase unlocks the api_client for THIS one order only — the
    global `production_writes_locked` setting is NEVER toggled. The
    approval is audited to `qoyod_per_order_approvals`.
    """
    model_config = ConfigDict(extra="forbid")
    order_number: str = Field(..., min_length=1, max_length=64)
    confirm:      str = Field(..., min_length=1, max_length=128)
    trace_id:     Optional[str] = None
    approval_phrase: Optional[str] = Field(
        default=None, max_length=256,
        description=("Per-order approval that overrides "
                     "production_writes_locked for this single order. "
                     "Must equal 'Approved to send order <N> only'."))


class PreviewReprocessBody(BaseModel):
    """Safe simulation — re-runs adapter → normalizer → builders for a
    single inbox row without ANY network call to Qoyod. No confirm
    token required (no side-effects). Either order_number OR trace_id
    must be supplied; trace_id wins when both are given."""
    model_config = ConfigDict(extra="forbid")
    order_number: Optional[str] = None
    trace_id:     Optional[str] = None


class FinalizeRoundingWarningBody(BaseModel):
    """Iter-293.4-rev8 — Explicit operator action to mark an
    INVOICE_CREATED row as COMPLETED_WITH_ROUNDING_WARNING when the
    قيود-actual total differs from Salla's by at most 0.01 SAR due
    to known قيود server-side rounding behaviour.

    Strict invariants:
      • The row MUST be at pipeline_stage `INVOICE_CREATED` (no other
        stage is finalisable this way).
      • The row MUST already have a real (non-DRY) `qoyod_invoice_id`.
      • The persisted `totals_comparison.difference` MUST be in the
        (0.005, 0.01] SAR window (or, if missing, the operator must
        supply an explicit `accept_difference_sar` value within that
        window — used for retroactive finalisation of rows from before
        the post-create verification shipped).
      • Confirm token MUST equal `FINALIZE-ROUNDING-<order_number>`.
      • NO قيود calls. Local DB writes ONLY (inbox row stage +
        ledger status + dedicated audit collection).
    """
    model_config = ConfigDict(extra="forbid")
    order_number: str = Field(..., min_length=1, max_length=64)
    trace_id:     Optional[str] = None
    confirm:      str = Field(..., min_length=1, max_length=128)
    # Optional override for rows that pre-date the totals_comparison
    # persistence (production order 269571122 fell into this gap).
    accept_difference_sar: Optional[float] = Field(
        default=None, ge=-0.01, le=0.01,
        description=("Operator-stated difference in SAR. Required when "
                     "the row carries no totals_comparison. Must be in "
                     "[-0.01, 0.01]."))
    operator_note: Optional[str] = Field(
        default=None, max_length=512,
        description="Free-text note appended to the audit row.")


class TestConnectionResponse(BaseModel):
    ok:          bool
    fingerprint: Optional[str] = None
    qoyod_user:  Optional[dict] = None
    error:       Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────
# Iter-2026-02.rev14 — Module-scope Pydantic body models.
# FastAPI's type introspection mis-classifies models defined INSIDE
# `make_qoyod_router()` (function scope) as query parameters — the
# resulting 422 reports `{"loc": ["query", "body"]}` even when the
# caller sent a valid JSON body. Declaring them at module scope
# (with an explicit `Body(...)` binding on the handler) resolves
# the ForwardRef cleanly and makes the endpoints usable.
# ─────────────────────────────────────────────────────────────────────
class RetryPaymentOnlyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    salla_order_number: str = Field(..., min_length=1, max_length=64)
    confirm_token:      str = Field(..., min_length=1, max_length=128)


class AdoptExistingPaymentBody(BaseModel):
    """Iter-2026-02.rev15 — Body model for
    `POST /admin/adopt-existing-payment`. Declared at MODULE scope
    for the same reason as `RetryPaymentOnlyBody` (function-scope
    Pydantic models trigger FastAPI's `loc: ["query","body"]`
    misclassification).
    """
    model_config = ConfigDict(extra="forbid")
    salla_order_number:       str = Field(..., min_length=1, max_length=64)
    qoyod_invoice_payment_id: str = Field(..., min_length=1, max_length=64)
    confirm_token:            str = Field(..., min_length=1, max_length=128)
    qoyod_invoice_id:  Optional[str] = Field(None, max_length=64)
    qoyod_customer_id: Optional[str] = Field(None, max_length=64)


class EnableSelectiveAutoSendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_token: str = Field(..., min_length=1, max_length=128)
    allowed_payment_methods: Optional[list[str]] = Field(
        None, max_length=32)


class DisableSelectiveAutoSendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_token: str = Field(..., min_length=1, max_length=128)


class ExpandSelectiveAutoSendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_token: str      = Field(..., min_length=1, max_length=128)
    add_methods:   list[str] = Field(..., min_length=1, max_length=16)


class ForceReprocessDryBody(BaseModel):
    """Iter-2026-02.rev18 — Force-reprocess a row stuck at DRY."""
    model_config = ConfigDict(extra="forbid")
    salla_order_number: str = Field(..., min_length=1, max_length=64)
    confirm_token:      str = Field(..., min_length=1, max_length=128)
    trace_id:  Optional[str] = Field(None, max_length=64)


class ApproveLockedPaymentBody(BaseModel):
    """Iter-2026-02.rev21 — Approve a parked invoice_payment."""
    model_config = ConfigDict(extra="forbid")
    lock_attempt_id: str = Field(..., min_length=1, max_length=64)
    confirm_token:   str = Field(..., min_length=1, max_length=128)


class EnableTabbyLiveCanaryBody(BaseModel):
    """Iter-2026-02.rev31 — Open Tabby-only Live Canary. Flips
    dry_run_mode=False, production_writes_locked=False,
    selective_live_send_enabled=True. Refuses if preconditions fail
    (auto_send must be OFF, allow-list must be exactly
    ['tabby_installment'], auto_receipt + create_receipts must be
    True)."""
    model_config = ConfigDict(extra="forbid")
    confirm_token: str = Field(..., min_length=1, max_length=128)


class DisableTabbyLiveCanaryBody(BaseModel):
    """Iter-2026-02.rev31 — Rollback to fail-closed posture. Flips
    dry_run_mode=True, production_writes_locked=True,
    selective_live_send_enabled=False. Always succeeds."""
    model_config = ConfigDict(extra="forbid")
    confirm_token: str          = Field(..., min_length=1, max_length=128)
    reason:        Optional[str] = Field(None, max_length=256)


# ─────────────────────────────────────────────────────────────────────
def make_qoyod_router(db, current_user) -> APIRouter:
    router = APIRouter(
        prefix="/integrations/qoyod",
        tags=["integrations:qoyod"],
    )

    # Standalone Qoyod invoice review: local-mirror reads, GET-only sync,
    # and Excel export.  Register before the legacy invoice endpoints so
    # this independent surface can never be mistaken for an invoice-send
    # operation.
    from integrations.qoyod.invoice_review_routes import (
        attach_invoice_review_routes,
    )
    attach_invoice_review_routes(
        router,
        db=db,
        current_user=current_user,
        tenant_id=_tenant_id,
        orders_owner_id=orders_owner_id,
        get_api_key=get_api_key,
        build_api_client=_build_qoyod_client_for,
    )

    async def _load_settings(tenant: str) -> dict:
        doc = await db.qoyod_settings.find_one(
            {"user_id": tenant}, {"_id": 0})
        if not doc:
            # First read — return defaults without writing yet.
            defaults = QoyodSettings(user_id=tenant).model_dump(mode="json")
            return defaults
        # Backwards-compat shim: legacy docs only carried
        # `invoice_trigger_status` (singular). If the new list field
        # is missing, derive it from the legacy value so downstream
        # code never has to worry about the old shape.
        if not doc.get("invoice_trigger_statuses"):
            legacy = doc.get("invoice_trigger_status")
            doc["invoice_trigger_statuses"] = (
                [legacy] if legacy else ["completed"])
        if "trigger_once_only" not in doc:
            doc["trigger_once_only"] = True
        # Iter-293.4-rev9 — Auto-migrate legacy tenants whose
        # invoice_date_source still points at Salla-side timestamps.
        # See pipeline._load_settings for the rationale.
        if doc.get("invoice_date_source") in ("completed_at",
                                               "trigger_status_date",
                                               None, ""):
            doc["invoice_date_source"] = "send_date"
        return doc

    async def _attach_fingerprint(tenant: str, payload: dict) -> dict:
        fp = await get_fingerprint(db, tenant)
        payload["credentials"] = {
            "configured":  bool(fp),
            "fingerprint": fp,
        }
        # The existing settings page is the control plane for the validated
        # Plan-B automatic sender. Never expose the stored Orders owner id.
        from integrations.qoyod_manual.auto_send import status_snapshot
        payload["plan_b_auto_send_status"] = status_snapshot(payload)
        return payload

    # ── GET /settings ────────────────────────────────────────────────
    @router.get("/settings")
    async def get_settings(user=Depends(current_user)):
        tenant = _tenant_id(user)
        s = await _load_settings(tenant)
        return await _attach_fingerprint(tenant, s)

    # ── PUT /settings ────────────────────────────────────────────────
    @router.put("/settings")
    async def update_settings(
        patch: SettingsPatch, user=Depends(current_user)):
        tenant = _tenant_id(user)
        current = await _load_settings(tenant)
        # Merge: only non-None fields from the patch overwrite.
        update = patch.model_dump(exclude_none=True, mode="json")
        # Backwards-compat: if the caller only sent the legacy
        # `invoice_trigger_status` (singular), expand it into the new
        # list field so the canonical shape stays consistent.
        if "invoice_trigger_status" in update and \
           "invoice_trigger_statuses" not in update:
            legacy_val = update.pop("invoice_trigger_status")
            update["invoice_trigger_statuses"] = (
                [legacy_val] if legacy_val else ["completed"])
        if not update:
            raise HTTPException(400, "no fields to update")
        # Iter-293 — Enforce the COD invariant at the API write boundary.
        # ANY row whose salla_method is in the COD family is coerced to
        # posting_mode=credit_invoice_only + qoyod_account_id=None, even
        # if the UI submits otherwise (defense in depth — the UI lock is
        # one layer, this is the second).
        if "payment_method_mapping" in update and isinstance(
            update["payment_method_mapping"], list,
        ):
            from integrations.qoyod.payment_methods import coerce_cod_rows
            try:
                # There is no catch-all transfer account anymore.  Persist
                # only bank-specific destinations so an unknown bank can
                # never be posted accidentally to an arbitrary account.
                generic_bank_keys = {
                    "bank", "bank_transfer", "wire_transfer", "تحويل_بنكي",
                }
                update["payment_method_mapping"] = [
                    row for row in update["payment_method_mapping"]
                    if str(row.get("salla_method") or "").strip().lower()
                    .replace(" ", "_") not in generic_bank_keys
                ]
                update["payment_method_mapping"] = coerce_cod_rows(
                    update["payment_method_mapping"])
            except ValueError as ve:
                # Iter-293.1 — `coerce_cod_rows` raises on the
                # bank_transfer = credit_invoice_only mis-configuration.
                # Translate to a proper HTTP 400 with the operator-facing
                # message intact.
                raise HTTPException(
                    status_code=400,
                    detail={"code": "INVALID_POSTING_MODE_FOR_BANK_TRANSFER",
                            "message": str(ve)},
                )
        # Validate the merged result via Pydantic so we never persist
        # an invalid combination (ADR-001 #4 Canonical Domain).
        merged = {**current, **update,
                  "user_id":    tenant,
                  "updated_at": _now()}
        valid = QoyodSettings(**merged).model_dump(mode="json")
        # Saving the existing master switches is the only activation path for
        # the validated Plan-B sender. It reuses the proven manual engine; the
        # legacy pipeline remains frozen. Disabling either switch or enabling
        # Dry Run disarms it immediately.
        from integrations.qoyod_manual.auto_send import (
            activation_issues,
            is_live_requested,
        )
        if is_live_requested(valid):
            orders_owner = orders_owner_id(user)
            canary = await db.qoyod_manual_canary_runs.find_one(
                {"status": "succeeded"},
                {"_id": 0, "run_id": 1, "finished_at": 1},
                sort=[("finished_at", -1)],
            )
            key = await get_api_key(db, tenant)
            salla_integration = await db.salla_integrations.find_one(
                {"user_id": orders_owner, "status": "connected"},
                {"_id": 0, "user_id": 1},
            )
            issues = activation_issues(
                valid,
                credentials_configured=bool(key),
                canary_succeeded=bool(canary),
                salla_connected=bool(salla_integration),
            )
            if issues:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "plan_b_auto_send_not_ready",
                        "message": "تعذر تفعيل الإرسال التلقائي بأمان",
                        "issues": issues,
                    },
                )
            valid["plan_b_auto_send_armed_at"] = (
                valid.get("plan_b_auto_send_armed_at")
                or _now().isoformat()
            )
            valid["plan_b_auto_send_orders_user_id"] = orders_owner
            valid["plan_b_auto_send_actor"] = str(
                (user or {}).get("email") or user["id"]
            )
            valid["plan_b_auto_send_canary_run_id"] = canary.get("run_id")
            valid["plan_b_auto_send_disabled_reason"] = None
            valid["plan_b_auto_send_last_error"] = None
        else:
            valid["plan_b_auto_send_armed_at"] = None
            valid["plan_b_auto_send_disabled_at"] = _now().isoformat()
            if not valid.get("enabled"):
                disabled_reason = "connector_disabled"
            elif not valid.get("auto_send"):
                disabled_reason = "operator_disabled"
            else:
                disabled_reason = "dry_run_enabled"
            valid["plan_b_auto_send_disabled_reason"] = disabled_reason
        # `created_at` is set by $setOnInsert only — pulling it out of
        # $set avoids the Mongo "conflict at path" write error when the
        # document is being upserted for the first time.
        valid.pop("created_at", None)
        await db.qoyod_settings.update_one(
            {"user_id": tenant},
            {"$set": valid,
             "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
        return await _attach_fingerprint(tenant, valid)

    # ── POST /credentials ────────────────────────────────────────────
    @router.post("/credentials")
    async def save_credentials(
        body: CredentialsRequest, user=Depends(current_user)):
        tenant = _tenant_id(user)
        result = await save_api_key(db, tenant, body.api_key)
        # Don't auto-enable. Merchant decides explicitly.
        return {"ok": True, **result}

    # ── DELETE /credentials ──────────────────────────────────────────
    @router.delete("/credentials")
    async def remove_credentials(user=Depends(current_user)):
        tenant = _tenant_id(user)
        # Force-disable to avoid sync attempts with no key.
        await db.qoyod_settings.update_one(
            {"user_id": tenant}, {"$set": {
                "enabled": False,
                "auto_send": False,
                "plan_b_auto_send_armed_at": None,
                "plan_b_auto_send_disabled_at": _now(),
                "plan_b_auto_send_disabled_reason": "credentials_removed",
            }})
        ok = await delete_api_key(db, tenant)
        return {"ok": ok}

    # ── Webhook Token (Make.com → Mezan shared secret) ───────────────
    # Lifecycle: GET status → POST generate (returns plaintext ONCE) →
    # DELETE revoke. After generate, only fingerprint is ever exposed.
    @router.get("/webhook-token")
    async def webhook_token_status(user=Depends(current_user)):
        tenant = _tenant_id(user)
        meta = await get_webhook_token_meta(db, tenant)
        return {"ok": True, "configured": bool(meta and meta.get("configured")),
                "meta": meta}

    @router.post("/webhook-token/generate")
    async def webhook_token_generate(user=Depends(current_user)):
        """Generates a fresh strong token, persists it encrypted, and
        returns the plaintext EXACTLY ONCE. Any subsequent read exposes
        only the fingerprint.

        Rotating: calling this endpoint again replaces the previous
        token immediately (no overlap). Make.com must be updated before
        the next webhook fires."""
        tenant = _tenant_id(user)
        token = generate_token()
        meta = await save_webhook_token(db, tenant, token)
        # The plaintext is returned ONLY here.
        return {"ok": True, "token": token, "meta": meta,
                "warning": "هذه القيمة لن تظهر مرة أخرى — احفظها فوراً في Make.com"}

    @router.delete("/webhook-token")
    async def webhook_token_revoke(user=Depends(current_user)):
        tenant = _tenant_id(user)
        ok = await revoke_webhook_token(db, tenant)
        return {"ok": ok}

    # ── POST /test-connection ────────────────────────────────────────
    @router.post("/test-connection", response_model=TestConnectionResponse)
    async def test_connection(user=Depends(current_user)):
        tenant = _tenant_id(user)
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(400, "no_credentials")
        try:
            me = await QoyodAPIClient(key).me()
            await mark_verified(db, tenant)
            return TestConnectionResponse(
                ok=True,
                fingerprint=await get_fingerprint(db, tenant),
                qoyod_user=me if isinstance(me, dict) else {"raw": str(me)[:300]},
            )
        except QoyodAPIError as exc:
            return TestConnectionResponse(
                ok=False,
                fingerprint=await get_fingerprint(db, tenant),
                error=exc.to_log_dict(),
            )

    # ── Catalogs proxies — read-only convenience for the UI ──────────
    # Note: As of Qoyod API 2.0 (legacy.qoyod.com), `/branches` and
    # `/taxes` are NOT exposed as REST resources — branches live as
    # "Locations" in the Qoyod UI and tax rates are inlined in invoice
    # payloads. We short-circuit those proxies so the operator sees a
    # clear "unsupported" marker instead of an opaque 404.
    _UNSUPPORTED_CATALOGS = {
        "list_branches": {
            "code": "qoyod_catalog_not_exposed",
            "message": "Qoyod 2.0 API does not expose a /branches "
                       "endpoint — enter the Branch ID manually from "
                       "Qoyod → الإعدادات → الفروع.",
            "qoyod_ui_path": "/settings/branches",
        },
        "list_taxes": {
            "code": "qoyod_catalog_not_exposed",
            "message": "Qoyod 2.0 API does not expose a /taxes endpoint "
                       "— enter the Tax ID manually from Qoyod → "
                       "الإعدادات → الضرائب.",
            "qoyod_ui_path": "/settings/taxes",
        },
    }

    async def _proxied_catalog(tenant: str, fetcher_name: str):
        # Short-circuit endpoints that Qoyod does NOT expose. The UI
        # falls back to free-text input for the corresponding ID.
        if fetcher_name in _UNSUPPORTED_CATALOGS:
            return {"ok": True, "data": [],
                    "unsupported": True,
                    **_UNSUPPORTED_CATALOGS[fetcher_name]}
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(400, "no_credentials")
        try:
            client = QoyodAPIClient(key)
            fn = getattr(client, fetcher_name)
            data = await fn()
            return {"ok": True, "data": data, "unsupported": False}
        except QoyodAPIError as exc:
            return {"ok": False, "unsupported": False,
                    "error": exc.to_log_dict()}

    @router.get("/qoyod-branches")
    async def qoyod_branches(user=Depends(current_user)):
        return await _proxied_catalog(_tenant_id(user), "list_branches")

    @router.get("/qoyod-accounts")
    async def qoyod_accounts(user=Depends(current_user)):
        return await _proxied_catalog(_tenant_id(user), "list_accounts")

    @router.get("/qoyod-taxes")
    async def qoyod_taxes(user=Depends(current_user)):
        return await _proxied_catalog(_tenant_id(user), "list_taxes")

    @router.get("/qoyod-inventories")
    async def qoyod_inventories(user=Depends(current_user)):
        # Iter-290 — populate `default_inventory_id` from real Qoyod
        # warehouses so the operator can't typo a non-existent id.
        return await _proxied_catalog(_tenant_id(user), "list_inventories")

    # ── Tenant identity diagnostics ─────────────────────────────────
    # Surfaces enough Qoyod-side evidence (org/branches + sample
    # products + sample customers) so the operator can verify the API
    # key Mezan is using belongs to the SAME Qoyod tenant they see in
    # the Qoyod web UI. Critical guard before Go-Live activation.
    @router.get("/diagnostics/identity")
    async def diagnostics_identity(user=Depends(current_user)):
        from integrations.qoyod.identity_diagnostics import \
            run_identity_diagnostics
        tenant = _tenant_id(user)
        result = await run_identity_diagnostics(db, tenant)
        return result

    # ── Setup helpers — payment methods discovery & validation ──────
    @router.get("/payment-methods/used")
    async def payment_methods_used(user=Depends(current_user)):
        """Returns every payment-method key that has appeared on real
        store data so the Settings UI can mandate a mapping for each.

        Per Iter 2026-02-26 each row also carries:
          • `provider_family` — the alias-collapsed base provider
            (e.g. `tamara` for `tamara_installment`).
          • `mapped_via`      — null | "direct" | "alias" indicating how
            the current settings resolve this key today.
          • `resolved_account_id` — the Qoyod account it maps to today.

        The UI uses these to show "✓ مربوطة عبر تمارا" instead of
        "غير مربوطة" when an alias already covers a variant.
        """
        from integrations.qoyod.payment_methods import (
            explain_resolution, PAYMENT_METHOD_ALIASES,
        )
        tenant = _tenant_id(user)
        used = await collect_used_payment_methods(db, user_id=tenant)
        settings = await db.qoyod_settings.find_one(
            {"user_id": tenant}, {"_id": 0}) or {}
        for row in used:
            info = explain_resolution(settings, row.get("key"))
            row["provider_family"]     = info["family"]
            row["mapped_via"]          = info["matched_via"]
            row["matched_key"]         = info["matched_key"]
            row["resolved_account_id"] = info["qoyod_account_id"]
        return {"ok": True,
                "used": used,
                "catalogue": CANONICAL_PAYMENT_METHODS,
                "aliases":   PAYMENT_METHOD_ALIASES}

    @router.get("/setup/validate")
    async def setup_validate(user=Depends(current_user)):
        """One-shot Settings-page validation. Powers the green/red
        banner at the top of the page and the disabled-state of the
        Save button."""
        tenant = _tenant_id(user)
        return {"ok": True,
                "validation": await validate_settings_for_setup(
                    db, user_id=tenant)}


    # ── GET /health — terse connector status for monitoring ──────────
    @router.get("/health")
    async def health(user=Depends(current_user)):
        tenant = _tenant_id(user)
        s = await _load_settings(tenant)
        fp = await get_fingerprint(db, tenant)
        last_invoice = await db.qoyod_invoices.find_one(
            {"user_id": tenant},
            {"_id": 0, "status": 1, "last_error": 1, "updated_at": 1,
             "sent_at": 1},
            sort=[("updated_at", -1)],
        )
        failed_count = await db.qoyod_invoices.count_documents(
            {"user_id": tenant,
             "status": {"$in": ["failed", "invoice_sent_receipt_failed"]}})
        return {
            "connector_key":      "qoyod",
            "enabled":            bool(s.get("enabled")),
            "credentials_loaded": bool(fp),
            "fingerprint":        fp,
            "auto_send":          bool(s.get("auto_send")),
            "auto_receipt":       bool(s.get("auto_receipt")),
            "default_product_type": s.get("default_product_type"),
            "last_invoice":       last_invoice,
            "failed_count":       failed_count,
        }

    # ── GET /invoices — Data Grid feed (Pre-Day 3 placeholder) ───────
    @router.get("/invoices")
    async def list_invoices(
        user=Depends(current_user),
        status_filter: Optional[str] = None,
        eligibility: Optional[str]   = None,
        limit: int = 100,
    ):
        tenant = _tenant_id(user)
        q: dict = {"user_id": tenant}
        if status_filter:
            q["status"] = status_filter
        if eligibility:
            q["eligibility_status"] = eligibility
        cursor = db.qoyod_invoices.find(
            q, {"_id": 0}).sort("updated_at", -1).limit(max(1, min(limit, 500)))
        rows = []
        async for r in cursor:
            rows.append(r)
        return {"ok": True, "count": len(rows), "items": rows}

    # ── GET /invoices/{order_id} — full record + timeline ────────────
    @router.get("/invoices/{order_id}")
    async def get_invoice(order_id: str, user=Depends(current_user)):
        tenant = _tenant_id(user)
        row = await db.qoyod_invoices.find_one(
            {"user_id": tenant, "salla_order_id": order_id},
            {"_id": 0},
        )
        if not row:
            raise HTTPException(404, "invoice_not_found")
        # Also pull the matching inbox row's history so the Timeline UI
        # shows the full pipeline (inbox-side + invoice-side merged).
        # Iter-290h.6 — the projection now ALSO carries the قيود
        # payloads/responses + the new `qoyod_invoice_payment_id` so
        # the operator can verify, from this drawer alone, that the
        # `POST /invoice_payments` step ran and what قيود returned.
        inbox = await db.integration_inbox.find_one(
            {"user_id": tenant, "salla_order_id": order_id},
            {"_id": 0,
             "stage_history": 1, "pipeline_stage": 1, "pipeline_error": 1,
             "pipeline_outcome": 1, "pipeline_started_at": 1,
             "pipeline_finished_at": 1, "pipeline_duration_ms": 1,
             "last_success_stage": 1, "last_failed_stage": 1,
             "attempts": 1, "trace_id": 1, "received_at": 1,
             "connector_key": 1,
             # Iter-290h.6 additions ↓
             "qoyod_invoice_id": 1,
             "qoyod_invoice_payment_id": 1,
             "qoyod_customer_id": 1,
             "qoyod_receipt_id": 1,
             "qoyod_payloads.invoice": 1,
             "qoyod_payloads.invoice_payment": 1,
             "qoyod_responses.invoice.body": 1,
             "qoyod_responses.invoice.qoyod_id": 1,
             "qoyod_responses.invoice_payment.body": 1,
             "qoyod_responses.invoice_payment.qoyod_id": 1,
             "qoyod_responses.invoice_payment.error": 1,
             "qoyod_responses.invoice.error": 1,
            },
            sort=[("received_at", -1)],
        )
        return {"ok": True, "invoice": row, "inbox": inbox}

    # ── GET /compliance/orphan-orders — Compliance Watch table ───────
    @router.get("/compliance/orphan-orders")
    async def compliance_orphans(
        user=Depends(current_user),
        limit: int = 200,
    ):
        tenant = _tenant_id(user)
        items = await list_orphan_orders(
            db, tenant, limit=max(1, min(limit, 1000)))
        return {"ok": True, "count": len(items), "items": items}

    # ── GET /compliance/summary — Dashboard Alert counts ─────────────
    @router.get("/compliance/summary")
    async def compliance_summary_endpoint(user=Depends(current_user)):
        tenant = _tenant_id(user)
        return {"ok": True, "summary": await compliance_summary(db, tenant)}

    # ── GET /compliance/reconciliation — Reconciliation Card ─────────
    # Three-number diff: eligible Salla orders vs invoices in Qoyod.
    @router.get("/compliance/reconciliation")
    async def compliance_reconciliation(user=Depends(current_user)):
        tenant = _tenant_id(user)
        return {"ok": True, "reconciliation": await reconciliation_check(db, tenant)}

    # ── Iter-293 — Admin diagnostics (READ-ONLY, no Qoyod mutations) ──
    @router.get("/admin/diagnostics/build")
    async def admin_diagnostics_build(user=Depends(current_user)):  # noqa: ARG001
        """Iter-2026-02.rev24 — Prove which build the RUNNING worker
        process actually uses. Read-only. No DB write. No Qoyod POST.

        Returns marker presence (Rev16/17/20/21), module __file__ + sha,
        git sha (best-effort), worker task presence, env presence flags
        (booleans only — never leaks secret values).

        If `code_matches_expected == false` on production, the deployed
        build is stale: redeploy backend AND ensure the worker process
        restarts. Re-hit until true.
        """
        from integrations.qoyod.sas_build_diagnostics import (
            build_diagnostics_report,
        )
        return build_diagnostics_report()

    @router.get("/admin/diagnostics/row")
    async def admin_diagnostics_row(
        user=Depends(current_user),  # noqa: ARG001
        trace_id: str = Query(..., min_length=8, max_length=64),
    ):
        """Iter-2026-02.rev24 — Full read-only dump of a single
        integration_inbox row by trace_id. Includes
        `selective_auto_send_gate` persisted decision + derived
        diagnosis booleans.

        Read-only. Does NOT reprocess, retry, approve, or POST.
        """
        from integrations.qoyod.sas_build_diagnostics import row_diagnostics
        return await row_diagnostics(db, trace_id)

    @router.get("/admin/cod-receipts-report")
    async def admin_cod_receipts_report(
        user=Depends(current_user),
        from_date: Optional[str] = Query(None, alias="from"),
        to_date:   Optional[str] = Query(None, alias="to"),
        limit:     int = Query(500, ge=1, le=5000),
    ):
        """Iter-293 — Lists COD invoices that wrongly produced a Qoyod
        invoice_payment/receipt under the old pipeline (pre-Iter-293).

        Read-only. The accountant uses the output to manually
        delete/void the wrong payment in Qoyod — Mezan does NOT do
        any automated cleanup here (per user spec)."""
        from integrations.qoyod.cod_receipts_report import cod_receipts_report
        tenant = _tenant_id(user)
        return await cod_receipts_report(
            db, tenant,
            from_iso=from_date, to_iso=to_date, limit=limit,
        )

    @router.get("/admin/bank-transfer-discovery")
    async def admin_bank_transfer_discovery(
        user=Depends(current_user),
        limit: int = Query(10, ge=1, le=50),
    ):
        """Iter-293 companion (preparation for Iter-294) — Scans historical
        qoyod_payloads for orders paid via bank_transfer and returns the
        candidate JSON paths where Salla might encode the receiving
        bank. The user inspects this to confirm the authoritative field
        before Iter-294 implements per-bank routing.

        Read-only. No Qoyod calls. Sensitive fields (card/email/phone)
        are redacted in the response."""
        from integrations.qoyod.bank_transfer_discovery import scan_existing_payloads
        tenant = _tenant_id(user)
        return await scan_existing_payloads(db, user_id=tenant, limit=limit)

    # ── POST /webhook — Day 3 entry point (no JWT, token-protected) ──
    # Token check + idempotency + validation + normalization, nothing more.
    attach_webhook_routes(router, db)

    # ── POST /pipeline/process-normalized — Day 4 advancement ────────
    # Strictly stops at CUSTOMER_RESOLVED. Manual trigger only — the
    # background worker (Day 5) will call the same orchestrator.
    @router.post("/pipeline/process-normalized")
    async def process_normalized(
        user=Depends(current_user),
        limit: int = 25,
    ):
        tenant = _tenant_id(user)
        return await process_pending_normalized(db, tenant, limit=limit)

    # ── POST /pipeline/process-customer-resolved — Day 5 (4b→4c→4d) ─
    # Honours dry_run_mode + Pre-flight + Payload Snapshot + PARTIAL_FAILURE.
    @router.post("/pipeline/process-customer-resolved")
    async def process_customer_resolved(
        user=Depends(current_user),
        limit: int = 25,
    ):
        tenant = _tenant_id(user)
        return await process_pending_customer_resolved(db, tenant, limit=limit)

    # ── Background worker liveness & manual trigger (iter-262) ──────
    @router.get("/worker/status")
    async def worker_status(user=Depends(current_user)):
        from integrations.qoyod.worker import liveness
        return {"ok": True, "worker": liveness()}

    @router.post("/worker/run-now")
    async def worker_run_now(user=Depends(current_user)):
        """Manual emergency trigger — drains one round of the worker
        immediately. Used by the First-Sync-Monitor 'Advance Now' button.
        """
        from integrations.qoyod.worker import run_now
        result = await run_now(db, user_id=_tenant_id(user))
        return {"ok": True, "result": result}

    # ── GET /reports/day4 — Eligibility & resolution outcomes card ──
    @router.get("/reports/day4")
    async def reports_day4(user=Depends(current_user)):
        tenant = _tenant_id(user)
        return {"ok": True, "report": await day4_report(db, tenant)}

    # ── QYD-GO — Production Readiness ────────────────────────────────
    # Three endpoints, all read-only except `activate` which only flips
    # `dry_run_mode/enabled` once the checklist passes.
    @router.get("/go-live/checklist")
    async def go_live_checklist_endpoint(user=Depends(current_user)):
        tenant = _tenant_id(user)
        return {"ok": True, "checklist": await go_live_checklist(db, tenant)}

    @router.get("/go-live/report")
    async def go_live_report_endpoint(user=Depends(current_user)):
        tenant = _tenant_id(user)
        return {"ok": True, "report": await go_live_report(db, tenant)}

    @router.post("/go-live/activate")
    async def go_live_activate_endpoint(user=Depends(current_user)):
        tenant = _tenant_id(user)
        try:
            return await activate_production_mode(db, tenant)
        except ActivationBlocked as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "activation_blocked",
                        "reasons": exc.reasons,
                        "items":   exc.items})

    @router.post("/go-live/clear-test-failures")
    async def clear_test_failures(user=Depends(current_user)):
        """Marks all current DEAD_LETTER / PARTIAL_FAILURE rows as
        `excluded_from_checklist=True` so they no longer block the
        readiness check. The rows stay in the database for the First-Sync
        Monitor; only the QYD-GO check ignores them."""
        tenant = _tenant_id(user)
        result = await db.integration_inbox.update_many(
            {"user_id": tenant,
             "pipeline_stage": {"$in": ["DEAD_LETTER", "PARTIAL_FAILURE"]},
             "excluded_from_checklist": {"$ne": True}},
            {"$set": {"excluded_from_checklist": True,
                      "excluded_at": datetime.now(timezone.utc)}},
        )
        return {"ok": True, "excluded": result.modified_count}

    # ── Dead-Letter Auto-Requeue (KNOWN_FIXED_PATTERNS only) ────────
    # Rationale: when a Qoyod-side bug is patched in our code, rows
    # that previously DEAD-LETTERed against that bug should self-heal —
    # NOT block Go-Live forever. Strictly bounded by
    # `KNOWN_FIXED_PATTERNS`. Per-row attempts capped by
    # `MAX_REQUEUE_ATTEMPTS`. See dead_letter_requeue.py for the
    # registry + safety semantics.
    @router.get("/dead-letter/preview")
    async def dead_letter_preview(
        include_dry_run: bool = False,
        user=Depends(current_user),
    ):
        """Read-only preview of rows that would be auto-requeued.
        Returns the registry too so the operator UI can show which
        patterns are active."""
        tenant = _tenant_id(user)
        candidates = await find_requeue_candidates(
            db, user_id=tenant, include_dry_run=include_dry_run)
        return {
            "ok": True,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "max_requeue_attempts": MAX_REQUEUE_ATTEMPTS,
            "patterns": [
                {"id": p.get("id"),
                 "description": p.get("description"),
                 "applies_to_failed_stages":
                    sorted(list(p.get("applies_to_failed_stages") or [])),
                 "fixed_at": p.get("fixed_at")}
                for p in KNOWN_FIXED_PATTERNS
            ],
        }

    @router.post("/dead-letter/auto-requeue")
    async def dead_letter_auto_requeue(
        body: DeadLetterAutoRequeueBody | None = None,
        user=Depends(current_user),
    ):
        """Manually trigger one round of auto-requeue. Same logic the
        background worker runs every tick — bounded by
        KNOWN_FIXED_PATTERNS + MAX_REQUEUE_ATTEMPTS."""
        tenant = _tenant_id(user)
        include_dry = bool(body and body.include_dry_run)
        result = await auto_requeue_known_fixed(
            db, user_id=tenant, include_dry_run=include_dry,
            actor=f"operator:{getattr(user, 'email', tenant)}",
        )
        return {"ok": True, "result": result}

    @router.post("/dead-letter/requeue-one")
    async def dead_letter_requeue_one(
        body: DeadLetterRequeueOneBody,
        user=Depends(current_user),
    ):
        """Manually requeue ONE row (by row_id or trace_id). Still
        bounded by the pattern registry — generic DEAD_LETTER rows
        cannot be requeued via this endpoint."""
        if not (body.row_id or body.trace_id):
            raise HTTPException(
                status_code=400,
                detail={"code": "row_id_or_trace_id_required",
                        "message": "either row_id or trace_id is required"})
        tenant = _tenant_id(user)
        result = await requeue_one(
            db, user_id=tenant,
            row_id=body.row_id, trace_id=body.trace_id,
            actor=f"operator:{getattr(user, 'email', tenant)}",
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=409,
                detail={"code": result.get("reason", "requeue_refused"),
                        **{k: v for k, v in result.items()
                           if k not in {"ok", "reason"}}})
        return result

    # ── SSOT Trust Gate — Product Adoption ──────────────────────────
    # When the resolver refuses with `qoyod_existing_untrusted`, the
    # operator must EITHER archive the historical product in Qoyod OR
    # explicitly adopt it via this endpoint. Adoption inserts the row
    # into `qoyod_products_mapping` with `adopted=True` so subsequent
    # orders bind cleanly without re-triggering the gate.
    @router.post("/products/adopt")
    async def adopt_product(
        body: AdoptProductBody,
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        if not body.sku.strip() or not body.qoyod_product_id.strip():
            raise HTTPException(
                status_code=400,
                detail={"code": "sku_and_qoyod_product_id_required"})
        result = await adopt_qoyod_product(
            db, user_id=tenant, sku=body.sku.strip(),
            qoyod_product_id=body.qoyod_product_id.strip(),
            qoyod_product_name=body.qoyod_product_name,
            note=body.note,
            actor=f"operator:{getattr(user, 'email', tenant)}",
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=409,
                detail={"code": result.get("reason", "adopt_refused")})
        return result

    # ── Iter-293.5-rev4 — Manual Customer Adoption (Local-Only) ─────
    # Operator has already created/verified the buyer in Qoyod and
    # wants Mezan to bind their phone/email to that contact_id.
    # DOES NOT call Qoyod's API — only upserts the local mapping.
    # Sets `dry_run_only=False` so the preview / sendable gate
    # accepts this binding immediately. Fully audited via
    # `adopted_by` / `adopted_at` / `adoption_note`.
    @router.post("/customers/adopt")
    async def adopt_customer(
        body: AdoptCustomerBody,
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        if not body.lookup_key.strip() or not body.qoyod_contact_id.strip():
            raise HTTPException(
                status_code=400,
                detail={"code":
                        "lookup_key_and_qoyod_contact_id_required"})
        result = await adopt_qoyod_customer(
            db,
            user_id=tenant,
            lookup_key=body.lookup_key.strip(),
            lookup_kind=body.lookup_kind,
            qoyod_contact_id=body.qoyod_contact_id.strip(),
            qoyod_contact_name=body.qoyod_contact_name,
            note=body.note,
            actor=f"operator:{getattr(user, 'email', tenant)}",
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=409,
                detail={"code": result.get("reason", "adopt_refused")})
        return result

    # ── Iter-293.4-rev3 — Per-Order Approval audit ──────────────────
    # Read-only list of every per-order approval granted via the
    # one-shot-reprocess endpoint. For ZATCA evidence trail.
    @router.get("/admin/per-order-approvals")
    async def list_per_order_approvals(
        user=Depends(current_user),
        limit: int = Query(100, ge=1, le=500),
        order_number: Optional[str] = Query(None),
    ):
        tenant = _tenant_id(user)
        q: dict = {"user_id": tenant}
        if order_number:
            q["order_number"] = order_number
        rows = []
        async for r in db.qoyod_per_order_approvals.find(
                q, {"_id": 0}).sort("approved_at", -1).limit(limit):
            if hasattr(r.get("approved_at"), "isoformat"):
                r["approved_at"] = r["approved_at"].isoformat()
            rows.append(r)
        return {
            "ok":    True,
            "count": len(rows),
            "items": rows,
            "note":  ("سجل القراءة فقط لكل موافقات per-order التي مُنحت "
                      "عبر one-shot-reprocess. كل موافقة مرتبطة بطلب "
                      "واحد ولا يمكن استخدامها لطلب آخر."),
        }

    # ── Iter-293.4-rev3 — DRY/PREVIEW mappings audit ────────────────
    # Read-only list of every product SKU whose `qoyod_product_id`
    # carries a DRY:/PREVIEW:* prefix OR is flagged `dry_run_only=True`.
    # Surfaces exactly what the operator needs to repair via
    # `POST /products/adopt` before any live invoice can be sent for
    # an order containing those SKUs.
    @router.get("/admin/products/dry-mappings")
    async def list_dry_product_mappings(
        user=Depends(current_user),
        limit: int = Query(200, ge=1, le=2000),
    ):
        tenant = _tenant_id(user)
        # Match: dry_run_only=True OR qoyod_product_id starts with
        # DRY:/PREVIEW: (case-insensitive).
        q = {
            "user_id": tenant,
            "$or": [
                {"dry_run_only": True},
                {"qoyod_product_id": {
                    "$regex": r"^(DRY:|PREVIEW:)", "$options": "i"}},
            ],
        }
        rows = []
        async for r in db.qoyod_products_mapping.find(
                q, {"_id": 0}).limit(limit):
            rows.append({
                "sku":                r.get("sku"),
                "qoyod_product_id":   r.get("qoyod_product_id"),
                "qoyod_product_name": r.get("qoyod_product_name"),
                "dry_run_only":       bool(r.get("dry_run_only", False)),
                "adopted":            bool(r.get("adopted", False)),
                "source":             r.get("source"),
                "created_at":         (r.get("created_at").isoformat()
                                       if hasattr(r.get("created_at"),
                                                  "isoformat") else None),
                "needs_repair_via":   "POST /products/adopt",
                "reason": (
                    "dry_run_only=true"
                    if r.get("dry_run_only")
                    else "qoyod_product_id has DRY:/PREVIEW: prefix"
                ),
            })
        return {
            "ok":      True,
            "count":   len(rows),
            "items":   rows,
            "note":    (
                "كل SKU في هذه القائمة يحتاج adoption يدوي بـ qoyod_product_id "
                "حقيقي. استخدم `POST /products/adopt` لكل SKU. هذا يُحدِّث "
                "الـ mapping ويُفعِّل dependency_status.sendable في الـ preview."),
        }

    # ── Existing-Data Migration (read-only pre-flight) ──────────────
    attach_migration_routes(router, db, current_user, _tenant_id)

    # ── Fresh-Start Audit (READ-ONLY) ───────────────────────────────
    # User spec 2026-06-27: a forensic snapshot of what Qoyod contains
    # before Mezan becomes the sole source. NO DELETE/PUT logic here.
    # Scope is hard-locked to invoices / receipts / products / customers.
    @router.post("/fresh-start/audit/run")
    async def fresh_start_audit_run(user=Depends(current_user)):
        tenant = _tenant_id(user)
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(400, "no_credentials")
        result = await run_fresh_start_audit(
            db, user_id=tenant,
            api_client=await _build_qoyod_client_for(db, tenant, key))
        return result

    @router.get("/fresh-start/audit")
    async def fresh_start_audit_status(user=Depends(current_user)):
        tenant = _tenant_id(user)
        doc = await latest_audit(db, user_id=tenant)
        return {"ok": True, "audit": doc}

    # ── Fresh-Start Cleanup — Plan + Execute (DELETE-CONFIRM gated) ─
    @router.post("/fresh-start/plan/build")
    async def fresh_start_plan_build(user=Depends(current_user)):
        tenant = _tenant_id(user)
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(400, "no_credentials")
        try:
            plan = await build_plan(
                db, user_id=tenant,
                api_client=await _build_qoyod_client_for(db, tenant, key))
        except QoyodAPIError as exc:
            return {"ok": False, "error": exc.to_log_dict()}
        return {"ok": True, "plan": plan,
                "expected_confirm_token": EXPECTED_CONFIRM_TOKEN,
                "protected_entities": PROTECTED_ENTITIES}

    @router.get("/fresh-start/plan/latest")
    async def fresh_start_plan_latest(user=Depends(current_user)):
        tenant = _tenant_id(user)
        doc = await latest_plan(db, user_id=tenant)
        return {"ok": True, "plan": doc,
                "expected_confirm_token": EXPECTED_CONFIRM_TOKEN,
                "protected_entities": PROTECTED_ENTITIES}

    @router.post("/fresh-start/execute")
    async def fresh_start_execute(
        payload: FreshStartExecutePayload, user=Depends(current_user)):
        tenant = _tenant_id(user)
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(400, "no_credentials")
        try:
            result = await execute_cleanup(
                db, user_id=tenant,
                job_id=payload.job_id,
                confirm_token=payload.confirm,
                api_client=await _build_qoyod_client_for(db, tenant, key))
        except CleanupRefused as exc:
            raise HTTPException(400, str(exc))
        return result

    # ── First-Sync Monitor (READ-ONLY operational view) ─────────────
    @router.get("/first-sync-monitor")
    async def first_sync_monitor_list(
        limit: int = 5, user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        rows = await list_recent_for_monitor(
            db, user_id=tenant, limit=limit)
        return {"ok": True, "rows": rows, "count": len(rows)}

    # Aggregate counters used by the sidebar alert dot + monitor page
    # status badges. Polled every few seconds by the UI.
    @router.get("/first-sync-monitor/stats/summary")
    async def first_sync_monitor_stats(user=Depends(current_user)):
        tenant = _tenant_id(user)
        stats = await get_monitor_stats(db, user_id=tenant)
        return {"ok": True, "stats": stats}

    # Archive (move + delete) DEAD_LETTER + PARTIAL_FAILURE dry-run
    # rows ONLY. Never touches COMPLETED rows or any production row.
    # The archive collection (`integration_inbox_archive`) is kept
    # forever so the operation is fully recoverable.
    @router.post("/first-sync-monitor/archive-failed-tests")
    async def first_sync_monitor_archive_failed_tests(
        payload: ArchiveFailedTestsBody,
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        try:
            result = await archive_failed_dry_run_tests(
                db, user_id=tenant,
                confirm_token=payload.confirm,
                actor=getattr(user, "email", None) or tenant,
            )
        except ArchiveRefused as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "confirm_required",
                        "message": str(exc),
                        "expected_token": ARCHIVE_CONFIRM_TOKEN})
        return {"ok": True, **result}

    # ── Duplicate-attempt detection (Iter-280) ─────────────────────
    # Lists inbox rows where the SAME logical webhook delivery
    # (order_number + event + status_slug) created >1 row. Surfaces
    # which trace each operator-action would touch.
    @router.get("/first-sync-monitor/duplicate-groups")
    async def first_sync_monitor_duplicate_groups(
        only_failed: bool = Query(
            True,
            description="When true, only groups containing ≥1 failed "
                        "terminal row are returned"),
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        groups = await find_duplicate_groups(
            db, user_id=tenant, only_failed=only_failed)
        return {
            "ok":              True,
            "groups":          groups,
            "expected_token":  DUPLICATE_CONFIRM_TOKEN,
        }

    # Archive every duplicate attempt in a group EXCEPT keep_trace_id.
    # NEVER touches Qoyod; archive collection is recoverable.
    @router.post("/first-sync-monitor/archive-duplicates")
    async def first_sync_monitor_archive_duplicates(
        payload: ArchiveDuplicateAttemptsBody,
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        try:
            result = await archive_duplicate_attempts(
                db, user_id=tenant,
                order_number=payload.order_number,
                event=payload.event,
                status_slug=payload.status_slug,
                keep_trace_id=payload.keep_trace_id,
                confirm_token=payload.confirm,
                actor=getattr(user, "email", None) or tenant,
            )
        except DuplicateMergeRefused as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "merge_refused",
                        "message": str(exc),
                        "expected_token": DUPLICATE_CONFIRM_TOKEN})
        return {"ok": True, **result}

    @router.get("/first-sync-monitor/{trace_id}")
    async def first_sync_monitor_one(
        trace_id: str, user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        row = await get_row_for_monitor(
            db, user_id=tenant, trace_id=trace_id)
        if not row:
            raise HTTPException(404, "trace_id_not_found")
        return {"ok": True, "row": row}

    # ── One-Shot Reprocess (single order, strict, audit-trail-heavy) ─
    # Targets EXACTLY one Salla order against the real Qoyod tenant.
    # Used to recover production rows that DEAD_LETTERed against a
    # since-fixed bug (e.g. DRY:product leak, Iter-267). NEVER scans
    # other DEAD_LETTER rows. NEVER triggers backfill.
    #
    # Required body:
    #   { "order_number": "268670571",
    #     "confirm":      "REPROCESS-268670571",
    #     "trace_id":     "<optional disambiguator>" }
    #
    # Response is uniformly shaped (see `one_shot_reprocess`) — the
    # UI renders the outcome directly without inspecting HTTP codes.
    @router.post("/admin/one-shot-reprocess")
    async def admin_one_shot_reprocess(
        payload: OneShotReprocessBody, user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        actor = getattr(user, "email", None) or tenant
        try:
            result = await reprocess_one_order(
                db, user_id=tenant,
                order_number=payload.order_number,
                trace_id=payload.trace_id,
                confirm=payload.confirm,
                approval_phrase=payload.approval_phrase,
                actor=actor,
            )
        except OneShotRefused as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    **exc.to_dict(),
                    "expected_confirm_token": CONFIRM_TOKEN_TEMPLATE.format(
                        order_number=payload.order_number),
                },
            )
        except HTTPException:
            raise
        except Exception as exc:
            # Surface the real error so the operator (and we) can
            # diagnose without diving into server logs. The traceback
            # tail is truncated to 1.5 KB to keep responses small.
            import traceback as _tb
            tb_tail = "".join(_tb.format_exception(exc))[-1500:]
            logger.exception(
                "qoyod one-shot reprocess UNHANDLED for order_number=%s "
                "trace_id=%s tenant=%s",
                payload.order_number, payload.trace_id, tenant,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code":    "one_shot_unhandled_exception",
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback_tail": tb_tail,
                    "order_number": payload.order_number,
                    "trace_id":     payload.trace_id,
                },
            )
        return result

    # ── Preview Reprocess (SAFE — no Qoyod calls) ──────────────────
    # Re-runs the WHOLE pipeline in memory: adapter → normalizer →
    # totals_guard → business rules → customer/product/invoice/receipt
    # payload builders → preflight. Returns the structured diagnostics
    # for ONE row WITHOUT any network call to Qoyod.
    #
    # No confirm token (no side-effects). Idempotency check still runs:
    # if a real (non-DRY) Qoyod invoice already exists for the order,
    # the response surfaces `invoice_already_created` with the existing
    # qoyod_invoice_id so the operator never builds a duplicate payload.
    @router.post("/admin/preview-reprocess")
    async def admin_preview_reprocess(
        payload: PreviewReprocessBody, user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        try:
            result = await preview_reprocess_one_order(
                db, user_id=tenant,
                order_number=payload.order_number,
                trace_id=payload.trace_id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            import traceback as _tb
            tb_tail = "".join(_tb.format_exception(exc))[-1500:]
            logger.exception(
                "qoyod preview-reprocess UNHANDLED order=%s trace=%s "
                "tenant=%s", payload.order_number, payload.trace_id, tenant)
            # Even unhandled exceptions return a structured JSON 200
            # so the UI can render the failure without inspecting HTTP
            # codes. `ok=false` + `failed_at_stage` keeps the contract
            # consistent with the success path.
            return {
                "ok":              False,
                "mode":            "preview",
                "qoyod_request_sent": False,
                "failed_at_stage": "unhandled_exception",
                "error_code":      "preview_unhandled_exception",
                "message":         f"{type(exc).__name__}: {exc}",
                "traceback_tail":  tb_tail,
                "order_number":    payload.order_number,
                "trace_id":        payload.trace_id,
            }
        return result

    # ── Order Recovery Diagnostics (GET — read-only, no Qoyod calls) ──
    # Iter-293.4-rev7 (2026-XX) — Surfaces ALL DB-side facts for a
    # specific order so the operator can diagnose a row stuck at
    # INVOICE_CREATED (e.g. production order 269571122) WITHOUT
    # invoking any side-effect path. NO confirm token, NO approval
    # phrase, NO writes — defensive against accidental re-runs.
    #
    # Returns:
    #   • inbox row snapshot (stage, qoyod ids, payloads, responses,
    #     stage_history, customer/product resolution).
    #   • qoyod_invoices ledger row.
    #   • per_order_approvals audit row (when present).
    #   • totals_comparison block (Salla vs Mezan-expected vs قيود-actual).
    #   • posting_mode the order resolves to.
    @router.get("/admin/order-recovery-diagnostics")
    async def admin_order_recovery_diagnostics(
        order_number: Optional[str] = None,
        trace_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        if not (order_number or trace_id):
            raise HTTPException(
                status_code=400,
                detail={"code": "missing_lookup",
                        "message": "supply order_number or trace_id"})
        tenant = _tenant_id(user)
        # Locate the inbox row(s).
        q: dict = {"user_id": tenant}
        if trace_id:
            q["trace_id"] = trace_id
        else:
            on = str(order_number)
            cands: list = [on]
            try:
                cands.append(int(on))
            except (TypeError, ValueError):
                pass
            q["$or"] = []
            for v in cands:
                q["$or"].extend([
                    {"salla_order_number": v},
                    {"salla_order_id":     v},
                    {"canonical_payload.order_number": v},
                    {"canonical_payload.order_id":     v},
                ])
        rows = await db.integration_inbox.find(
            q, {"_id": 0}).to_list(length=20)
        if not rows:
            return {
                "ok":           False,
                "code":         "row_not_found",
                "order_number": order_number,
                "trace_id":     trace_id,
            }
        if len(rows) > 1 and not trace_id:
            return {
                "ok":   False,
                "code": "multiple_matches_supply_trace_id",
                "order_number": order_number,
                "candidates": [{
                    "trace_id":       r.get("trace_id"),
                    "received_at":    r.get("received_at"),
                    "pipeline_stage": r.get("pipeline_stage"),
                } for r in rows[:10]],
            }
        row = rows[0]
        payloads = row.get("qoyod_payloads") or {}
        responses = row.get("qoyod_responses") or {}
        inv_resp_obj = (responses.get("invoice") or {})
        inv_resp_body = inv_resp_obj.get("body")
        canonical = row.get("canonical_payload") or {}

        # Pull the qoyod_invoices ledger entry.
        ledger = await db.qoyod_invoices.find_one(
            {"user_id":        tenant,
             "salla_order_id": canonical.get("order_id")
                               or str(row.get("salla_order_number") or "")},
            {"_id": 0},
        )

        # Pull the per-order approval audit (any approvals for this
        # trace — may be zero or one row).
        approval = await db.qoyod_per_order_approvals.find_one(
            {"user_id":     tenant,
             "trace_id":    row.get("trace_id")},
            {"_id": 0},
        )
        if isinstance(approval, dict):
            approved_at = approval.get("approved_at")
            if hasattr(approved_at, "isoformat"):
                approval["approved_at"] = approved_at.isoformat()

        # Posting-mode resolution (read-only — never modifies state).
        settings_doc = await db.qoyod_settings.find_one(
            {"user_id": tenant}, {"_id": 0}) or {}
        try:
            from integrations.qoyod.payment_methods import (
                resolve_posting_mode)
            posting_mode = resolve_posting_mode(
                settings_doc,
                (canonical.get("payment_method")
                 or canonical.get("payment_method_native")))
        except Exception:    # pragma: no cover
            posting_mode = None

        # Totals comparison — Salla vs Mezan-expected vs قيود-actual.
        inv_diag = payloads.get("invoice_diagnostics") or {}
        salla_total = canonical.get("total_amount")
        mezan_expected_total = inv_diag.get("mezan_expected_total")
        qoyod_actual_total = None
        for src in (inv_resp_body, inv_resp_body.get("invoice")
                    if isinstance(inv_resp_body, dict) else None):
            if not isinstance(src, dict):
                continue
            for k in ("total", "total_amount", "balance", "grand_total"):
                if src.get(k) is not None:
                    try:
                        qoyod_actual_total = float(src[k])
                    except (TypeError, ValueError):
                        qoyod_actual_total = None
                    if qoyod_actual_total is not None:
                        break
            if qoyod_actual_total is not None:
                break
        try:
            difference = (
                None if (qoyod_actual_total is None or salla_total is None)
                else round(float(qoyod_actual_total) - float(salla_total), 4)
            )
        except (TypeError, ValueError):
            difference = None

        return {
            "ok":               True,
            "qoyod_request_sent": False,
            "mode":             "diagnostic_readonly",
            "row": {
                "id":                  row.get("id"),
                "trace_id":            row.get("trace_id"),
                "salla_order_number":  row.get("salla_order_number"),
                "salla_order_id":      row.get("salla_order_id"),
                "pipeline_stage":      row.get("pipeline_stage"),
                "qoyod_invoice_id":    row.get("qoyod_invoice_id"),
                "qoyod_invoice_number": row.get("qoyod_invoice_number"),
                "qoyod_customer_id":   row.get("qoyod_customer_id"),
                "qoyod_invoice_payment_id":
                    row.get("qoyod_invoice_payment_id"),
                "qoyod_receipt_id":    row.get("qoyod_receipt_id"),
                "received_at":         row.get("received_at"),
                "last_failed_stage":   row.get("last_failed_stage"),
                "pipeline_error":      row.get("pipeline_error"),
                "lock_reason":         row.get("lock_reason"),
                "lock_step":           row.get("lock_step"),
                "lock_attempt_id":     row.get("lock_attempt_id"),
            },
            "qoyod_invoices_ledger": ledger,
            "per_order_approval":   approval,
            "posting_mode":         posting_mode,
            "invoice_request_body":  payloads.get("invoice"),
            "invoice_response_body": inv_resp_body,
            "invoice_diagnostics":   inv_diag,
            "invoice_payment_payload": payloads.get("invoice_payment"),
            "stage_history":        row.get("stage_history") or [],
            "totals_comparison": {
                "salla_total":              salla_total,
                "mezan_expected_total":     mezan_expected_total,
                "qoyod_actual_total":       qoyod_actual_total,
                "difference":               difference,
                "mismatch":                 (difference is not None
                                             and abs(difference) > 0.005),
                "tolerance_sar":            0.005,
            },
        }

    # ── Pending Orders (GET — read-only, categorised view) ──────────
    # Iter-293.5 (updated 2026-07-01 per user directive):
    #
    # The page surfaces EVERY inbox row that satisfies EITHER:
    #   (a) pipeline_stage is one of the well-known HOLD/failure
    #       stages listed in _PENDING_STAGES; OR
    #   (b) salla_order_status_native indicates the order SHOULD have
    #       an invoice (completed / delivered / shipped / in_progress
    #       / تم التنفيذ / جاري التوصيل / تم التوصيل / تم الشحن) AND
    #       the row does NOT yet carry a real qoyod_invoice_id.
    #
    # For each order (identified by salla_order_id/number) only the
    # LATEST trace is shown — older traces are discarded so a
    # canceled-then-completed order surfaces as `completed`, not stale.
    _PENDING_STAGES: tuple[str, ...] = (
        "LOCKED_AWAITING_APPROVAL",
        "INVOICE_CREATED_TOTAL_MISMATCH",
        "BANK_TRANSFER_PAYMENT_ROUTING_PENDING",
        "HOLD_UNSUPPORTED_PAYMENT_METHOD",
        "HOLD_COD_PENDING_FIX",
        "UNRESOLVED_QOYOD_DEPENDENCY",
        "STALE_TRACE_NOT_CURRENT_ORDER_STATE",
        "FAILED_INVOICE",
        "DEAD_LETTER",
    )
    # Salla statuses that make an order billable (should appear in
    # the queue when there is no قيود invoice yet). Kept aligned with
    # `eligible_statuses.ELIGIBLE_ORDER_STATUSES` (Iter-293.5-rev3).
    from integrations.qoyod.eligible_statuses import (
        ELIGIBLE_ORDER_STATUSES,
    )
    _ELIGIBLE_STATUSES_FOR_QUEUE: frozenset[str] = ELIGIBLE_ORDER_STATUSES
    _CATEGORY_ORDER: tuple[str, ...] = (
        "ready_to_send",
        "needs_mapping",
        "bank_transfer_hold",
        "cod",
        "unsupported_method",
        "total_rounding_review",
        "stale_or_cancelled",
    )

    def _stage_to_category(stage: str) -> str:
        from integrations.qoyod.pending_classifier import stage_to_category
        return stage_to_category(stage)

    def _categorise_row(row: dict) -> str:
        """Delegates to `pending_classifier.categorise_row` — pure
        module-level helper so tests exercise the exact same logic
        as the endpoint. See `pending_classifier.py`."""
        from integrations.qoyod.pending_classifier import categorise_row
        return categorise_row(row)

    def _payload_has_leak(payload) -> bool:
        from integrations.qoyod.pending_classifier import payload_has_leak
        return payload_has_leak(payload)

    # ── Iter-001 (Eligible Orders Read-Only Audit) ──────────────────
    # Surfaces every Salla order in `unified_orders` whose status is
    # billable but hasn't reached قيود yet. Complements pending-orders
    # (which reads only from `integration_inbox`) by ALSO detecting
    # orders that never entered the pipeline at all
    # (missing_from_pipeline). See `eligible_orders.py`.
    #
    # Read-Only Contract: NO writes, NO Qoyod API calls, NO approve.
    @router.get("/admin/eligible-orders")
    async def admin_eligible_orders(
        since_days: int = 90,
        limit: int = 200,
        show_already_sent: bool = False,
        debug: bool = False,
        user=Depends(current_user),
    ):
        from integrations.qoyod.eligible_orders import (
            build_eligible_orders_report,
        )
        return await build_eligible_orders_report(
            db,
            user_id=_tenant_id(user),
            since_days=since_days,
            limit=limit,
            show_already_sent=show_already_sent,
            debug=debug,
        )

    # ── Phase C.0 (2026-07-01) — Selective Send Policy Report ─
    # Read-Only diagnostic that runs the Selective Live Send policy
    # against every order the tenant has and returns one decision
    # (`allow`/`block` + blocker_code + reason) per row. This endpoint
    # NEVER opens the write lock, NEVER calls Qoyod, NEVER writes to
    # the DB. It exists so operators can preview what the gate would
    # do BEFORE flipping `selective_live_send_enabled` to true.
    @router.get("/admin/selective-send-policy-report")
    async def admin_selective_send_policy_report(
        since_days: int = 90,
        limit: int = 200,
        user=Depends(current_user),
    ):
        from integrations.qoyod.selective_send_policy import (
            build_selective_send_policy_report,
        )
        return await build_selective_send_policy_report(
            db,
            user_id=_tenant_id(user),
            since_days=since_days,
            limit=limit,
        )

    # ── Iter-001k+ (2026-02-XX) — Order Totals Breakdown ──────
    # Read-Only diagnostic: when `_check_totals` reports a totals
    # mismatch (diff > 0.01), this endpoint returns the FULL numeric
    # breakdown for one order so the operator can identify which
    # adjustment (coupon / promotion / wallet / etc.) is missing
    # from the current reconstruction formula.
    #
    # STRICT read-only contract:
    #   • Zero Qoyod API calls.
    #   • Zero DB writes.
    #   • No policy change / gate flip / send attempt.
    #   • Raw payload debug ONLY when `include_raw_debug=true`.
    @router.get("/admin/order-totals-breakdown/{order_number}")
    async def admin_order_totals_breakdown(
        order_number: str,
        include_raw_debug: bool = False,
        user=Depends(current_user),
    ):
        from integrations.qoyod.order_totals_breakdown import (
            fetch_order_totals_breakdown,
        )
        return await fetch_order_totals_breakdown(
            db,
            user_id=_tenant_id(user),
            order_number=str(order_number),
            include_raw_debug=bool(include_raw_debug),
        )

    # ── Iter-001k+ (2026-02-XX) — Mezan-VAT Qoyod Simulation ───
    # Read-Only diagnostic answering ONE question:
    #     "If we build Qoyod invoice using Mezan's fixed 15% VAT
    #      and Salla's gross customer total as the only anchor,
    #      does the simulated Qoyod gross equal Salla's official
    #      total within 0.01 SAR?"
    #
    # Salla's own tax fields are IGNORED. This endpoint never
    # sends, never writes, never opens the gate.
    # ── Iter-001k+ (2026-02-XX) — Admin DRY RCA endpoint ──────
    # Read-Only mirror of `scripts/dry_rca_five_orders.py` for
    # operators without shell access. Refuses to execute unless
    # gates are Fail-Closed.
    @router.get("/admin/dry-rca-report")
    async def admin_dry_rca_report(
        orders: str,
        user=Depends(current_user),
    ):
        from integrations.qoyod.dry_rca_report import (
            build_dry_rca_report, GatesNotFailClosedError,
        )
        parsed = [o.strip() for o in (orders or "").split(",")
                  if o.strip()]
        if not parsed:
            raise HTTPException(
                status_code=400,
                detail=("`orders` query param is required, e.g. "
                        "?orders=269629400,269632660"))
        if len(parsed) > 50:
            raise HTTPException(
                status_code=400,
                detail="Max 50 orders per request.")
        try:
            return await build_dry_rca_report(
                db,
                user_id=_tenant_id(user),
                order_numbers=parsed,
            )
        except GatesNotFailClosedError as e:
            raise HTTPException(status_code=409, detail=str(e))

    # ── Iter-001k+ (2026-02-27) — Canary Readiness Preview ────
    # Read-Only preflight for a SINGLE order. Refuses if gates
    # are not Fail-Closed. Never sends. Never writes.
    # ── Iter-001k+ (2026-02-27) — Canary Live Send ────────────
    # Single-order live send with 14 hardcoded guards. Refuses
    # every order except 269629400. Never mutates gate settings.
    @router.post("/admin/canary-live-send")
    async def admin_canary_live_send(
        payload: dict,
        user=Depends(current_user),
    ):
        from integrations.qoyod.canary_live_send import (
            execute_canary_live_send,
        )
        order_number    = str(payload.get("order_number") or "")
        approval_phrase = payload.get("approval_phrase") or ""
        return await execute_canary_live_send(
            db,
            order_number=order_number,
            approval_phrase=approval_phrase,
            actor=str(getattr(user, "email",
                              getattr(user, "id", "operator"))),
            user_id=_tenant_id(user),
        )

    @router.get("/admin/canary-readiness-preview/{order_number}")
    async def admin_canary_readiness_preview(
        order_number: str,
        user=Depends(current_user),
    ):
        from integrations.qoyod.canary_readiness import (
            build_canary_readiness_preview,
        )
        from integrations.qoyod.dry_rca_report import (
            GatesNotFailClosedError,
        )
        try:
            return await build_canary_readiness_preview(
                db,
                user_id=_tenant_id(user),
                order_number=str(order_number),
            )
        except GatesNotFailClosedError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.get("/admin/qoyod-mezan-vat-simulation/{order_number}")
    async def admin_qoyod_mezan_vat_simulation(
        order_number: str,
        user=Depends(current_user),
    ):
        from integrations.qoyod.qoyod_simulation import (
            fetch_qoyod_simulation,
        )
        return await fetch_qoyod_simulation(
            db,
            user_id=_tenant_id(user),
            order_number=str(order_number),
        )

    @router.get("/admin/qoyod/pending-orders")
    async def admin_qoyod_pending_orders(
        limit: int = 200,
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        limit = max(1, min(int(limit), 500))
        # Two overlapping queries — well-known HOLD stages, PLUS any
        # inbox row whose Salla status is billable and has no real
        # قيود invoice yet. Overlap is deduplicated below.
        stage_query = {
            "user_id": tenant,
            "pipeline_stage": {"$in": list(_PENDING_STAGES)},
        }
        status_query = {
            "user_id": tenant,
            "$or": [
                {"canonical_payload.order_status_native":
                    {"$in": list(_ELIGIBLE_STATUSES_FOR_QUEUE)}},
                {"canonical_payload.order_status":
                    {"$in": list(_ELIGIBLE_STATUSES_FOR_QUEUE)}},
            ],
            # No REAL qoyod_invoice_id yet. DRY:/PREVIEW: values are
            # allowed here (they represent unresolved mappings, not
            # a shipped invoice).
            "$and": [{
                "$or": [
                    {"qoyod_invoice_id": None},
                    {"qoyod_invoice_id": ""},
                    {"qoyod_invoice_id": {"$regex": "^(DRY:|PREVIEW:)"}},
                ]}],
            "pipeline_stage": {"$nin": ["COMPLETED",
                                        "COMPLETED_WITH_ROUNDING_WARNING"]},
        }
        # Fetch, cap per-query at limit; union afterwards.
        rows_a = await db.integration_inbox.find(
            stage_query, {"_id": 0}).sort(
            [("received_at", -1)]).to_list(length=limit)
        rows_b = await db.integration_inbox.find(
            status_query, {"_id": 0}).sort(
            [("received_at", -1)]).to_list(length=limit)
        # Deduplicate: keep only the LATEST trace per
        # (salla_order_id / salla_order_number) — a canceled event
        # arriving after a completed one should hide the earlier row.
        by_order: dict[str, dict] = {}
        for row in rows_a + rows_b:
            canonical = row.get("canonical_payload") or {}
            key = str(
                canonical.get("order_id")
                or row.get("salla_order_id")
                or row.get("salla_order_number")
                or row.get("id"))
            existing = by_order.get(key)
            if existing is None:
                by_order[key] = row
                continue
            # Later received_at wins.
            a = row.get("received_at") or ""
            b = existing.get("received_at") or ""
            if str(a) > str(b):
                by_order[key] = row
        raw_rows = sorted(
            by_order.values(),
            key=lambda r: str(r.get("received_at") or ""),
            reverse=True)[:limit]

        # Load settings for flag + lock display.
        settings_doc = await db.qoyod_settings.find_one(
            {"user_id": tenant}, {"_id": 0}) or {}
        flag_enabled = bool(
            settings_doc.get("selective_live_send_enabled", False))
        lock_on_by_setting = settings_doc.get(
            "production_writes_locked", None)
        # Fail-closed default: absence of the flag means locked.
        lock_effective = (lock_on_by_setting is None
                          or bool(lock_on_by_setting))
        lock_source = (
            "explicit_setting"
            if lock_on_by_setting is not None
            else "fail_closed_default")

        # Iter-293.5 fix — Cross-trace / cross-collection existing
        # invoice detection.
        #
        # A single Salla order_number can produce MULTIPLE inbox
        # traces (SKIPPED → COMPLETED → SKIPPED — normal flow). If
        # any trace for the same order has already produced a real
        # قيود invoice, no OTHER trace of that order may be surfaced
        # as ready_to_send. Two lookups feed this map:
        #
        #   1. integration_inbox rows for the same order carrying a
        #      real qoyod_invoice_id (any pipeline_stage).
        #   2. qoyod_invoices ledger rows (canonical source-of-truth
        #      for what actually exists in قيود).
        order_keys: set[str] = set()
        for r in raw_rows:
            can = r.get("canonical_payload") or {}
            for v in (
                r.get("salla_order_number"),
                r.get("salla_order_id"),
                can.get("order_number"),
                can.get("order_id"),
            ):
                if v not in (None, ""):
                    order_keys.add(str(v))

        order_has_invoice: dict[str, dict] = {}
        if order_keys:
            or_conditions = []
            for k in order_keys:
                or_conditions.extend([
                    {"salla_order_number": k},
                    {"salla_order_id":     k},
                    {"canonical_payload.order_number": k},
                    {"canonical_payload.order_id":     k},
                ])
            # 1. Sibling inbox traces.
            sibling_cursor = db.integration_inbox.find({
                "user_id": tenant,
                "$or": or_conditions,
                "qoyod_invoice_id": {
                    "$exists": True,
                    "$nin": [None, ""],
                    "$not": {"$regex": "^(DRY:|PREVIEW:)"},
                },
            }, {"salla_order_number": 1, "salla_order_id": 1,
                "canonical_payload.order_number": 1,
                "canonical_payload.order_id":     1,
                "qoyod_invoice_id": 1, "qoyod_invoice_number": 1,
                "trace_id": 1, "pipeline_stage": 1, "_id": 0})
            async for sibling in sibling_cursor:
                scan = sibling.get("canonical_payload") or {}
                for k in (sibling.get("salla_order_number"),
                          sibling.get("salla_order_id"),
                          scan.get("order_number"),
                          scan.get("order_id")):
                    if k in (None, ""):
                        continue
                    order_has_invoice.setdefault(str(k), {
                        "source":               "inbox_sibling_trace",
                        "qoyod_invoice_id":     sibling.get(
                                                    "qoyod_invoice_id"),
                        "qoyod_invoice_number": sibling.get(
                                                    "qoyod_invoice_number"),
                        "trace_id":             sibling.get("trace_id"),
                        "pipeline_stage":       sibling.get(
                                                    "pipeline_stage"),
                    })
            # 2. qoyod_invoices ledger (source of truth).
            ledger_cursor = db.qoyod_invoices.find({
                "user_id": tenant,
                "$or": [
                    {"salla_order_id":     {"$in": list(order_keys)}},
                    {"salla_order_number": {"$in": list(order_keys)}},
                ],
                "qoyod_invoice_id": {"$exists": True, "$nin": [None, ""]},
            }, {"salla_order_id": 1, "salla_order_number": 1,
                "qoyod_invoice_id": 1, "qoyod_invoice_number": 1,
                "status": 1, "_id": 0})
            async for led in ledger_cursor:
                for k in (led.get("salla_order_id"),
                          led.get("salla_order_number")):
                    if k in (None, ""):
                        continue
                    # Ledger wins if not already set — but respect
                    # inbox_sibling_trace when it's already there
                    # (both are valid signals).
                    order_has_invoice.setdefault(str(k), {
                        "source":               "qoyod_invoices_ledger",
                        "qoyod_invoice_id":     led.get(
                                                    "qoyod_invoice_id"),
                        "qoyod_invoice_number": led.get(
                                                    "qoyod_invoice_number"),
                        "status":               led.get("status"),
                    })

        def _order_key_for(row: dict) -> Optional[str]:
            can = row.get("canonical_payload") or {}
            for v in (row.get("salla_order_number"),
                      row.get("salla_order_id"),
                      can.get("order_number"),
                      can.get("order_id")):
                if v not in (None, "") and str(v) in order_has_invoice:
                    return str(v)
            return None

        buckets: dict[str, list[dict]] = {c: [] for c in _CATEGORY_ORDER}
        for row in raw_rows:
            category = _categorise_row(row)
            canonical = row.get("canonical_payload") or {}
            payloads = row.get("qoyod_payloads") or {}
            totals = row.get("totals_comparison") or {}
            existing_qid = row.get("qoyod_invoice_id") or ""
            order_status_native = (
                canonical.get("order_status_native")
                or canonical.get("order_status"))
            # Iter-293.5 fix — if ANY trace / ledger row shows a real
            # invoice for this order_number, downgrade the row: it is
            # NOT a candidate. Push it to total_rounding_review (where
            # the accountant can decide what to do with the sibling
            # trace) OR hide it if we already have a fully-completed
            # sibling. We keep it visible under total_rounding_review
            # so operators can see the whole family.
            sibling_key = _order_key_for(row)
            sibling_invoice = (order_has_invoice.get(sibling_key)
                               if sibling_key else None)
            row_has_own_real_invoice = bool(
                existing_qid and not str(existing_qid).startswith(
                    ("DRY:", "PREVIEW:")))
            if sibling_invoice and not row_has_own_real_invoice:
                # A sibling trace / ledger row already carries the قيود
                # invoice — this row must NOT sit in "ready_to_send".
                if category == "ready_to_send":
                    category = "total_rounding_review"
            buckets[category].append({
                "row_id":               row.get("id"),
                "trace_id":             row.get("trace_id"),
                "salla_order_number":   row.get("salla_order_number")
                                        or canonical.get("order_number"),
                "salla_order_id":       row.get("salla_order_id")
                                        or canonical.get("order_id"),
                "salla_order_status":   order_status_native,
                "received_at":          row.get("received_at"),
                "pipeline_stage":       row.get("pipeline_stage"),
                "payment_method":       canonical.get("payment_method"),
                "salla_total":          canonical.get("total_amount"),
                "qoyod_invoice_id":     existing_qid or None,
                "qoyod_invoice_number": row.get("qoyod_invoice_number"),
                "qoyod_invoice_payment_id":
                    row.get("qoyod_invoice_payment_id"),
                "qoyod_receipt_id":     row.get("qoyod_receipt_id"),
                "has_existing_invoice": bool(
                    row_has_own_real_invoice
                    or (sibling_invoice is not None)),
                "existing_invoice_source": (
                    "self" if row_has_own_real_invoice
                    else (sibling_invoice or {}).get("source")),
                "existing_invoice_info": sibling_invoice,
                "dependency_status": (
                    (payloads.get("invoice_diagnostics") or {})
                    .get("dependency_status")),
                "reason":               (row.get("pipeline_error") or {})
                                        .get("code")
                                        or (row.get("pipeline_error") or {})
                                        .get("reason"),
                "totals_comparison":    totals or None,
                "actions_available":    _pending_actions_for_stage(
                                            row.get("pipeline_stage") or "",
                                            flag_enabled),
            })

        counts = {c: len(rs) for c, rs in buckets.items()}
        counts["total"] = sum(counts.values())

        return {
            "ok":                          True,
            "mode":                        "read_only",
            "qoyod_request_sent":          False,
            "production_writes_locked":    lock_effective,
            "lock_source":                 lock_source,
            "selective_live_send_enabled": flag_enabled,
            "eligible_statuses_for_pending_queue": sorted(
                _ELIGIBLE_STATUSES_FOR_QUEUE),
            "counts":                      counts,
            "categories":                  buckets,
            "generated_at":                _iso_now(),
        }

    def _pending_actions_for_stage(stage: str,
                                   flag_enabled: bool) -> list[str]:
        """Which action buttons the UI should offer for a row.
        Preview is always allowed (read-only). Approve-and-Send is
        gated by both the stage AND the tenant flag."""
        actions = ["preview"]
        if stage == "UNRESOLVED_QOYOD_DEPENDENCY":
            actions.append("resolve_dependency")
        if stage == "INVOICE_CREATED_TOTAL_MISMATCH":
            actions.append("finalize_rounding_warning")
        # Approve-and-send is enabled ONLY when:
        #   • The row is truly at a ready-to-send hold state, AND
        #   • The tenant flag is TRUE.
        # Explicitly refused for bank_transfer and unsupported methods.
        if (flag_enabled
                and stage in ("LOCKED_AWAITING_APPROVAL",)):
            actions.append("approve_and_send")
        return actions

    def _iso_now() -> str:
        from datetime import datetime, timezone as _tz
        return datetime.now(_tz.utc).isoformat()

    # ── Finalize Rounding Warning (POST — local DB writes ONLY) ─────
    # Iter-293.4-rev8 (2026-XX) — Explicit operator action to close
    # an INVOICE_CREATED row whose قيود-actual total differs from
    # Salla's by at most 0.01 SAR (qoyod_server_side_rounding). NO
    # قيود calls. NO receipt. NO invoice changes. ONLY:
    #   1. integration_inbox row stage → COMPLETED_WITH_ROUNDING_WARNING.
    #   2. qoyod_invoices ledger status → completed_with_rounding_warning.
    #   3. Append audit row to `qoyod_rounding_warning_audits`.
    @router.post("/admin/qoyod/finalize-rounding-warning")
    async def admin_finalize_rounding_warning(
        payload: FinalizeRoundingWarningBody,
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        actor = getattr(user, "email", None) or tenant
        expected_token = f"FINALIZE-ROUNDING-{payload.order_number}"
        if (payload.confirm or "").strip() != expected_token:
            raise HTTPException(
                status_code=400,
                detail={
                    "code":    "confirm_token_mismatch",
                    "message": ("confirm must equal "
                                f"'FINALIZE-ROUNDING-{payload.order_number}'"),
                    "expected": expected_token,
                })
        # Locate the row.
        q: dict = {"user_id": tenant}
        if payload.trace_id:
            q["trace_id"] = payload.trace_id
        else:
            on = str(payload.order_number)
            cands: list = [on]
            try:
                cands.append(int(on))
            except (TypeError, ValueError):
                pass
            q["$or"] = []
            for v in cands:
                q["$or"].extend([
                    {"salla_order_number": v},
                    {"salla_order_id":     v},
                    {"canonical_payload.order_number": v},
                    {"canonical_payload.order_id":     v},
                ])
        rows = await db.integration_inbox.find(q).to_list(length=20)
        if not rows:
            raise HTTPException(
                status_code=404,
                detail={"code": "row_not_found",
                        "order_number": payload.order_number,
                        "trace_id":     payload.trace_id})
        if len(rows) > 1 and not payload.trace_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "multiple_matches_supply_trace_id",
                    "candidates": [{
                        "trace_id":       r.get("trace_id"),
                        "pipeline_stage": r.get("pipeline_stage"),
                    } for r in rows[:10]],
                })
        row = rows[0]

        if row.get("pipeline_stage") != "INVOICE_CREATED":
            raise HTTPException(
                status_code=409,
                detail={
                    "code":    "wrong_stage",
                    "message": ("finalize-rounding-warning only works on "
                                "rows at INVOICE_CREATED; this row is at "
                                f"{row.get('pipeline_stage')!r}"),
                    "pipeline_stage": row.get("pipeline_stage"),
                })

        qid = row.get("qoyod_invoice_id") or ""
        if not qid or str(qid).startswith(("DRY:", "PREVIEW:")):
            raise HTTPException(
                status_code=409,
                detail={
                    "code":    "qoyod_invoice_id_not_real",
                    "message": ("the row carries no real Qoyod invoice "
                                "id — refusing to finalize as warning."),
                    "qoyod_invoice_id": qid or None,
                })

        # Determine the difference: prefer the persisted comparison;
        # otherwise honour the operator-supplied accept_difference_sar.
        persisted = row.get("totals_comparison") or {}
        persisted_diff = persisted.get("difference")
        effective_diff: Optional[float] = None
        diff_source: str = ""
        if persisted_diff is not None:
            try:
                effective_diff = float(persisted_diff)
                diff_source = "persisted_totals_comparison"
            except (TypeError, ValueError):
                effective_diff = None
        if effective_diff is None and payload.accept_difference_sar is not None:
            effective_diff = float(payload.accept_difference_sar)
            diff_source = "operator_supplied_accept_difference_sar"

        if effective_diff is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code":    "difference_unknown",
                    "message": ("row has no persisted totals_comparison "
                                "AND no accept_difference_sar was "
                                "provided. Cannot finalize blindly."),
                    "hint": ("call GET /admin/order-recovery-diagnostics "
                            "first; if it reports difference=null pass "
                            "accept_difference_sar explicitly."),
                })
        if abs(effective_diff) > 0.01:
            raise HTTPException(
                status_code=409,
                detail={
                    "code":    "difference_exceeds_warning_band",
                    "message": ("the difference exceeds 0.01 SAR; this "
                                "endpoint refuses to finalize because the "
                                "gap is in BLOCKER territory and must be "
                                "reviewed by an accountant."),
                    "difference":       effective_diff,
                    "warning_band_sar": 0.01,
                })

        # Persist the finalisation idempotently.
        canonical = row.get("canonical_payload") or {}
        from datetime import timezone as _tz, datetime as _dt
        now_utc = _dt.now(_tz.utc)
        try:
            p_finalize = transition(
                from_stage="INVOICE_CREATED",
                to_stage="COMPLETED_WITH_ROUNDING_WARNING",
                actor=actor,
                note=(f"finalize-rounding-warning — diff={effective_diff:+.2f} "
                      "SAR accepted as قيود server-side rounding"),
            )
            p_finalize.setdefault("$set", {}).update({
                "rounding_warning":               True,
                "rounding_warning_finalized_at":  now_utc,
                "rounding_warning_finalized_by":  actor,
                "rounding_warning_difference":    effective_diff,
            })
            await db.integration_inbox.update_one(
                {"id": row["id"]}, p_finalize)
        except InvalidTransition as exc:
            raise HTTPException(
                status_code=409,
                detail={"code":    "invalid_transition",
                        "message": str(exc),
                        "from":    row.get("pipeline_stage"),
                        "to":      "COMPLETED_WITH_ROUNDING_WARNING"})

        # Mirror to qoyod_invoices ledger.
        await db.qoyod_invoices.update_one(
            {"user_id":        tenant,
             "salla_order_id": canonical.get("order_id")
                               or str(row.get("salla_order_number") or "")},
            {"$set": {
                "pipeline_stage":   "COMPLETED_WITH_ROUNDING_WARNING",
                "status":           "completed_with_rounding_warning",
                "rounding_warning": True,
                "rounding_warning_difference": effective_diff,
                "updated_at":       now_utc,
            }})

        # ZATCA-ready audit row — survives container restarts, exportable.
        import uuid as _uuid
        audit_id = str(_uuid.uuid4())
        audit_row = {
            "audit_id":        audit_id,
            "user_id":         tenant,
            "order_number":    payload.order_number,
            "trace_id":        row.get("trace_id"),
            "row_id":          row.get("id"),
            "qoyod_invoice_id": qid,
            "qoyod_invoice_number": row.get("qoyod_invoice_number"),
            "actor":           actor,
            "finalized_at":    now_utc,
            "difference_sar":  effective_diff,
            "difference_source": diff_source,
            "totals_comparison_persisted": persisted or None,
            "reason":          "qoyod_server_side_rounding",
            "scope":           "single_order",
            "operator_note":   payload.operator_note,
        }
        try:
            await db.qoyod_rounding_warning_audits.insert_one(audit_row)
        except Exception as _exc:    # pragma: no cover
            logger.warning(
                "qoyod_rounding_warning_audits insert failed: %s", _exc)

        logger.warning(
            "FINALIZE_ROUNDING_WARNING actor=%s order=%s trace=%s "
            "qoyod_invoice_id=%s difference_sar=%+.2f audit_id=%s",
            actor, payload.order_number, row.get("trace_id"),
            qid, effective_diff, audit_id,
        )

        refreshed = await db.integration_inbox.find_one({"id": row["id"]})
        return {
            "ok":      True,
            "outcome": "COMPLETED_WITH_ROUNDING_WARNING",
            "row_id":  row.get("id"),
            "trace_id": row.get("trace_id"),
            "order_number": payload.order_number,
            "qoyod_invoice_id":     qid,
            "qoyod_invoice_number": row.get("qoyod_invoice_number"),
            "qoyod_invoice_payment_id": None,
            "qoyod_receipt_id":     None,
            "qoyod_request_sent":   False,    # local DB only
            "difference_sar":       effective_diff,
            "difference_source":    diff_source,
            "reason":               "qoyod_server_side_rounding",
            "audit_id":             audit_id,
            "finalized_at":         now_utc.isoformat(),
            "finalized_by":         actor,
            "pipeline_stage_after": (refreshed or {}).get("pipeline_stage"),
            "message": (
                f"تم إغلاق الطلب {payload.order_number} كـ "
                "COMPLETED_WITH_ROUNDING_WARNING. فرق "
                f"{effective_diff:+.2f} SAR مقبول كـ قيود server-side "
                "rounding. لا تعديل في قيود. تم تسجيل audit مع actor "
                f"= {actor} و audit_id = {audit_id}."),
        }
    #    Iter-275 (layered `amounts`) + Iter-276 (line discount) +
    #    Iter-278 (legacy adapter nested-amounts fix). Lets the
    #    operator confirm a Production redeploy actually landed
    #    without inspecting code.
    @router.get("/admin/normalizer-self-test")
    async def admin_normalizer_self_test(user=Depends(current_user)):
        from integrations.qoyod.normalizer import _normalize_item
        from integrations.qoyod.legacy_adapter import _adapt_item
        # Exact shape from production order 268632361 / AMS11980.
        sample = {
            "sku": "AMS11980",
            "name": "عباية ستيتش بناتي",
            "quantity": 1,
            "amounts": {
                "price_without_tax": {"amount": 199, "currency": "SAR"},
                "total_discount":    {"amount": 11.94, "currency": "SAR"},
                "tax": {
                    "percent": "8.00",
                    "amount":  {"amount": 14.96, "currency": "SAR"},
                },
                "total": {"amount": 202.02, "currency": "SAR"},
            },
        }
        expected = {"unit_price": 199.0, "tax_amount": 14.96,
                    "discount_amount": 11.94, "total": 202.02}
        try:
            adapted = _adapt_item(sample, "SAR")
            dto = _normalize_item(adapted)
            got = {
                "unit_price":      dto.unit_price,
                "tax_amount":      dto.tax_amount,
                "discount_amount": getattr(dto, "discount_amount", None),
                "total":           dto.total,
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        ok_iter275 = got["unit_price"] == 199.0 and got["tax_amount"] == 14.96
        ok_iter276 = got["discount_amount"] == 11.94
        ok_iter278 = ok_iter275 and ok_iter276    # adapter fix gates both
        return {
            "ok":              ok_iter275 and ok_iter276,
            "iter_275_layered_amounts_supported": ok_iter275,
            "iter_276_line_discount_supported":   ok_iter276,
            "iter_278_adapter_nested_amounts_fix": ok_iter278,
            "expected":        expected,
            "got":             got,
            "adapted_item":    adapted,
            "sample_input":    sample,
            "hint": ("If any flag is false → redeploy needed. The chain "
                     "runs raw → _adapt_item → _normalize_item, so a "
                     "broken adapter (Iter-278) silently zeroes the "
                     "values even when the normalizer itself is fine."),
        }

    # ── Per-Row Replay — re-run the EXACT raw payload of an existing
    #    inbox row through the live adapter + normalizer chain. Use
    #    this when self-test says ok=true but a specific order still
    #    produces zeros: it tells you whether the bug is in the
    #    parsing chain OR in the stored canonical (cached pre-fix).
    @router.get("/admin/normalize-row-self-test")
    async def admin_normalize_row_self_test(
        trace_id: str = Query(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.normalizer import _normalize_item, normalize
        from integrations.qoyod.legacy_adapter import adapt as adapt_legacy
        tenant = _tenant_id(user)
        row = await db.integration_inbox.find_one(
            {"user_id": tenant, "trace_id": trace_id},
            {"_id": 0, "raw_payload": 1, "canonical_payload": 1,
             "trace_id": 1, "salla_order_number": 1, "pipeline_stage": 1,
             "received_at": 1})
        if not row:
            raise HTTPException(
                status_code=404,
                detail={"code": "row_not_found",
                        "trace_id": trace_id})
        raw = row.get("raw_payload") or {}
        out: dict = {
            "row": {
                "trace_id":           row.get("trace_id"),
                "salla_order_number": row.get("salla_order_number"),
                "pipeline_stage":     row.get("pipeline_stage"),
                "received_at":        row.get("received_at"),
            },
            "stored_canonical": row.get("canonical_payload"),
        }
        # ─ Step 1: legacy adapter ────────────────────────────────────
        try:
            adapted, adapter_meta = adapt_legacy(raw)
        except Exception as exc:
            out["adapter_error"] = f"{type(exc).__name__}: {exc}"
            return out
        out["adapter_meta"] = adapter_meta
        adapted_items = (adapted.get("data") or {}).get("items") \
            if isinstance(adapted.get("data"), dict) else adapted.get("items")
        out["adapter_first_item"] = (adapted_items or [None])[0]
        # Iter-279: surface the status fields the operator can verify
        # made it through the adapter into the payload the normalizer
        # actually sees.
        if isinstance(adapted, dict):
            data_envelope = adapted.get("data") or {}
            out["adapted_payload_status"] = {
                "order_status":      adapted.get("order_status"),
                "order_status_slug": adapted.get("order_status_slug"),
                "status":            adapted.get("status"),
                "data.status":       data_envelope.get("status"),
            }
            # Trace where the status came from in the original raw.
            out["status_source"] = (
                "raw.order_status_slug" if raw.get("order_status_slug")
                else "raw.status_slug" if raw.get("status_slug")
                else "raw.status"       if raw.get("status")
                else "raw.order_status" if raw.get("order_status")
                else "MISSING"
            )

        # ─ Step 2: normalizer (full DTO from ADAPTED payload) ────────
        # Iter-279: previously called `normalize(raw)` which skipped
        # the adapter step — for legacy Make payloads this raised
        # `NormalizationError(missing_order_status)` because the status
        # lives in `raw.order_status_slug` and only the adapter routes
        # it into `data.status` where the normalizer looks. Mirror the
        # real webhook chain: adapt() THEN normalize(adapted).
        normalizer_input = adapted if adapter_meta.get("adapter_applied") else raw
        try:
            dto = normalize(normalizer_input,
                            received_at=row.get("received_at"))
            canon = dto.model_dump(mode="json")
        except Exception as exc:
            out["normalizer_error"] = f"{type(exc).__name__}: {exc}"
            # Surface the status keys we DID try so the operator knows
            # whether the adapter dropped them.
            out["status_in_adapted_payload"] = {
                "data.status":  (adapted.get("data") or {}).get("status")
                                 if isinstance(adapted, dict) else None,
                "order_status": (adapted or {}).get("order_status")
                                 if isinstance(adapted, dict) else None,
                "order_status_slug": (adapted or {}).get("order_status_slug")
                                      if isinstance(adapted, dict) else None,
                "status":       (adapted or {}).get("status")
                                 if isinstance(adapted, dict) else None,
            }
            return out

        live_first = (canon.get("items") or [None])[0]
        stored_first = ((row.get("canonical_payload") or {}).get("items")
                        or [None])[0]
        out["live_first_item"] = live_first
        out["stored_first_item"] = stored_first

        # ─ Per-field extractor source attribution ───────────────────
        if isinstance(adapted_items, list) and adapted_items:
            raw_first = (raw.get("items") or [None])[0] \
                if isinstance(raw.get("items"), list) else None
            adapted_first = adapted_items[0] if adapted_items else None
            adapted_amounts = (adapted_first or {}).get("amounts") or {}
            out["extractor_source"] = {
                "unit_price": ("raw.items[0].amounts.price_without_tax.amount"
                               if (raw_first or {}).get("amounts", {}).get(
                                   "price_without_tax") is not None
                               else ("raw.items[0].price.amount"
                                     if isinstance((raw_first or {}).get("price"), dict)
                                     else "raw.items[0].unit_price / fallback 0")),
                "tax_amount": ("raw.items[0].amounts.tax (recursed)"
                               if (raw_first or {}).get("amounts", {}).get("tax")
                               is not None
                               else "fallback 0"),
                "discount_amount": ("raw.items[0].amounts.total_discount.amount"
                                     if (raw_first or {}).get("amounts", {}).get(
                                         "total_discount") is not None
                                     else "fallback 0"),
                "total": ("raw.items[0].amounts.total.amount"
                          if (raw_first or {}).get("amounts", {}).get("total")
                          is not None
                          else "fallback unit_price*quantity"),
                "adapted_amounts": adapted_amounts,
            }

        # ─ Drift detection ──────────────────────────────────────────
        out["live_vs_stored_drift"] = (live_first != stored_first)
        out["hint"] = (
            "If `live_first_item` is correct but `stored_first_item` "
            "shows zeros, the row was normalized BEFORE the fix shipped — "
            "reprocess the row to overwrite the stored canonical. "
            "If `live_first_item` ALSO shows zeros, the deploy is stale."
        )
        return out

    # ── Webhook Parse Failures — last N malformed-JSON receipts ─────
    # When Make.com sends invalid JSON (e.g. injecting an Array into
    # Raw Body without Create JSON), Mezan rejects with 422 and logs
    # to `webhook_parse_failures`. This route exposes the last N for
    # debugging without forcing the operator to dig in Mongo.
    @router.get("/admin/webhook-parse-failures")
    async def admin_webhook_parse_failures(
        limit: int = Query(5, ge=1, le=50),
        user=Depends(current_user),
    ):
        # The collection is single-tenant by design (token-scoped) so
        # we surface globally for `main`. The token_prefix tells the
        # operator which scenario tripped.
        rows = await db.webhook_parse_failures \
            .find({}, {"_id": 0}) \
            .sort("occurred_at", -1) \
            .limit(limit).to_list(length=limit)
        return {
            "count":   len(rows),
            "rows":    rows,
            "hint":    ("If 'body_preview' contains literal `[object Object]` "
                        "or `omap{...}`, Make.com is injecting an Array into "
                        "Raw JSON. Use a Create JSON module instead — see "
                        "docs/integrations/make-runbook-build-items-array.md"),
        }

    # ── Iter-293 — Webhook Activity Log ──────────────────────────────
    # Lightweight tail of EVERY webhook arrival from Make (or future
    # direct Salla webhook). Operators use this as a live "tail -f" to
    # spot parsing errors, missed events, or stale upstream config.
    from integrations.qoyod.webhook_activity import (
        list_recent_events, get_event_counts, soft_cap_old_rows,
    )

    @router.get("/admin/webhook-activity")
    async def admin_webhook_activity(
        limit: int = Query(50, ge=1, le=200),
        event_type: Optional[str] = Query(None),
        order_id: Optional[str] = Query(None),
        skipped_only: bool = Query(False),
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        rows = await list_recent_events(
            db, user_id=tenant, limit=limit,
            event_type=event_type, salla_order_id=order_id,
            skipped_only=skipped_only)
        # Soft cap maintenance — lazy trim once per fetch.
        # Fire-and-forget; never raises (helper swallows).
        try:
            await soft_cap_old_rows(db, user_id=tenant, keep=1000)
        except Exception:  # noqa: BLE001
            pass
        return {"count": len(rows), "rows": rows}

    @router.get("/admin/webhook-activity/counts")
    async def admin_webhook_activity_counts(
        hours: int = Query(24, ge=1, le=168),
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        return await get_event_counts(db, user_id=tenant, since_hours=hours)

    @router.get("/admin/unallocated-receipts-report")
    async def admin_unallocated_receipts_report(
        max_receipts: int = Query(200, ge=1, le=500),
        max_invoices: int = Query(500, ge=1, le=2000),
        user=Depends(current_user),
    ):
        """Iter-290h — Manual reconciliation report.

        Lists Qoyod receipts that appear unallocated (the "غير مستعمل"
        bin in قيود) and proposes a matching invoice for each one,
        scored by reference / amount / customer / date proximity.
        Dismissed rows (operator marked as "تمت المعالجة يدوياً") are
        filtered out.

        READ-ONLY. The endpoint never mutates Qoyod state — the
        operator links receipts manually in قيود UI. A later iteration
        may add a one-click "تخصيص" button once we've proven the
        suggestion accuracy on a real sample.
        """
        from integrations.qoyod.unallocated_receipts_report import (
            build_unallocated_receipts_report,
        )
        tenant = _tenant_id(user)
        return await build_unallocated_receipts_report(
            db, user_id=tenant,
            max_receipts=max_receipts, max_invoices=max_invoices,
        )

    class _DismissPatch(BaseModel):
        model_config = ConfigDict(extra="forbid")
        note: Optional[str] = Field(default=None, max_length=500)

    @router.post("/admin/unallocated-receipts/{receipt_id}/dismiss")
    async def admin_unallocated_receipt_dismiss(
        receipt_id: str,
        body: _DismissPatch = _DismissPatch(),
        user=Depends(current_user),
    ):
        """Iter-290h — Operator marks a Qoyod receipt as 'تمت المعالجة
        يدوياً' inside ميزان. The report no longer surfaces it.
        Idempotent — calling twice refreshes `dismissed_at`."""
        from integrations.qoyod.unallocated_receipts_report import (
            dismiss_receipt,
        )
        tenant = _tenant_id(user)
        actor = (getattr(user, "email", None)
                 or getattr(user, "id", None) or "operator")
        row = await dismiss_receipt(
            db, user_id=tenant,
            qoyod_receipt_id=str(receipt_id),
            actor=str(actor), note=body.note,
        )
        return {"ok": True, "dismissed": row}

    @router.delete("/admin/unallocated-receipts/{receipt_id}/dismiss")
    async def admin_unallocated_receipt_undismiss(
        receipt_id: str, user=Depends(current_user),
    ):
        """Iter-290h — Reverse of dismiss. Soft-toggles `active=False`
        so the row reappears in the report (audit trail preserved)."""
        from integrations.qoyod.unallocated_receipts_report import (
            undismiss_receipt,
        )
        tenant = _tenant_id(user)
        return await undismiss_receipt(
            db, user_id=tenant, qoyod_receipt_id=str(receipt_id),
        )

    class _RetryPaymentBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        salla_order_number: str = Field(..., min_length=1, max_length=64)
        confirm_token:      str = Field(..., min_length=1, max_length=128)

    @router.post("/admin/retry-payment-only")
    async def admin_retry_payment_only(
        body: RetryPaymentOnlyBody = Body(...),
        user=Depends(current_user),
    ):
        """Iter-290h.5 — Surgical retry of `POST /invoice_payments`
        for an existing قيود invoice. Touches NOTHING else (no
        customer/products/invoice/receipt creation, no full pipeline
        re-run). Carries a structured diagnostic block — including
        the LIVE قيود verdict, not a stale one from a previous run.

        Iter-2026-02.rev14 — `RetryPaymentOnlyBody` is defined at
        MODULE scope (not inside this factory) so FastAPI resolves
        the ForwardRef and binds it as a JSON body. Declaring the
        model inside the factory made FastAPI mis-classify it as a
        query parameter and return 422 `{"loc": ["query", "body"]}`.
        The `= Body(...)` marker is defensive; the module-scope
        model is the primary fix.

        Confirmation token: `RETRY-PAYMENT-<salla_order_number>`.
        """
        from integrations.qoyod.retry_payment_only import (
            retry_payment_only, RetryPaymentRefused,
        )
        tenant = _tenant_id(user)
        actor = (getattr(user, "email", None)
                 or getattr(user, "id", None) or "operator")
        try:
            return await retry_payment_only(
                db,
                user_id=tenant,
                salla_order_number=body.salla_order_number,
                confirm_token=body.confirm_token,
                actor=str(actor),
            )
        except RetryPaymentRefused as exc:
            return exc.to_dict()

    # ── Iter-2026-02.rev15 — Adopt an EXISTING قيود receipt ─────────
    # Operator has already created the receipt manually in قيود
    # (e.g. PYT2 for order 269629400 / invoice 186). We record it in
    # Mezan + write an idempotency ledger row so any future automatic
    # attempt (retry_payment_only, canary, worker) returns
    # ALREADY_PAID instead of POSTing a duplicate receipt. NO قيود
    # API calls are performed.
    @router.post("/admin/adopt-existing-payment")
    async def admin_adopt_existing_payment(
        body: AdoptExistingPaymentBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.adopt_existing_payment import (
            adopt_existing_payment, AdoptPaymentRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await adopt_existing_payment(
                db, user_id=tenant,
                salla_order_number=body.salla_order_number,
                qoyod_invoice_payment_id=body.qoyod_invoice_payment_id,
                qoyod_invoice_id=body.qoyod_invoice_id,
                qoyod_customer_id=body.qoyod_customer_id,
                confirm_token=body.confirm_token,
                actor=str(actor),
            )
        except AdoptPaymentRefused as exc:
            return {
                "ok":     False,
                "outcome": "REFUSED",
                "code":   exc.code,
                "detail": str(exc),
                **exc.extra,
            }

    # ── Iter-2026-02.rev16 — Selective Auto-Send admin endpoints ────
    # Enable/Disable/Expand the tenant's Selective Auto-Send policy.
    # Enable stamps `cutover_at=NOW` automatically — orders created
    # BEFORE this timestamp are NEVER auto-sent (no backlog).
    # `production_writes_locked` is NEVER modified by these endpoints.
    @router.post("/admin/enable-selective-auto-send")
    async def admin_enable_selective_auto_send(
        body: EnableSelectiveAutoSendBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.enable_selective_auto_send import (
            enable_selective_auto_send, SelectiveAutoSendRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await enable_selective_auto_send(
                db, user_id=tenant,
                confirm_token=body.confirm_token,
                allowed_payment_methods=body.allowed_payment_methods,
                actor=str(actor))
        except SelectiveAutoSendRefused as exc:
            return {"ok": False, "outcome": "REFUSED",
                    "code": exc.code, "detail": exc.human}

    @router.post("/admin/disable-selective-auto-send")
    async def admin_disable_selective_auto_send(
        body: DisableSelectiveAutoSendBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.enable_selective_auto_send import (
            disable_selective_auto_send, SelectiveAutoSendRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await disable_selective_auto_send(
                db, user_id=tenant,
                confirm_token=body.confirm_token,
                actor=str(actor))
        except SelectiveAutoSendRefused as exc:
            return {"ok": False, "outcome": "REFUSED",
                    "code": exc.code, "detail": exc.human}

    @router.post("/admin/expand-selective-auto-send")
    async def admin_expand_selective_auto_send(
        body: ExpandSelectiveAutoSendBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.enable_selective_auto_send import (
            expand_allowed_payment_methods, SelectiveAutoSendRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await expand_allowed_payment_methods(
                db, user_id=tenant,
                add_methods=body.add_methods,
                confirm_token=body.confirm_token,
                actor=str(actor))
        except SelectiveAutoSendRefused as exc:
            return {"ok": False, "outcome": "REFUSED",
                    "code": exc.code, "detail": exc.human}

    # ── Iter-2026-02.rev31 — Live Canary for Tabby (Option A) ────────
    # Purpose-built endpoint that flips EXACTLY three flags and
    # nothing else:
    #     dry_run_mode                = False
    #     production_writes_locked    = False
    #     selective_live_send_enabled = True
    # Refuses if ANY precondition fails (auto_send must be OFF,
    # SAS must be ENABLED, allow-list must be exactly
    # ["tabby_installment"], auto_receipt=True, capabilities.
    # create_receipts=True). NEVER touches payment_method_mapping.
    # Rollback via /admin/live-canary/disable-tabby is always
    # available and restores the fail-closed posture.
    @router.post("/admin/live-canary/enable-tabby")
    async def admin_enable_tabby_live_canary(
        body: EnableTabbyLiveCanaryBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.live_canary import (
            enable_tabby_live_canary, LiveCanaryRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await enable_tabby_live_canary(
                db, user_id=tenant,
                confirm_token=body.confirm_token,
                actor=str(actor))
        except LiveCanaryRefused as exc:
            return {"ok": False, "outcome": "REFUSED",
                    "code": exc.code, "detail": exc.message}

    @router.post("/admin/live-canary/disable-tabby")
    async def admin_disable_tabby_live_canary(
        body: DisableTabbyLiveCanaryBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.live_canary import (
            disable_tabby_live_canary, LiveCanaryRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await disable_tabby_live_canary(
                db, user_id=tenant,
                confirm_token=body.confirm_token,
                actor=str(actor),
                reason=body.reason)
        except LiveCanaryRefused as exc:
            return {"ok": False, "outcome": "REFUSED",
                    "code": exc.code, "detail": exc.message}

    # ── Iter-2026-02.rev18 — Force-reprocess a DRY-run row ──────────
    # Dedicated recovery endpoint for rows stuck at INVOICE_CREATED
    # with DRY:invoice:* sentinels. Refuses when a REAL قيود
    # invoice_id exists anywhere for the order. Clears DRY IDs,
    # rewinds stage NORMALIZED, and re-runs the pipeline inline so
    # the Selective Auto-Send Gate + scoped live client (rev17) fire.
    @router.post("/admin/force-reprocess-dry")
    async def admin_force_reprocess_dry(
        body: ForceReprocessDryBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.force_reprocess_dry import (
            force_reprocess_dry_row, ForceReprocessRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await force_reprocess_dry_row(
                db, user_id=tenant,
                salla_order_number=body.salla_order_number,
                trace_id=body.trace_id,
                confirm_token=body.confirm_token,
                actor=str(actor))
        except ForceReprocessRefused as exc:
            return {"ok": False, "outcome": "REFUSED",
                    "code": exc.code, "detail": str(exc),
                    **exc.extra}

    # ── Iter-2026-02.rev21 — Approve LOCKED_AWAITING_APPROVAL ──────
    # Replay the saved `/invoice_payments` payload from
    # `qoyod_write_lock_attempts`. NEVER creates an invoice, NEVER
    # a customer/product. Scoped bypass of the write lock — DB
    # `production_writes_locked` stays TRUE on disk.
    @router.post("/admin/approve-locked-payment")
    async def admin_approve_locked_payment(
        body: ApproveLockedPaymentBody = Body(...),
        user=Depends(current_user),
    ):
        from integrations.qoyod.approve_locked_payment import (
            approve_locked_payment, ApproveLockedPaymentRefused,
        )
        tenant = _tenant_id(user)
        actor  = (getattr(user, "email", None) or "operator")
        try:
            return await approve_locked_payment(
                db, user_id=tenant,
                lock_attempt_id=body.lock_attempt_id,
                confirm_token=body.confirm_token,
                actor=str(actor))
        except ApproveLockedPaymentRefused as exc:
            return {"ok": False, "outcome": "REFUSED",
                    "code": exc.code, "detail": str(exc),
                    **exc.extra}

    # ── Iter-290h.7 — Payment-method field probe (read-only) ────────
    class _PaymentMethodProbeBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        empty_payment_method_invoice_id:   str = Field(
            ..., min_length=1, max_length=64)
        reference_invoice_id_with_payment: str = Field(
            ..., min_length=1, max_length=64)

    @router.post("/admin/payment-method-field-probe")
    async def admin_payment_method_field_probe(
        body: _PaymentMethodProbeBody, user=Depends(current_user),
    ):
        """Iter-290h.7 — Strictly READ-ONLY diagnostic. Calls
        `GET /invoices/{id}` on two قيود invoices (one with empty
        payment method, one with a populated payment method) and
        returns a structured comparison so the operator can identify
        the canonical wire field. NO writes against قيود.
        """
        from integrations.qoyod.payment_method_field_probe import (
            probe_payment_method_field,
        )
        tenant = _tenant_id(user)
        return await probe_payment_method_field(
            db, user_id=tenant,
            empty_payment_method_invoice_id=body.empty_payment_method_invoice_id,
            reference_invoice_id_with_payment=body.reference_invoice_id_with_payment,
        )

    # ── Iter-290i — Reference-Lists for the picker UI ───────────────
    @router.post("/admin/reference-lists/refresh")
    async def admin_refresh_reference_lists(user=Depends(current_user)):
        """Iter-290i — Pull every Qoyod reference list (categories,
        unit_types, inventories, accounts, taxes, branches, customers)
        and cache them for the settings-page pickers. Strictly
        READ-ONLY against Qoyod."""
        from integrations.qoyod.reference_lists import (
            refresh_reference_lists,
        )
        tenant = _tenant_id(user)
        return await refresh_reference_lists(db, user_id=tenant)

    @router.get("/admin/reference-lists")
    async def admin_get_reference_lists(user=Depends(current_user)):
        """Iter-290i — Return the cached reference lists. Empty
        document with `cached: false` if the operator hasn't refreshed
        yet."""
        from integrations.qoyod.reference_lists import (
            get_reference_lists,
        )
        tenant = _tenant_id(user)
        return await get_reference_lists(db, user_id=tenant)

    # ── Iter-290j-rounding-fix · Phase 1 — Read-only diagnostic ─────
    @router.get("/admin/rounding-mismatch-report")
    async def admin_rounding_mismatch_report(
        limit: int = 200, user=Depends(current_user),
    ):
        """Iter-290j-rounding-fix Phase 1 — Strictly READ-ONLY scan
        of completed/partial inbox rows that classifies each
        discrepancy into one of five buckets so the operator can
        decide which fix to apply. NO writes against قيود or DB."""
        from integrations.qoyod.rounding_mismatch_report import (
            build_rounding_mismatch_report,
        )
        tenant = _tenant_id(user)
        return await build_rounding_mismatch_report(
            db, user_id=tenant, limit=max(1, min(limit, 500)))

    # ── Iter-290k · Phase-2 DRY-RUN — Strictly READ-ONLY simulation ─
    # Simulates the proposed قيود-internal-rounding fix using
    # Decimal + ROUND_HALF_UP on the actual invoice payload we sent.
    # Zero DB writes, zero قيود calls — pure projection.
    @router.get("/admin/rounding-dry-run")
    async def admin_rounding_dry_run(
        limit: int = 200, user=Depends(current_user),
    ):
        """Iter-290k Phase-2 DRY-RUN — never mutates anything.
        Returns per-eligible-row simulation results showing whether
        a single-line discount adjustment would land the قيود total
        exactly on the Salla total."""
        from integrations.qoyod.rounding_dry_run import (
            build_dry_run_report,
        )
        tenant = _tenant_id(user)
        return await build_dry_run_report(
            db, user_id=tenant, limit=max(1, min(limit, 500)))

    # ── Salla Order Statuses — dynamic source for the trigger picker ─
    # Avoids hardcoding "completed"/"delivered"/"paid" — pulls the
    # tenant's actual status catalogue from Salla so custom statuses
    # are also selectable. Falls back to observed statuses from
    # `unified_orders` when Salla API is unreachable.
    @router.get("/salla-order-statuses")
    async def salla_order_statuses(user=Depends(current_user)):
        # Qoyod accounting markers still live under the singleton ``main``
        # tenant, but Salla OAuth and Orders V2 belong to the merchant owner.
        # Using ``main`` here made a healthy Salla connection look disconnected
        # in the Qoyod settings page and could trip the automatic-send breaker.
        orders_owner = orders_owner_id(user)
        statuses: list[dict] = []
        source = "salla_api"
        error: Optional[dict] = None
        try:
            resp = await call_salla(
                db, orders_owner, "GET", "/orders/statuses")
            data = resp.get("data") if isinstance(resp, dict) else None
            if isinstance(data, list):
                for s in data:
                    if not isinstance(s, dict):
                        continue
                    slug = (s.get("slug") or s.get("code")
                            or s.get("name") or "").strip()
                    if not slug:
                        continue
                    statuses.append({
                        "id":    s.get("id"),
                        "slug":  slug.lower(),
                        "name":  s.get("name") or slug,
                        "name_en": s.get("name_en"),
                        "type":  s.get("type"),
                        "is_system": s.get("type") == "system",
                    })
        except SallaError as exc:
            error = {"code": "salla_unavailable", "message": str(exc),
                     "needs_reauth": getattr(exc, "needs_reauth", False)}
            source = "fallback"
        # Fallback: distinct statuses observed in unified_orders.
        if not statuses:
            seen: set[str] = set()
            async for o in db.unified_orders.find(
                {"user_id": orders_owner},
                {"order_status": 1, "raw.status.slug": 1,
                 "raw.status.name": 1, "_id": 0},
            ):
                slug = (o.get("order_status") or "").strip().lower()
                if slug and slug not in seen:
                    seen.add(slug)
                    raw_status = ((o.get("raw") or {}).get("status") or {})
                    statuses.append({
                        "id":   None,
                        "slug": slug,
                        "name": raw_status.get("name") or slug,
                        "name_en": None,
                        "type": "observed",
                        "is_system": False,
                    })
        return {"ok": True, "statuses": statuses,
                "source": source, "error": error}



    # ── طلبات لم تُرسل إلى قيود — READ-ONLY ────────────────────────
    @router.get("/unsent-orders")
    async def unsent_orders_endpoint(
        days: int = Query(30, ge=1, le=365),
        limit: int = Query(1000, ge=1, le=5000),
        status: Optional[str] = Query(None),
        salla_status: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        user=Depends(current_user),
    ):
        from integrations.qoyod.unsent_orders import list_unsent_orders

        tenant = _tenant_id(user)
        return await list_unsent_orders(
            db,
            user_id=tenant,
            orders_user_id=orders_owner_id(user),
            days=days,
            limit=limit,
            status=status,
            salla_status=salla_status,
            search=search,
        )

    # ── تقرير المطابقة ميزان ↔ قيود — READ-ONLY ─────────────────────
    @router.get("/reconciliation-report")
    async def reconciliation_report_endpoint(
        sync_first: bool = Query(
            True,
            description=(
                "If true, fetch invoices from Qoyod into the local "
                "qoyod_invoices table before comparison."
            ),
        ),
        user=Depends(current_user),
    ):
        tenant = _tenant_id(user)
        sync_summary: dict = {"ran": False}

        if sync_first:
            try:
                key = await get_api_key(db, tenant)
                if not key:
                    return {
                        "ok": False,
                        "error": (
                            "لم يتم ضبط API key لقيود. أضف المفتاح من "
                            "إعدادات قيود قبل تشغيل المطابقة."
                        ),
                        "sync_summary": {
                            "ran": True,
                            "ok": False,
                            "error": "no_credentials",
                        },
                        "counts": {},
                        "rows": [],
                    }

                from integrations.qoyod.qoyod_invoices_sync import (
                    sync_qoyod_invoices,
                )

                api_client = await _build_qoyod_client_for(
                    db, tenant, key
                )
                sync_summary = await sync_qoyod_invoices(
                    db,
                    user_id=tenant,
                    api_client=api_client,
                )
                sync_summary["ran"] = True

            except Exception as exc:
                return {
                    "ok": False,
                    "error": (
                        "خطأ أثناء جلب فواتير قيود: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "sync_summary": {
                        "ran": True,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    "counts": {},
                    "rows": [],
                }

            if not sync_summary.get("ok"):
                return {
                    "ok": False,
                    "error": (
                        "فشل جلب فواتير قيود؛ لم تُنفذ المطابقة "
                        "على بيانات محلية قديمة."
                    ),
                    "sync_summary": sync_summary,
                    "counts": {},
                    "rows": [],
                }

        try:
            from integrations.qoyod.reconciliation_v2 import (
                run_reconciliation_v2,
            )

            report = await run_reconciliation_v2(
                db,
                orders_user_id=orders_owner_id(user),
                markers_user_id=tenant,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": (
                    "فشل تشغيل تقرير المطابقة: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "sync_summary": sync_summary,
                "counts": {},
                "rows": [],
            }

        report["sync_summary"] = sync_summary

        try:
            await db.qoyod_reconciliation_reports.insert_one({
                "user_id": tenant,
                **{k: v for k, v in report.items() if k != "ok"},
            })
        except Exception:
            pass

        return report

    # ── Iter-294 — Global Qoyod Production Write Lock report ────────
    # Read-only audit endpoint. Shows every write attempt that was
    # refused by the global lock, with the locked payload, action,
    # order_number, trace_id, and operator recommendation per row.
    @router.get("/admin/write-lock-report")
    async def write_lock_report(
        user=Depends(current_user),
        limit:        int            = Query(100, ge=1, le=500),
        since_hours:  Optional[int]  = Query(None, ge=1, le=720),
        action:       Optional[str]  = Query(None),
        order_number: Optional[str]  = Query(None),
    ):
        """Return blocked-write attempts + counts.

        Filters:
          • `since_hours` — only rows blocked within the last N hours.
          • `action`      — `create_invoice` / `create_invoice_payment` /
                            `create_product` / `create_contact` / etc.
          • `order_number` — single-order drill-down.
        """
        tenant = _tenant_id(user)
        settings_doc = await db.qoyod_settings.find_one(
            {"user_id": tenant},
            {"_id": 0, "production_writes_locked": 1}) or {}
        # Iter-293.4 — `effective_lock_state` honours fail-closed default.
        effective_locked = is_locked(settings_doc)
        explicit_value = settings_doc.get("production_writes_locked")
        fail_closed_env = fail_closed_default_enabled()

        rows = await list_blocked_attempts(
            db, user_id=tenant, limit=limit,
            action=action, order_number=order_number,
            since_hours=since_hours,
        )
        counts_24h = await count_blocked_attempts_by_action(
            db, user_id=tenant, since_hours=24)

        # Operator-facing summary card.
        total_attempts = sum(counts_24h.values())
        if effective_locked:
            note = (
                "القفل مفعل: كل محاولة كتابة لقيود محفوظة هنا للمراجعة. "
                "لا يتم الإرسال إلا عبر one-shot-reprocess بعد موافقة صريحة.")
            if explicit_value is None:
                # Fail-closed by default — code-level safety net.
                note = (
                    "القفل مفعل (Fail-Closed افتراضي): الحقل "
                    "production_writes_locked غير مضبوط، والكود يفرض "
                    "القفل افتراضياً لحماية بيئة قيود الإنتاجية. "
                    "اضبط الحقل صراحةً (true/false) من إعدادات قيود.")
        else:
            note = ("القفل غير مفعل حالياً — أي كتابة جديدة ستذهب مباشرة "
                    "لقيود. لتفعيل القفل: PUT /settings "
                    "{production_writes_locked: true}.")

        return {
            "ok": True,
            "production_writes_locked":        effective_locked,
            "production_writes_locked_field":  explicit_value,    # None means missing
            "fail_closed_default_enabled":     fail_closed_env,
            "lock_source": (
                "explicit_setting" if explicit_value is not None
                else ("fail_closed_default" if fail_closed_env
                      else "dev_escape_hatch_unlocked")
            ),
            "summary": {
                "total_blocked_24h":  total_attempts,
                "by_action_24h":      counts_24h,
                "filters": {
                    "limit":        limit,
                    "since_hours":  since_hours,
                    "action":       action,
                    "order_number": order_number,
                },
                "operator_note": note,
            },
            "items": rows,
        }

    return router
