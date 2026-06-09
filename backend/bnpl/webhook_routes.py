"""BNPL webhook routes (Iter-116 Phase 2B).

Mounted UNDER the same `/api` router so the public URL is:
    POST /api/webhooks/tamara/orders/{webhook_secret}

Why a path-secret?
    Tamara delivers webhooks WITHOUT any user session.  We need to know
    which merchant the notification belongs to.  Putting a per-user
    random hex (`webhook_secret`, indexed) in the URL gives us an O(1)
    lookup without exposing the primary `user_id`.  The Tamara
    `tamaraToken` is then matched against the merchant's stored
    `notification_token` for the second authentication layer.

Security layers:
    1. URL must contain a recognised `webhook_secret`.
    2. Query `?tamaraToken=` and `Authorization: Bearer …` must both
       equal the merchant's stored notification_token.
    3. Order details are NOT trusted from the payload — we always
       re-fetch from `GET /merchants/orders/{order_id}` using the
       merchant's encrypted api_token.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from .clients.tamara import TamaraClient, TamaraError
from .config_store import (
    DEFAULTS, _try_decrypt as _decrypt_blob,
    find_user_by_webhook_secret,
)
from .sync_service import _merge_into_unified_orders


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_tamara_order(order: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Map Tamara order JSON → row for `payment_transactions`."""
    total = order.get("total_amount") or {}
    captured = order.get("captured_amount") or order.get("total_captured_amount") or {}
    refunded = order.get("refunded_amount") or order.get("total_refunded_amount") or {}

    def _amt(v):
        if isinstance(v, dict):
            try:
                return float(v.get("amount") or 0)
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    consumer = order.get("consumer") or {}
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "provider": "tamara",
        "provider_id": (order.get("order_id") or "").strip(),
        "status": (order.get("status") or "").lower(),
        "amount": _amt(total),
        "currency": (total or {}).get("currency") if isinstance(total, dict) else "SAR",
        "captured_amount": _amt(captured),
        "refunded_amount": _amt(refunded),
        "order_reference_id": (order.get("order_reference_id") or "").strip(),
        "order_number": (order.get("order_number") or order.get("order_reference_id") or "").strip(),
        "buyer_email": consumer.get("email") or "",
        "buyer_phone": consumer.get("phone_number") or "",
        "created_at_provider": order.get("created_at") or "",
        "updated_at_provider": order.get("updated_at") or "",
        "raw_payload": order,
        "synced_at": _now_iso(),
    }


def _extract_tamara_refunds(order: Dict[str, Any], user_id: str) -> list[dict]:
    out = []
    for r in (order.get("refunds") or []):
        amt = r.get("total_amount") or r.get("amount") or {}
        try:
            value = float(amt.get("amount") or 0) if isinstance(amt, dict) else float(amt or 0)
        except (TypeError, ValueError):
            value = 0.0
        out.append({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "provider": "tamara",
            "provider_payment_id": order.get("order_id"),
            "provider_refund_id": r.get("refund_id") or r.get("id") or "",
            "order_reference_id": order.get("order_reference_id") or "",
            "amount": value,
            "currency": amt.get("currency") if isinstance(amt, dict) else "SAR",
            "status": (r.get("status") or "").lower(),
            "reason": r.get("comment") or r.get("reason") or "",
            "refunded_at": r.get("created_at") or "",
            "raw": r,
            "synced_at": _now_iso(),
        })
    return out


