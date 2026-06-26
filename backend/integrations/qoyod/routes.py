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

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
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


# MVP runs single-tenant; we still derive user_id from the auth layer
# so the schema stays multi-tenant ready (ADR-001 #11).
_MVP_TENANT_ID = "main"


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


class CredentialsRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


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
    async def _proxied_catalog(tenant: str, fetcher_name: str):
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(400, "no_credentials")
        try:
            client = QoyodAPIClient(key)
            fn = getattr(client, fetcher_name)
            data = await fn()
            return {"ok": True, "data": data}
        except QoyodAPIError as exc:
            return {"ok": False, "error": exc.to_log_dict()}

    @router.get("/qoyod-branches")
    async def qoyod_branches(user=Depends(current_user)):
        return await _proxied_catalog(_tenant_id(user), "list_branches")

    @router.get("/qoyod-accounts")
    async def qoyod_accounts(user=Depends(current_user)):
        return await _proxied_catalog(_tenant_id(user), "list_accounts")

    @router.get("/qoyod-taxes")
    async def qoyod_taxes(user=Depends(current_user)):
        return await _proxied_catalog(_tenant_id(user), "list_taxes")

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

    return router
