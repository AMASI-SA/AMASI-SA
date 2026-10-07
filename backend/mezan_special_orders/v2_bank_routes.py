"""Unregistered, default-OFF bank collection endpoint for the V2-only adapter.

The existing authentication dependency must be injected by a separately reviewed
application composition. The body cannot supply an owner, account, amount or
posting legs. Persisted identities and controls are rechecked inside the owner
transaction; this module never registers itself or enables financial writing.
"""
from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import ValidationError

from .access import current_actor
from .binding import bound
from .domain import DomainError
from .v2_bank_receipts import CollectReceiptV2, collect_existing_receipt

# Emit owned literals, never arbitrary messages, exception strings or details.
_PUBLIC = frozenset({
    "permission_required", "active_user_required", "order_not_found",
    "revision_conflict", "idempotency_payload_conflict", "order_closed",
    "pending_receipt_claim_required", "bank_movement_already_allocated",
    "bank_receipt_movement_mismatch", "bank_transaction_does_not_match",
    "special_v2_verified_agreement_required", "special_v2_native_receivable_required",
    "special_v2_agreement_reconciliation_required", "special_v2_receivable_balance_insufficient",
    "special_v2_receipt_before_receivable_requires_advance",
    "special_v2_bank_currency_not_yet_supported", "special_v2_receipt_used_by_native_order",
    "special_v2_global_writes_paused", "special_write_epoch_stale",
    "special_order_writes_paused", "accounting_writes_paused", "accounting_period_closed",
    "MZ2_LINK_REQUIRED", "special_v2_actor_scope_mismatch",
})


def public_rejection(exc: Exception) -> HTTPException:
    code = None
    status = 503
    if isinstance(exc, DomainError):
        code, status = exc.code, exc.http_status
    elif isinstance(exc, HTTPException):
        status = exc.status_code
        if type(exc.detail) is dict:
            code = exc.detail.get("code")
    elif isinstance(exc, ValidationError):
        return HTTPException(422, detail={"code": "invalid_special_order_payload"})
    # A string subclass or malformed detail must not execute its own comparison.
    if type(status) is not int or status not in {403, 404, 409, 422, 423}:
        status = 503
    if type(code) is str:
        for literal in _PUBLIC:
            if literal == code:
                return HTTPException(status, detail={"code": literal})
    return HTTPException(status, detail={"code": "special_v2_bank_approval_rejected"})


def make_v2_bank_collection_router(db, current_user) -> APIRouter:
    router = APIRouter(prefix="/special-orders-v1", tags=["Special order V2 bank approval"])
    binding = bound(db)
    if binding is None or not binding.enablement.reads:
        return router

    @router.post("/{order_id}/v2-bank-collections")
    async def collect(order_id: UUID, request: CollectReceiptV2,
                      user=Depends(current_user),
                      idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)):
        try:
            # Tenant is resolved from the saved user, never supplied by the client.
            actor = await current_actor(binding, user)
            return await collect_existing_receipt(binding, tenant_id=actor.tenant_id,
                session_user=user, order_id=str(order_id), request=request,
                idempotency_key=idempotency_key)
        except Exception as exc:
            raise public_rejection(exc) from None

    return router
