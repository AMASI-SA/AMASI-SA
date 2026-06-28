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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.product_resolver import adopt_qoyod_product
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
    # Iter-290 — Qoyod /invoices requires `inventory_id` on every line
    # item, even for service/non-stock products. The operator creates
    # one default warehouse in Qoyod and pastes its id here.
    default_inventory_id:          Optional[str] = None


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


class OneShotReprocessBody(BaseModel):
    """Strict single-order reprocess payload. The operator MUST supply
    `order_number` (the human-readable Salla order id) plus a
    `confirm` token that equals `REPROCESS-<order_number>`. The
    optional `trace_id` disambiguates when multiple inbox rows exist
    for the same order_number (e.g. multiple status webhooks)."""
    model_config = ConfigDict(extra="forbid")
    order_number: str = Field(..., min_length=1, max_length=64)
    confirm:      str = Field(..., min_length=1, max_length=128)
    trace_id:     Optional[str] = None


class PreviewReprocessBody(BaseModel):
    """Safe simulation — re-runs adapter → normalizer → builders for a
    single inbox row without ANY network call to Qoyod. No confirm
    token required (no side-effects). Either order_number OR trace_id
    must be supplied; trace_id wins when both are given."""
    model_config = ConfigDict(extra="forbid")
    order_number: Optional[str] = None
    trace_id:     Optional[str] = None


class TestConnectionResponse(BaseModel):
    ok:          bool
    fingerprint: Optional[str] = None
    qoyod_user:  Optional[dict] = None
    error:       Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────
def make_qoyod_router(db, current_user) -> APIRouter:
    router = APIRouter(
        prefix="/integrations/qoyod",
        tags=["integrations:qoyod"],
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
        # Legacy default `completed_at` was equivalent to the new
        # `trigger_status_date` semantics. Migrate on the fly.
        if doc.get("invoice_date_source") == "completed_at":
            doc["invoice_date_source"] = "trigger_status_date"
        return doc

    async def _attach_fingerprint(tenant: str, payload: dict) -> dict:
        fp = await get_fingerprint(db, tenant)
        payload["credentials"] = {
            "configured":  bool(fp),
            "fingerprint": fp,
        }
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
        # Validate the merged result via Pydantic so we never persist
        # an invalid combination (ADR-001 #4 Canonical Domain).
        merged = {**current, **update,
                  "user_id":    tenant,
                  "updated_at": _now()}
        valid = QoyodSettings(**merged).model_dump(mode="json")
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
            {"user_id": tenant}, {"$set": {"enabled": False}})
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
        inbox = await db.integration_inbox.find_one(
            {"user_id": tenant, "salla_order_id": order_id},
            {"_id": 0, "stage_history": 1, "pipeline_stage": 1,
             "pipeline_error": 1, "attempts": 1, "trace_id": 1,
             "received_at": 1, "connector_key": 1},
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
            db, user_id=tenant, api_client=QoyodAPIClient(key))
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
                db, user_id=tenant, api_client=QoyodAPIClient(key))
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
                api_client=QoyodAPIClient(key))
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

    # ── Salla Order Statuses — dynamic source for the trigger picker ─
    # Avoids hardcoding "completed"/"delivered"/"paid" — pulls the
    # tenant's actual status catalogue from Salla so custom statuses
    # are also selectable. Falls back to observed statuses from
    # `unified_orders` when Salla API is unreachable.
    @router.get("/salla-order-statuses")
    async def salla_order_statuses(user=Depends(current_user)):
        tenant = _tenant_id(user)
        statuses: list[dict] = []
        source = "salla_api"
        error: Optional[dict] = None
        try:
            resp = await call_salla(db, tenant, "GET", "/orders/statuses")
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
                {"user_id": tenant},
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

    return router
