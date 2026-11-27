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
from .intent_writer import PrepareDenied, prepare_salla_unpaid_order_intent
from .prepare_action import verify_prepare_unpaid_order
from .private_field_action import verify_private_field
from .private_fields import PrivateOrderFieldDenied, save_private_order_field
from .private_fields import require_private_fields_ttl, require_unchanged_private_vault
from .private_vault_action import verify_private_vault_prepare
from .private_order_builder import PrivateOrderAssemblyDenied, prepare_from_private_vault
from .quote_cipher import EncryptedOrderUnavailable, decrypt_order_payload
from .retention import RetentionNotReady, require_assisted_quote_ttl
from .reference_reconciliation import ReferenceUnverified, recover_salla_reference
from .bank_receipt_action import verify_bank_receipt_action
from .bank_receipt_stage import stage_bank_receipt, BankReceiptStageDenied
from .bank_review_readback import read_synced_bank_review
from .bank_status_guard import BankStatusDenied, ensure_bank_review_custom_status
from .service import intent_storage_id
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


class AssistedPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    inbox_id: str = Field(min_length=1, max_length=80)
    conversation_id: str = Field(min_length=1, max_length=80)
    selected_rate_id: int = Field(gt=0)
    order: PendingOrder


def make_assisted_salla_order_router(
    db: Any, current_user: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/orders-v2/assisted-salla", tags=["assisted-orders"])

    @router.post("/signed-bank-receipt")
    async def signed_bank_receipt(request: Request) -> dict:
        """Stage only an apparent bank receipt, never mark order paid.

        The original image stays in protected Chatwoot. This route accepts
        canonical image/message metadata and limited preliminary vision
        fields from the separate trusted native bot service. It compares
        against a current active Salla customer-transfer bank.
        """
        if os.environ.get("MEZAN_ASSISTED_SALLA_BANK_RECEIPT_ENABLED", "0") != "1":
            raise HTTPException(status_code=503,detail={"code":"bank_receipt_disabled"})
        owner_id=str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
        secret=str(os.environ.get("MEZAN_ASSISTED_SALLA_BANK_RECEIPT_HMAC_SECRET") or "").encode()
        reference_secret=str(
            os.environ.get("MEZAN_ASSISTED_SALLA_BANK_REFERENCE_HMAC_SECRET") or ""
        ).encode()
        account=str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or "")
        inbox=str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or "")
        if (not owner_id or len(secret)<32 or len(reference_secret)<32
                or not account.isdigit() or not inbox.isdigit()):
            raise HTTPException(status_code=503,detail={"code":"bank_receipt_scope_not_configured"})
        try:
            raw=await read_bounded_action_body(request)
        except BodyTooLarge as exc:
            raise HTTPException(status_code=413,detail={"code":exc.args[0]}) from exc
        try:
            action=verify_bank_receipt_action(
                raw,request.headers.get("x-mezan-action-timestamp",""),
                request.headers.get("x-mezan-action-delivery",""),
                request.headers.get("x-mezan-action-signature",""),secret,
            )
        except SignatureDenied as exc:
            raise HTTPException(status_code=401,detail={"code":exc.args[0]}) from exc
        if (action.account_id!=int(account) or action.inbox_id!=int(inbox)):
            raise HTTPException(status_code=403,detail={"code":"outside_approved_inbox"})
        try:
            return await stage_bank_receipt(
                db,user_id=owner_id,intent_id=action.intent_id,
                account_id=action.account_id,inbox_id=action.inbox_id,
                conversation_id=action.conversation_id,
                contact_id=action.source_contact_id,
                message_id=action.source_message_id,
                attachment_id=action.source_attachment_id,
                source_id_sha256=action.source_id_sha256,
                selected_bank_id=action.selected_bank_id,
                visible=action.visible,reference_secret=reference_secret,
                enabled=True,
            )
        except BankReceiptStageDenied as exc:
            raise HTTPException(status_code=409,detail={"code":str(exc)}) from exc

    @router.post("/signed-private-field")
    async def signed_private_field(request: Request) -> dict:
        """Store one current public customer field encrypted in Mezan only.

        Separate purpose HMAC, no model access and no Salla POST. This endpoint
        cannot create a quote or send a payment URL. Off by default.
        """
        if os.environ.get("MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_ENABLED", "0") != "1":
            raise HTTPException(status_code=503, detail={"code": "private_fields_disabled"})
        owner_id = str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
        secret = str(os.environ.get("MEZAN_ASSISTED_SALLA_FIELD_HMAC_SECRET") or "").encode()
        account = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or "")
        inbox = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or "")
        if (not owner_id or len(secret) < 32 or not account.isdigit()
                or not inbox.isdigit()):
            raise HTTPException(status_code=503, detail={"code": "field_scope_not_configured"})
        try:
            raw = await read_bounded_action_body(request)
        except BodyTooLarge as exc:
            raise HTTPException(status_code=413, detail={"code": exc.args[0]}) from exc
        try:
            action = verify_private_field(
                raw, request.headers.get("x-mezan-action-timestamp", ""),
                request.headers.get("x-mezan-action-delivery", ""),
                request.headers.get("x-mezan-action-signature", ""), secret,
            )
        except SignatureDenied as exc:
            raise HTTPException(status_code=401, detail={"code": exc.args[0]}) from exc
        if (action.account_id != int(account)
                or action.inbox_id != int(inbox)):
            raise HTTPException(status_code=403, detail={"code": "outside_approved_inbox"})
        try:
            return await save_private_order_field(
                db, owner_id=owner_id,
                account_id=action.account_id, inbox_id=action.inbox_id,
                conversation_id=action.conversation_id,
                intent_id=action.intent_id,
                contact_id=action.source_contact_id,
                message_id=action.source_message_id,
                field=action.field, value=action.value,
            )
        except PrivateOrderFieldDenied as exc:
            raise HTTPException(
                status_code=409, detail={"code": str(exc)}
            ) from exc

    @router.post("/signed-prepare-private")
    async def signed_prepare_private(request: Request) -> dict:
        """Server-only encrypted field vault → validated Salla unpaid quote.

        The signed caller never supplies buyer phone/name/email/address.
        It only names a verified Chatwoot contact + Salla catalog selections.
        This endpoint is OFF until private field storage, quote TTL and the
        Salla read scopes are explicitly enabled by the merchant owner.
        """
        if (os.environ.get("MEZAN_ASSISTED_SALLA_SIGNED_PREPARE_ENABLED", "0") != "1" or
                os.environ.get("MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_ENABLED", "0") != "1" or
                os.environ.get("MEZAN_ASSISTED_SALLA_QUOTE_WRITE_ENABLED", "0") != "1"):
            raise HTTPException(status_code=503, detail={"code":"private_prepare_disabled"})
        owner_id=str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
        secret=str(os.environ.get("MEZAN_ASSISTED_SALLA_PREPARE_VAULT_HMAC_SECRET") or "").encode()
        account=str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or "")
        inbox=str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or "")
        if (not owner_id or len(secret)<32 or not account.isdigit()
                or not inbox.isdigit()):
            raise HTTPException(status_code=503,detail={"code":"private_prepare_scope_not_configured"})
        try:
            raw=await read_bounded_action_body(request)
        except BodyTooLarge as exc:
            raise HTTPException(status_code=413,detail={"code":exc.args[0]}) from exc
        try:
            action=verify_private_vault_prepare(
                raw,request.headers.get("x-mezan-action-timestamp",""),
                request.headers.get("x-mezan-action-delivery",""),
                request.headers.get("x-mezan-action-signature",""),secret,
            )
        except SignatureDenied as exc:
            raise HTTPException(status_code=401,detail={"code":exc.args[0]}) from exc
        if (action.account_id!=int(account) or action.inbox_id!=int(inbox)):
            raise HTTPException(status_code=403,detail={"code":"outside_approved_inbox"})
        try:
            await require_assisted_quote_ttl(db)
            await require_private_fields_ttl(db)
            return await prepare_from_private_vault(
                db,owner_id=owner_id,
                account_id=action.account_id,inbox_id=action.inbox_id,
                conversation_id=action.conversation_id,
                intent_id=action.intent_id,contact_id=action.source_contact_id,
                current_message_id=action.source_message_id,
                receiver_country_code=action.receiver_country_code,
                country_id=action.country_id,city_id=action.city_id,
                district_id=action.district_id,courier_id=action.courier_id,
                selected_rate_id=action.selected_rate_id,
                products=[p.model_dump(exclude_none=True) for p in action.products],
                accepted_methods=list(action.accepted_methods),
                branch_id=action.branch_id,
                enabled=True,
            )
        except (RetentionNotReady,PrivateOrderFieldDenied,PrivateOrderAssemblyDenied,
                PrepareDenied) as exc:
            raise HTTPException(status_code=409,detail={"code":str(exc)}) from exc

    @router.post("/signed-prepare")
    async def signed_prepare(request: Request) -> dict:
        """Authorize trusted Chatwoot service to prepare an UNPAID order.

        This never POSTs to Salla. All catalog, carrier and payment-method
        facts are re-read by Mezan. A separate verified inbound customer
        confirmation and an independent create-order permission are required
        before the real order is created.
        """
        if os.environ.get("MEZAN_ASSISTED_SALLA_SIGNED_PREPARE_ENABLED", "0") != "1":
            raise HTTPException(status_code=503, detail={"code": "signed_prepare_disabled"})
        owner_id = str(os.environ.get("MEZAN_ASSISTED_SALLA_OWNER_ID") or "").strip()
        secret = str(os.environ.get("MEZAN_ASSISTED_SALLA_PREPARE_HMAC_SECRET") or "").encode()
        account = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT") or "")
        inbox = str(os.environ.get("MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX") or "")
        if (not owner_id or len(secret) < 32 or not account.isdigit()
                or not inbox.isdigit()):
            raise HTTPException(status_code=503, detail={"code": "prepare_scope_not_configured"})
        try:
            raw = await read_bounded_action_body(request)
        except BodyTooLarge as exc:
            raise HTTPException(
                status_code=413, detail={"code": exc.args[0]}
            ) from exc
        try:
            verified = verify_prepare_unpaid_order(
                raw,
                request.headers.get("x-mezan-action-timestamp", ""),
                request.headers.get("x-mezan-action-delivery", ""),
                request.headers.get("x-mezan-action-signature", ""),
                secret,
            )
        except SignatureDenied as exc:
            raise HTTPException(status_code=401, detail={"code": exc.args[0]}) from exc
        if (verified.account_id != int(account)
                or verified.inbox_id != int(inbox)):
            raise HTTPException(status_code=403, detail={"code": "outside_approved_inbox"})
        try:
            await require_assisted_quote_ttl(db)
        except RetentionNotReady as exc:
            raise HTTPException(
                status_code=503, detail={"code": str(exc)}
            ) from exc
        try:
            return await prepare_salla_unpaid_order_intent(
                db,
                user_id=owner_id, intent_id=verified.intent_id,
                inbox_id=str(verified.inbox_id),
                conversation_id=str(verified.conversation_id),
                source_message_id=verified.source_message_id,
                source_contact_id=verified.source_contact_id,
                order=verified.order,
                selected_rate_id=verified.selected_rate_id,
                enabled=(
                    os.environ.get("MEZAN_ASSISTED_SALLA_QUOTE_WRITE_ENABLED", "0")
                    == "1"
                ),
            )
        except PrepareDenied as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": str(exc)}
            ) from exc

    @router.post("/prepare-unpaid-intent")
    async def prepare_unpaid_order_intent(
        request: AssistedPrepareRequest,
        user: dict = Depends(current_user),
    ) -> dict:
        """Owner-only synthetic pilot: verify Salla cart inputs BEFORE order.

        Creation and checkout are separately gated. Do not give owner auth
        to CSA agents. Production chat integration requires a scoped service
        endpoint and signed contact/recipient identity proof.
        """
        role = str((user or {}).get("role") or "").strip().lower()
        if not user or (role != "owner" and user.get("is_owner") is not True):
            raise HTTPException(status_code=403, detail={"code": "owner_only"})
        try:
            await require_assisted_quote_ttl(db)
        except RetentionNotReady as exc:
            raise HTTPException(
                status_code=503, detail={"code": str(exc)}
            ) from exc
        try:
            return await prepare_salla_unpaid_order_intent(
                db,
                user_id=str(user["id"]),
                intent_id=request.intent_id,
                inbox_id=request.inbox_id,
                conversation_id=request.conversation_id,
                order=request.order,
                selected_rate_id=request.selected_rate_id,
                enabled=(
                    os.environ.get("MEZAN_ASSISTED_SALLA_QUOTE_WRITE_ENABLED", "0")
                    == "1"
                ),
            )
        except PrepareDenied as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": str(exc)}
            ) from exc

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

    @router.post("/bank-review/{intent_id}/ensure-under-review")
    async def ensure_bank_status_owner_pilot(
        intent_id: str, user: dict = Depends(current_user),
    ) -> dict:
        """Explicit owner-only, default-OFF Salla custom status update pilot.

        Never update payment.status, Qoyod, GL or fulfillment here. Multiple
        custom statuses share under_review slug; select the exact active
        «بإنتظار المراجعة» target from Salla before at most one status POST.
        """
        role=str((user or {}).get("role") or "").strip().lower()
        if not user or (role!="owner" and user.get("is_owner") is not True):
            raise HTTPException(status_code=403,detail={"code":"owner_only"})
        if os.environ.get("MEZAN_ASSISTED_SALLA_BANK_SET_REVIEW_STATUS_ENABLED","0")!="1":
            raise HTTPException(status_code=503,detail={"code":"bank_status_update_disabled"})
        owner_id=str(user["id"])
        try:
            key=intent_storage_id(owner_id,intent_id)
        except Exception:
            raise HTTPException(status_code=422,detail={"code":"invalid_bank_intent"}) from None
        record=await db.salla_ai_order_intents.find_one({
            "_id":key,"user_id":owner_id,"intent_id":intent_id,
            "pricing_mode":"salla_bank_review",
        })
        if not isinstance(record,dict) or type(record.get("salla_order_id")) is not int:
            raise HTTPException(status_code=404,detail={"code":"verified_bank_order_missing"})
        try:
            return await ensure_bank_review_custom_status(
                db,user_id=owner_id,intent_id=intent_id,
                salla_order_id=record["salla_order_id"],
                request=call_salla,enabled=True,
            )
        except BankStatusDenied as exc:
            raise HTTPException(status_code=409,detail={"code":str(exc)}) from exc

    @router.get("/bank-review/{intent_id}")
    async def get_bank_review_readback(
        intent_id: str, user: dict = Depends(current_user),
    ) -> dict:
        """Read-only bank review facts for authorized Mezan owner pilot.

        Does NOT approve/mark paid; the customer-service reviewer must check
        real bank credit independently before any financial or fulfillment
        action. Broader staff ACL/UI requires separate owner-approved scope.
        """
        role=str((user or {}).get("role") or "").strip().lower()
        if not user or (role!="owner" and user.get("is_owner") is not True):
            raise HTTPException(status_code=403,detail={"code":"owner_only"})
        if os.environ.get("MEZAN_ASSISTED_SALLA_BANK_REVIEW_READ_ENABLED","0") != "1":
            raise HTTPException(status_code=503,detail={"code":"bank_review_read_disabled"})

        async def load_current_order(owner_id: str, reference: str):
            try:
                return await get_order(
                    MongoOrderRepository(db),user_id=owner_id,
                    order_number=reference,
                )
            except OrderNotFoundError as exc:
                raise LookupError("mezan_bank_order_not_synced") from exc

        try:
            return await read_synced_bank_review(
                db,user_id=str(user["id"]),
                intent_id=intent_id,load_order=load_current_order,
            )
        except Exception:
            # Do not print bank identifiers or private Chatwoot content.
            raise HTTPException(
                status_code=503,
                detail={"code":"bank_review_readback_unavailable"},
            ) from None

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

        # An API-created order can have a numeric Salla ID before the
        # human-facing order reference is present in the initial response.
        # Recover that reference using an authenticated Salla GET only.
        # This never makes a second Salla POST or changes payment state.
        try:
            recovery = await recover_salla_reference(
                db, user_id=owner_id, intent_id=action.intent_id,
                provider_get=call_salla,
            )
        except ReferenceUnverified:
            return {
                "state": "order_reference_unverified",
                "checkout_url": None, "amount_verified": False,
            }
        if recovery["state"] == "reference_not_yet_available":
            return {
                "state": "waiting_for_salla_reference",
                "checkout_url": None, "amount_verified": False,
            }

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
        # If quote preparation read the encrypted private customer vault,
        # prevent a different edit/revision from being submitted even if
        # another worker changes fields concurrently after initial validation.
        if quote.get("private_vault_revision") is not None:
            try:
                await require_unchanged_private_vault(
                    db, owner_id=owner_id,
                    account_id=action.account_id,
                    inbox_id=action.inbox_id,
                    conversation_id=action.conversation_id,
                    intent_id=action.intent_id,
                    contact_id=quote.get("source_contact_id"),
                    expected_revision=quote["private_vault_revision"],
                    expected_vault_ref=quote.get("private_vault_ref"),
                )
            except PrivateOrderFieldDenied:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "private_order_fields_changed_or_expired"},
                ) from None
        try:
            # Full customer/product/address data comes from Mezan storage,
            # never from the AI caller or signed action body.
            # New source records never persist raw customer/order PII.
            # Transitional legacy authoritative quotes are supported only by
            # the explicit older source; new unpriced quotes MUST be encrypted.
            if quote.get("source") == "mezan_authoritative_quote_v1":
                order = PendingOrder.model_validate(quote.get("order_payload"))
            else:
                order = decrypt_order_payload(
                    quote.get("encrypted_order_payload"),
                    owner_id=owner_id, intent_id=action.intent_id,
                )
            if order_digest(order) != action.quote_digest:
                raise ValueError("quote mismatch")
        except (EncryptedOrderUnavailable, ValueError, TypeError):
            raise HTTPException(status_code=409, detail={"code": "verified_payload_missing"}) from None

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
