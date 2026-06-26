"""POST /api/integrations/qoyod/webhook — Day 3 entry point.

Strict Day 3 scope (per user directive):
    1) Receive webhook
    2) Verify token
    3) Idempotency
    4) Save raw event
    5) Validation
    6) Normalization
    7) Build canonical SalesOrderDTO
    8) STOP

No business rules, no Qoyod calls. Failures during validation OR
normalization → FAILED_* stage → DEAD_LETTER. Nothing is deleted,
nothing retries forever — DEAD_LETTER rows stay queryable for
manual review (Day 4-5 will wire the retry button).
"""
from __future__ import annotations

import hmac
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from integrations.qoyod.normalizer import (
    validate, normalize, NormalizationError,
)
from integrations.qoyod.state_machine import (
    transition, initial_history_entry,
)
from integrations.qoyod.webhook_token_store import (
    verify_provided_token,
)
from integrations.qoyod.legacy_adapter import adapt as adapt_legacy


# Connector key for the inbox row — matches the unique idempotency
# index. Lets future direct integrations (e.g. Salla webhook directly)
# coexist with the same `idempotency_key` namespace.
CONNECTOR_KEY = "make_com_qoyod"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Token verification dependency factory
# ─────────────────────────────────────────────────────────────────────
def _make_verify_token(db):
    """Build a FastAPI dependency that captures the DB handle.

    Verification order (matches webhook_token_store.verify_provided_token):
        1) DB-stored token under tenant 'main' (production path)
        2) `QOYOD_WEBHOOK_TOKEN` env var      (preview / CI fallback)

    Raises 401 on mismatch, 503 only when BOTH DB and env are empty
    (a real misconfiguration the operator must fix).
    """
    async def _verify_token(
        x_webhook_token: Optional[str] = Header(
            default=None, alias="X-Webhook-Token"),
    ) -> bool:
        provided = (x_webhook_token or "").strip()
        if not provided:
            raise HTTPException(401, "missing_webhook_token")
        env_fallback = (os.environ.get("QOYOD_WEBHOOK_TOKEN") or "").strip()
        # Short-circuit "no source at all" check so the operator sees
        # the actionable error code immediately.
        db_token_exists = await _db_token_configured(db)
        if not env_fallback and not db_token_exists:
            raise HTTPException(503, "qoyod_webhook_token_not_configured")
        ok = await verify_provided_token(
            db, provided=provided, user_id="main",
            env_fallback=env_fallback or None,
        )
        if not ok:
            raise HTTPException(401, "invalid_webhook_token")
        return True
    return _verify_token


async def _db_token_configured(db) -> bool:
    """Cheap existence probe — used only to distinguish 401 vs 503."""
    doc = await db.qoyod_webhook_tokens.find_one(
        {"user_id": "main", "revoked": {"$ne": True}},
        {"_id": 1})
    return doc is not None