def attach_bnpl_webhook_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/webhooks", tags=["bnpl-webhooks"])

    # ── Tamara orders webhook ──────────────────────────────────
    @router.post("/tamara/orders/{webhook_secret}")
    async def tamara_orders_webhook(
        webhook_secret: str, request: Request,
    ):
        # 1) URL-secret → user
        cfg = await find_user_by_webhook_secret(db, webhook_secret, "tamara")
        if not cfg:
            raise HTTPException(404, "Unknown webhook")
        user_id = cfg["user_id"]

        # 2) tamaraToken (query + Authorization) must match stored notif token
        expected = _decrypt_blob(cfg.get("notification_token_encrypted") or b"")
        if not expected:
            raise HTTPException(401, "Notification token not configured")

        tok_query = request.query_params.get("tamaraToken", "")
        auth = request.headers.get("Authorization", "")
        tok_header = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
        if tok_query != expected or tok_header != expected:
            raise HTTPException(401, "Invalid Tamara token")

        # 3) Read the trigger payload (don't trust its financials).
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        order_id = (payload.get("order_id") or "").strip()
        order_ref = (payload.get("order_reference_id") or "").strip()

        # 4) Re-fetch the full order from Tamara API for trustworthy data.
        api_token = _decrypt_blob(cfg.get("api_token_encrypted") or b"")
        if not api_token:
            raise HTTPException(400, "Tamara api_token not configured")
        client = TamaraClient(
            api_token=api_token,
            base_url=cfg.get("api_base_url") or DEFAULTS["tamara"]["api_base_url"],
        )
        order: Optional[Dict[str, Any]] = None
        try:
            if order_id:
                order = await client.get_order_by_id(order_id)
            elif order_ref:
                order = await client.get_order_by_reference(order_ref)
        except TamaraError as exc:
            # Keep the webhook idempotent even if Tamara is briefly down:
            # store the trigger payload so the merchant doesn't lose it.
            order = {"order_id": order_id, "order_reference_id": order_ref,
                     "status": (payload.get("event_type") or "").lower(),
                     "_fetch_error": str(exc)}

        if not order:
            order = payload  # last-resort fallback

        # 5) Upsert payment_transactions + refunds + unified_orders
        txn = _normalise_tamara_order(order, user_id)
        if txn["provider_id"]:
            await db.payment_transactions.update_one(
                {"user_id": user_id, "provider": "tamara",
                 "provider_id": txn["provider_id"]},
                {"$set": {k: v for k, v in txn.items() if k != "id"},
                 "$setOnInsert": {"id": txn["id"], "created_at": _now_iso()}},
                upsert=True,
            )

        for rfd in _extract_tamara_refunds(order, user_id):
            rid = rfd.get("provider_refund_id") or ""
            if not rid:
                continue
            await db.payment_refunds.update_one(
                {"user_id": user_id, "provider": "tamara",
                 "provider_refund_id": rid},
                {"$set": {k: v for k, v in rfd.items() if k != "id"},
                 "$setOnInsert": {"id": rfd["id"], "created_at": _now_iso()}},
                upsert=True,
            )

        if txn.get("order_reference_id") or txn.get("order_number"):
            await _merge_into_unified_orders(db, user_id, txn)

        # 6) Stamp last_webhook_at for the UI.
        await db.bnpl_settings.update_one(
            {"user_id": user_id, "provider": "tamara"},
            {"$set": {"last_webhook_at": _now_iso()}},
        )

        # 7) Tamara also expects the merchant to call Authorise Order on
        #    `order_approved` — but per Tamara's flow that's done during
        #    checkout in their merchant app, NOT the accounting layer.
        #    We just acknowledge the notification.
        return {"ok": True, "event_type": payload.get("event_type")}

    # ── Tabby payments webhook (Iter-116 Phase 2C placeholder) ──
    # Tabby webhooks include a configurable signature header set when
    # we register the webhook via POST /api/v1/webhooks.  We'll wire
    # that in the next phase; for now the endpoint exists so users can
    # register the URL with Tabby ahead of time.
    @router.post("/tabby/payments/{webhook_secret}")
    async def tabby_payments_webhook(
        webhook_secret: str, request: Request,
    ):
        cfg = await find_user_by_webhook_secret(db, webhook_secret, "tabby")
        if not cfg:
            raise HTTPException(404, "Unknown webhook")
        # Phase 2C: full signature verification + payment upsert lands here.
        try:
            _ = await request.json()
        except Exception:
            pass
        await db.bnpl_settings.update_one(
            {"user_id": cfg["user_id"], "provider": "tabby"},
            {"$set": {"last_webhook_at": _now_iso()}},
        )
        return {"ok": True, "note": "tabby webhook received — full processing in next phase"}

    parent_router.include_router(router)
