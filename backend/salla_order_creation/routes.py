"""Owner-gated Mezan endpoint for confirmed assisted Salla order creation.

No direct model/provider access. Requires a pre-existing trusted, fresh Mezan
quote and Chatwoot customer confirmation record. The required proof-writer
integration is a separate task; without it this endpoint cannot create orders.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from salla_integration.service import call_salla, get_integration
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order, OrderNotFoundError

from .confirmation import ConfirmationDenied, persist_signed_confirmation, verify_confirmation
from .checkout_readback import resolve_synced_checkout, verify_checkout_read
from .http_limits import BodyTooLarge, read_bounded_action_body
from .payload import PendingOrder
from .service import CreationDenied, create_confirmed_salla_order, intent_storage_id, order_digest
from .signature import SignatureDenied, verify_action


class AssistedCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    inbox_id: str = Field(min_length=1, max_length=80)
    conversation_id: str = Field(min_length=1, max_length=80)
    order: PendingOrder


def make_assisted_salla_order_router(
    db: Any, current_user: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/orders-v2/assisted-salla", tags=["assisted-orders"])

    @router.get("/capabilities")
    async def get_capabilities(user: dict = Depends(current_user)) -> dict:
        role = str((user or {}).get("role") or "").strip().lower()
        if not user or (role != "owner" and user.get("is_owner") is not True):
            raise HTTPException(status_code=403, detail={"code": "owner_only"})
        doc = await get_integration(db, str(user["id"]))
        scopes = set(str((doc or {}).get("scope") or "").split())
        return {
            "connected": bool(doc and doc.get("status") == "connected"),
            "orders_create_scope": "orders.read_write" in scopes,
            "catalog_read_scope": bool(
                {"products.read", "products.read_write"} & scopes
            ),
            "shipping_read_scope": bool(
                {"shipping.read", "shipping.read_write"} & scopes
            ),
            "creation_feature_enabled": (
                os.environ.get("MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED", "0") == "1"
            ),
            "proof_writers_integrated": False,
            "note": "Scope is a stored token claim, not live API authorization proof.",
        }

    @router.get("/intents/{intent_id}")
    async def get_intent_status(
        intent_id: str, user: dict = Depends(current_user),
    ) -> dict:
        role = str((user or {}).get("role") or "").strip().lower()
        if not user or (role != "owner" and user.get("is_owner") is not True):
            raise HTTPException(status_code=403, detail={"code": "owner_only"})
        try:
            key = intent_storage_id(str(user["id"]), intent_id)
        except CreationDenied as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code}
            ) from exc
        record = await db.salla_ai_order_intents.find_one({
            "_id": key, "user_id": str(user["id"]),
        })
        if not record:
            raise HTTPException(status_code=404, detail={"code": "not_found"})
        # Never return payload/customer data, secrets or private quote contents.
        return {
            "state": record.get("state"),
            "salla_order_id": record.get("salla_order_id"),
            "reference_id": record.get("reference_id"),
            "needs_reconciliation": record.get("state") == "unknown",
        }

    @router.post("/create-confirmed")
    async def create_confirmed(
        request: AssistedCreateRequest,
        user: dict = Depends(current_user),
    ) -> dict:
        # This owner-only preview does not grant chat agents owner credentials.
        # Later trusted service-to-service access must go through Action Gateway.
        role = str((user or {}).get("role") or "").strip().lower()
        if not user or (role != "owner" and user.get("is_owner") is not True):
            raise HTTPException(status_code=403, detail={"code": "owner_only"})
        try:
            return await create_confirmed_salla_order(
                db,
                user_id=str(user["id"]),
                actor_id=str(user["id"]),
                intent_id=request.intent_id,
                inbox_id=request.inbox_id,
                conversation_id=request.conversation_id,
                order=request.order,
                post_salla=call_salla,
                enabled=(
                    os.environ.get("MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED", "0") == "1"
                ),
            )
        except CreationDenied as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code}
            ) from exc

    @router.post("/signed-confirmation")
    async def signed_confirmation(request: Request) -> dict:
        """Record customer consent without authorizing a Salla POST.

        This endpoint is independently feature-gated, so a pilot can verify
        signed Chatwoot consent safely while order creation remains OFF.
        Only the native worker may sign after a canonical current-message read.
        """
        if os.environ.get("MEZAN_ASSISTED_SALLA_CONFIRMATION_ENABLED", "0") != "1":
            raise HTTPException(status_code=503, detail={"code": "confirmation_disabled"})
        owner_id = str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
        secret = str(os.environ.get("MEZAN_ASSISTED_SALLA_CONFIRMATION_HMAC_SECRET") or "").encode()
        account = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or "")
        inbox = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or "")
        if (not owner_id or len(secret) < 32 or
                not account.isdigit() or not inbox.isdigit()):
            raise HTTPException(status_code=503, detail={"code": "action_scope_not_configured"})
        try:
            raw = await read_bounded_action_body(request)
        except BodyTooLarge as exc:
            raise HTTPException(
                status_code=413, detail={"code": exc.args[0]}
            ) from exc
        delivery = request.headers.get("x-mezan-action-delivery", "")
        try:
            verified = verify_confirmation(
                raw, request.headers.get("x-mezan-action-timestamp", ""),
                delivery,
                request.headers.get("x-mezan-action-signature", ""),
                secret,
            )
        except SignatureDenied as exc:
            raise HTTPException(
                status_code=401, detail={"code": exc.args[0]}
            ) from exc
        try:
            return await persist_signed_confirmation(
                db,
                owner_id=owner_id,
                expected_account_id=int(account),
                expected_inbox_id=int(inbox),
                action=verified,
            )
        except ConfirmationDenied as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.args[0]}
            ) from exc

    @router.post("/signed-checkout")
    async def signed_checkout(request: Request) -> dict:
        """Read a Salla checkout URL already present on the Mezan order.

        No generation, no browser clipboard, no raw customer lookup or order
        number from the caller. Separate read-only capability from Salla POST.
        """
        if os.environ.get("MEZAN_ASSISTED_SALLA_CHECKOUT_READ_ENABLED", "0") != "1":
            raise HTTPException(status_code=503, detail={"code": "checkout_read_disabled"})
        owner_id = str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
        secret = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHECKOUT_HMAC_SECRET") or "").encode()
        account = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or "")
        inbox = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or "")
        host_setting = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHECKOUT_ALLOWED_HOSTS") or "")
        allowed_hosts = frozenset(
            h.strip().lower() for h in host_setting.split(",") if h.strip()
        )
        if (not owner_id or len(secret) < 32 or not account.isdigit()
                or not inbox.isdigit() or not allowed_hosts
                or any("/" in host or ":" in host or "@" in host
                       or host in {"localhost", "127.0.0.1"}
                       for host in allowed_hosts)):
            raise HTTPException(status_code=503, detail={"code": "checkout_scope_not_configured"})
        try:
            raw = await read_bounded_action_body(request)
        except BodyTooLarge as exc:
            raise HTTPException(
                status_code=413, detail={"code": exc.args[0]}
            ) from exc
        try:
            action = verify_checkout_read(
                raw, request.headers.get("x-mezan-action-timestamp", ""),
                request.headers.get("x-mezan-action-delivery", ""),
                request.headers.get("x-mezan-action-signature", ""), secret,
            )
        except SignatureDenied as exc:
            raise HTTPException(
                status_code=401, detail={"code": exc.args[0]}
            ) from exc
        if (action.account_id != int(account)
                or action.inbox_id != int(inbox)):
            raise HTTPException(status_code=403, detail={"code": "outside_approved_inbox"})

        async def read_existing_mezan_order(user_id: str, ref: str):
            try:
                return await get_order(
                    MongoOrderRepository(db), user_id=user_id,
                    order_number=ref,
                )
            except OrderNotFoundError as exc:
                raise LookupError("mezan_order_not_synced") from exc

        return await resolve_synced_checkout(
            db,
            owner_id=owner_id,
            intent_id=action.intent_id,
            inbox_id=str(action.inbox_id),
            conversation_id=str(action.conversation_id),
            quote_ref=action.quote_ref,
            quote_digest=action.quote_digest,
            quote_version=action.quote_version,
            confirmation_message_id=action.confirmation_message_id,
            load_order=read_existing_mezan_order,
            allowed_hosts=allowed_hosts,
        )

    @router.post("/signed-action")
    async def signed_action(request: Request) -> dict:
        """Purpose-bound CSA service call, disabled unless specifically enabled.

        The caller signs ONLY opaque refs. Mezan loads the full verified cart
        from its own quote ledger and validates the independent customer
        confirmation. No Salla token or owner credential goes to the worker.
        """
        if os.environ.get("MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED", "0") != "1":
            raise HTTPException(status_code=503, detail={"code": "creation_disabled"})
        owner_id = str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
        secret = str(os.environ.get("MEZAN_ASSISTED_SALLA_ORDER_HMAC_SECRET") or "").encode()
        configured_account = str(
            os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or ""
        )
        configured_inbox = str(
            os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or ""
        )
        if (not owner_id or len(secret) < 32 or not configured_account.isdigit()
                or not configured_inbox.isdigit()):
            raise HTTPException(status_code=503, detail={"code": "action_scope_not_configured"})
        try:
            raw = await read_bounded_action_body(request)
        except BodyTooLarge as exc:
            raise HTTPException(
                status_code=413, detail={"code": exc.args[0]}
            ) from exc
        delivery = request.headers.get("x-mezan-action-delivery", "")
        try:
            action = verify_action(
                raw,
                request.headers.get("x-mezan-action-timestamp", ""),
                delivery,
                request.headers.get("x-mezan-action-signature", ""),
                secret,
            )
        except SignatureDenied as exc:
            raise HTTPException(status_code=401, detail={"code": exc.args[0]}) from exc
        if (action.account_id != int(configured_account)
                or action.inbox_id != int(configured_inbox)):
            raise HTTPException(status_code=403, detail={"code": "outside_approved_inbox"})

        quote = await db.salla_ai_order_quotes.find_one({
            "user_id": owner_id,
            "intent_id": action.intent_id,
            "inbox_id": str(action.inbox_id),
            "conversation_id": str(action.conversation_id),
            "quote_ref": action.quote_ref,
        })
        if (not quote or quote.get("version") != action.quote_version
                or quote.get("payload_digest") != action.quote_digest):
            raise HTTPException(status_code=409, detail={"code": "quote_reference_not_verified"})
        confirmation = await db.salla_ai_order_confirmations.find_one({
            "user_id": owner_id, "intent_id": action.intent_id,
            "inbox_id": str(action.inbox_id),
            "conversation_id": str(action.conversation_id),
        })
        if (not confirmation or
                str(confirmation.get("message_id")) != str(action.confirmation_message_id)):
            raise HTTPException(status_code=409, detail={"code": "confirmation_reference_missing"})
        try:
            # Full customer/product/address data comes from Mezan storage,
            # never from the AI caller or signed action body.
            order = PendingOrder.model_validate(quote.get("order_payload"))
            if order_digest(order) != action.quote_digest:
                raise ValueError("quote mismatch")
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409, detail={"code": "verified_payload_missing"}) from exc

        # Durable delivery replay fence in Mongo; unique native _id across pods.
        delivery_key = hashlib.sha256(
            (owner_id + ":" + delivery).encode("utf-8")
        ).hexdigest()
        try:
            await db.salla_ai_action_deliveries.insert_one({
                "_id": delivery_key,
                "owner_id": owner_id,
                "intent_id": action.intent_id,
                "digest": action.quote_digest,
                "created_at": datetime.now(timezone.utc),
            })
        except Exception:
            previous = await db.salla_ai_action_deliveries.find_one({
                "_id": delivery_key,
                "owner_id": owner_id,
            })
            if (not previous or previous.get("intent_id") != action.intent_id
                    or previous.get("digest") != action.quote_digest):
                raise HTTPException(status_code=503, detail={"code": "delivery_ledger_busy"})
            return {"state": "replayed_delivery", "order_id": None}

        try:
            return await create_confirmed_salla_order(
                db,
                user_id=owner_id,
                actor_id="signed-customer-service",
                intent_id=action.intent_id,
                inbox_id=str(action.inbox_id),
                conversation_id=str(action.conversation_id),
                order=order,
                post_salla=call_salla,
                enabled=True,
            )
        except CreationDenied as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code}
            ) from exc

    return router