# Legacy alias retained so existing tests that monkey-patch the old
# name keep working until they are migrated to the factory above.
def _verify_token(  # noqa: D401 (kept for backward compat)
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
) -> bool:
    """Backwards-compatible env-only verifier (used by older tests).

    Note: synchronous on purpose — the day-3 test suite calls this
    function directly without awaiting. The production webhook route
    uses `_make_verify_token(db)` (DB-first) instead.
    """
    expected = (os.environ.get("QOYOD_WEBHOOK_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "qoyod_webhook_token_not_configured")
    provided = (x_webhook_token or "").strip()
    if not provided:
        raise HTTPException(401, "missing_webhook_token")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(401, "invalid_webhook_token")
    return True


# ─────────────────────────────────────────────────────────────────────
# Idempotency key extraction
# ─────────────────────────────────────────────────────────────────────
def derive_idempotency_key(raw: dict, header_key: Optional[str]) -> str:
    """Idempotency key resolution order:
        1. Explicit `X-Idempotency-Key` header (Make.com & friends).
        2. `salla:order:<id>:<event>` derived from the payload.
        3. Last resort: random UUID (guarantees insertion).
    """
    if header_key and header_key.strip():
        return header_key.strip()
    data = raw.get("data") if isinstance(raw, dict) else {}
    if not isinstance(data, dict):
        data = {}
    order_id = (data.get("reference_id") or data.get("id")
                or data.get("order_id"))
    event = (raw.get("event") if isinstance(raw, dict) else None) \
            or raw.get("event_type") if isinstance(raw, dict) else None \
            or "order"
    if order_id:
        return f"salla:order:{order_id}:{event}"
    # No key in headers AND no order id → random; will never collide.
    return f"salla:unknown:{uuid.uuid4().hex}"


# ─────────────────────────────────────────────────────────────────────
# Header capture — keep only safe, diagnostic headers
# ─────────────────────────────────────────────────────────────────────
_SAFE_HEADERS = (
    "user-agent", "content-type", "x-event", "x-event-type",
    "x-salla-event", "x-make-trace", "x-request-id",
    "x-idempotency-key", "x-mezan-source",
)


def _capture_headers(req: Request) -> dict[str, str]:
    return {
        h: req.headers.get(h)
        for h in _SAFE_HEADERS if req.headers.get(h)
    }


# ─────────────────────────────────────────────────────────────────────
# Pipeline orchestration (5→7)
# ─────────────────────────────────────────────────────────────────────
async def _apply(db, *, doc_filter: dict, patch: dict) -> None:
    """One-shot helper so each transition is a single line at the call site."""
    await db.integration_inbox.update_one(doc_filter, patch)


async def _process_inbox_row(
    db, *, row: dict, raw_payload: dict,
    adapter_meta: Optional[dict] = None,
) -> tuple[str, Optional[dict]]:
    """Run steps 5→7 against a freshly-inserted inbox row.

    Returns the final `pipeline_stage` and (when failure) the error dict.

    The function is **safe to call** on already-processed rows
    (idempotency guards above): the call sites only invoke it for
    `pipeline_stage == "NEW"` rows.

    `adapter_meta` (when provided) carries the Legacy-Adapter outcome.
    When `items_source == "missing"` the function consults the tenant's
    `enrichment_fallback_enabled` setting:
      • False (default) → FAILED_VALIDATION  (no invoice ever created)
      • True            → NEEDS_ENRICHMENT → FAILED_ENRICHMENT
        (enricher stub: actual Salla-API call is not implemented yet)
    """
    doc_filter = {"id": row["id"]}

    # ── NEW → RECEIVED (raw saved is already in the row) ─────────
    patch = transition(from_stage="NEW", to_stage="RECEIVED",
                       actor="webhook",
                       note="raw payload persisted")
    await _apply(db, doc_filter=doc_filter, patch=patch)
    # Sync the in-memory row dict so later DEAD_LETTER routing can
    # compute pipeline_duration_ms without an extra round-trip.
    started_at = patch.get("$set", {}).get("pipeline_started_at")
    if started_at is not None:
        row["pipeline_started_at"] = started_at

    # ── Items-missing branch (Legacy-Adapter outcome) ────────────
    if adapter_meta and adapter_meta.get("items_source") == "missing":
        return await _handle_missing_items(
            db, row=row, doc_filter=doc_filter, adapter_meta=adapter_meta)

    # ── RECEIVED → VALIDATED (5) ─────────────────────────────────
    ok, err = validate(raw_payload)
    if not ok:
        return await _dead_letter(
            db, doc_filter=doc_filter,
            from_stage="RECEIVED",
            fail_stage="FAILED_VALIDATION",
            error=err,
            started_at=row.get("pipeline_started_at"),
        )
    await _apply(db, doc_filter=doc_filter,
                 patch=transition(from_stage="RECEIVED", to_stage="VALIDATED",
                                  actor="webhook"))

    # ── VALIDATED → NORMALIZED (6+7) ─────────────────────────────
    try:
        dto = normalize(raw_payload, received_at=row.get("received_at"))
    except NormalizationError as ne:
        return await _dead_letter(
            db, doc_filter=doc_filter,
            from_stage="VALIDATED",
            fail_stage="FAILED_NORMALIZATION",
            error=ne.to_log_dict(),
            started_at=row.get("pipeline_started_at"),
        )
    except Exception as exc:   # defensive — never raise out of webhook
        return await _dead_letter(
            db, doc_filter=doc_filter,
            from_stage="VALIDATED",
            fail_stage="FAILED_NORMALIZATION",
            error={"code": "normalizer_crash",
                   "message": f"{exc.__class__.__name__}: {exc}"},
            started_at=row.get("pipeline_started_at"),
        )

    # Persist the canonical DTO + advance the stage.
    canonical = dto.model_dump(mode="json")
    patch = transition(from_stage="VALIDATED", to_stage="NORMALIZED",
                       actor="webhook",
                       note=f"DTO built · {len(canonical['items'])} items")
    patch.setdefault("$set", {}).update({
        "canonical_payload":  canonical,
        "salla_order_id":     canonical["order_id"],
        "salla_order_number": canonical.get("order_number"),
    })
    await _apply(db, doc_filter=doc_filter, patch=patch)

    return ("NORMALIZED", None)


async def _dead_letter(
    db, *, doc_filter: dict, from_stage: str, fail_stage: str,
    error: dict, started_at: Optional[datetime] = None,
) -> tuple[str, dict]:
    """Two-hop transition: `from_stage` → `fail_stage` → `DEAD_LETTER`.

    The first hop records the precise failure (for diagnostics);
    the second hop is the terminal stage that puts the row "in the
    dead-letter bucket" so retries don't happen automatically.
    """
    p1 = transition(from_stage=from_stage, to_stage=fail_stage,
                    actor="webhook", error=error)
    p1.setdefault("$set", {})["pipeline_error"] = error
    await _apply(db, doc_filter=doc_filter, patch=p1)

    p2 = transition(from_stage=fail_stage, to_stage="DEAD_LETTER",
                    actor="webhook",
                    note="auto-routed: validation/normalization failure "
                         "(no retry — manual review required)",
                    existing_started_at=started_at)
    await _apply(db, doc_filter=doc_filter, patch=p2)
    return ("DEAD_LETTER", error)


async def _handle_missing_items(
    db, *, row: dict, doc_filter: dict, adapter_meta: dict,
) -> tuple[str, Optional[dict]]:
    """Branch entered when the Legacy Adapter could not find any line
    items in the incoming payload.

    Behaviour is gated by the per-tenant setting
    `qoyod_settings.enrichment_fallback_enabled`:

      • Default OFF (user policy 2026-06-26):
          RECEIVED → FAILED_VALIDATION
          code = `missing_items_no_enricher`
          → NO invoice ever created. Manual review.

      • Toggle ON:
          RECEIVED → NEEDS_ENRICHMENT → FAILED_ENRICHMENT
          code = `enricher_not_implemented`
          The Salla-API enricher will be wired in a follow-up
          iteration. Until then the row sits in FAILED_ENRICHMENT
          for operator visibility (NOT auto-promoted).

    Either way: `enrichment_fallback_used` is persisted for audit.
    """
    started_at = row.get("pipeline_started_at")
    tenant = row.get("user_id")
    settings = await db.qoyod_settings.find_one(
        {"user_id": tenant}, {"_id": 0, "enrichment_fallback_enabled": 1})
    fallback_enabled = bool(
        (settings or {}).get("enrichment_fallback_enabled", False))

    if not fallback_enabled:
        # Strict path — NEVER create an invoice from an items-missing payload.
        error = {
            "code": "missing_items_no_enricher",
            "message": "payload has no line items and "
                       "enrichment_fallback_enabled is OFF",
            "items_source": adapter_meta.get("items_source"),
            "adapter_applied": adapter_meta.get("adapter_applied"),
        }
        p1 = transition(from_stage="RECEIVED",
                        to_stage="FAILED_VALIDATION",
                        actor="webhook", error=error)
        p1.setdefault("$set", {})["pipeline_error"] = error
        p1["$set"]["enrichment_fallback_used"] = False
        await _apply(db, doc_filter=doc_filter, patch=p1)

        p2 = transition(from_stage="FAILED_VALIDATION",
                        to_stage="DEAD_LETTER",
                        actor="webhook",
                        note="no line items and enricher disabled — "
                             "manual review required",
                        existing_started_at=started_at)
        await _apply(db, doc_filter=doc_filter, patch=p2)
        return ("DEAD_LETTER", error)

    # Toggle ON — enter NEEDS_ENRICHMENT. The actual Salla-API enricher
    # is intentionally NOT yet implemented (per user spec: states first,
    # call later). We transition into NEEDS_ENRICHMENT for visibility
    # then immediately FAILED_ENRICHMENT with a clear `not_implemented`
    # marker so the operator knows what to do next.
    p1 = transition(from_stage="RECEIVED", to_stage="NEEDS_ENRICHMENT",
                    actor="webhook",
                    note="items missing — enricher fallback ENABLED")
    p1.setdefault("$set", {})["enrichment_fallback_used"] = True
    await _apply(db, doc_filter=doc_filter, patch=p1)

    error = {
        "code": "enricher_not_implemented",
        "message": "Salla-API enricher is not yet implemented in this "
                   "iteration (states wired, call pending).",
        "items_source": adapter_meta.get("items_source"),
    }
    p2 = transition(from_stage="NEEDS_ENRICHMENT",
                    to_stage="FAILED_ENRICHMENT",
                    actor="webhook", error=error)
    p2.setdefault("$set", {})["pipeline_error"] = error
    await _apply(db, doc_filter=doc_filter, patch=p2)

    p3 = transition(from_stage="FAILED_ENRICHMENT", to_stage="DEAD_LETTER",
                    actor="webhook",
                    note="enricher stub not implemented yet — manual review",
                    existing_started_at=started_at)
    await _apply(db, doc_filter=doc_filter, patch=p3)
    return ("DEAD_LETTER", error)


# ─────────────────────────────────────────────────────────────────────
# Router attach
# ─────────────────────────────────────────────────────────────────────
def attach_webhook_routes(router: APIRouter, db) -> None:
    """Hook the POST /webhook handler into the Qoyod router.

    Defined as a function so the router factory can call it after
    creating the router — keeps `make_qoyod_router()` short.
    """
    verify_token_dep = _make_verify_token(db)

    @router.post("/webhook")
    async def receive_webhook(
        request: Request,
        body: Any = Body(...),
        x_idempotency_key: Optional[str] = Header(
            default=None, alias="X-Idempotency-Key"),
        _token_ok: bool = Depends(verify_token_dep),
    ):
        # Day 3 ONLY accepts JSON object payloads.
        if not isinstance(body, dict):
            raise HTTPException(400, "payload_must_be_json_object")

        tenant = "main"   # single-tenant MVP per ADR-001 #11
        trace_id = uuid.uuid4().hex
        now = _now()
        idem_key = derive_idempotency_key(body, x_idempotency_key)

        # ── 3b) Legacy-shape Adapter ─────────────────────────────────
        # Make.com (and the legacy /api/webhook/make module) emit a
        # flat JSON contract. Convert to canonical Salla shape BEFORE
        # idempotency lookup or persistence runs. The original raw
        # payload is still kept verbatim in `raw_payload` for audit.
        adapted_body, adapter_meta = adapt_legacy(body)

        # Pull the (best-effort) order anchor for fast lookup later.
        data = adapted_body.get("data") if isinstance(adapted_body.get("data"), dict) else adapted_body
        salla_order_id = (
            data.get("reference_id") or data.get("id") or data.get("order_id"))
        if salla_order_id is not None:
            salla_order_id = str(salla_order_id)

        # ── 4) Save Raw Event (idempotent INSERT) ────────────────────
        row_id = uuid.uuid4().hex
        new_row = {
            "id": row_id,
            "schema_version": 1,
            "user_id": tenant,
            "trace_id": trace_id,
            "connector_key": CONNECTOR_KEY,
            "source": "webhook",
            "received_at": now,
            "raw_payload": body,
            "adapted_payload": adapted_body if adapter_meta["adapter_applied"] else None,
            "adapter_meta": adapter_meta,
            "enrichment_fallback_used": False,
            "raw_headers": _capture_headers(request),
            "signature_status": "verified",
            "salla_order_id": salla_order_id,
            "salla_order_number": str(data.get("reference_id") or data.get("id") or "") or None,
            "idempotency_key": idem_key,
            "pipeline_stage": "NEW",
            "pipeline_error": None,
            "attempts": 0,
            "next_retry_at": None,
            "processed_at": None,
            "canonical_payload": None,
            "stage_history": [
                initial_history_entry(actor="webhook",
                                      note=f"trace_id={trace_id}"
                                            f" · adapter={adapter_meta['adapter_applied']}"
                                            f" · items_source={adapter_meta['items_source']}"),
            ],
        }
        try:
            await db.integration_inbox.insert_one(new_row)
        except DuplicateKeyError:
            # 3) Idempotency: already received — return the existing trace.
            existing = await db.integration_inbox.find_one(
                {"user_id": tenant, "connector_key": CONNECTOR_KEY,
                 "idempotency_key": idem_key},
                {"_id": 0, "id": 1, "trace_id": 1, "pipeline_stage": 1,
                 "received_at": 1, "salla_order_id": 1,
                 "stage_history": {"$slice": -1}},
            )
            return {
                "ok": True,
                "duplicate": True,
                "idempotency_key": idem_key,
                "trace_id": (existing or {}).get("trace_id"),
                "pipeline_stage": (existing or {}).get("pipeline_stage"),
                "salla_order_id": (existing or {}).get("salla_order_id"),
                "received_at": (existing or {}).get("received_at"),
            }

        # ── 5-7) Run validation + normalization synchronously ────────
        final_stage, err = await _process_inbox_row(
            db, row=new_row, raw_payload=adapted_body,
            adapter_meta=adapter_meta)

        # Fetch the updated row's audit fields for the response.
        latest = await db.integration_inbox.find_one(
            {"id": row_id},
            {"_id": 0, "pipeline_stage": 1, "pipeline_started_at": 1,
             "pipeline_finished_at": 1, "pipeline_duration_ms": 1,
             "last_success_stage": 1, "last_failed_stage": 1,
             "canonical_payload": 1, "salla_order_id": 1},
        )

        return {
            "ok":               err is None,
            "duplicate":        False,
            "idempotency_key":  idem_key,
            "trace_id":         trace_id,
            "pipeline_stage":   (latest or {}).get("pipeline_stage", final_stage),
            "salla_order_id":   (latest or {}).get("salla_order_id"),
            "audit": {
                "started_at":  (latest or {}).get("pipeline_started_at"),
                "finished_at": (latest or {}).get("pipeline_finished_at"),
                "duration_ms": (latest or {}).get("pipeline_duration_ms"),
                "last_success_stage": (latest or {}).get("last_success_stage"),
                "last_failed_stage":  (latest or {}).get("last_failed_stage"),
            },
            "error": err,
            "canonical_payload_present": bool((latest or {}).get("canonical_payload")),
        }
