"""Unmounted internal WhatsApp order-stage route factory.

Dependency gate: caller MUST supply server-configured owner id and trusted
Chatwoot latest-message verifier. This module is not registered by default.
The verifier must fetch the live Chatwoot conversation and prove: account 1,
inbox 2, WhatsApp inbound sender identity, bot assignment, latest incoming
message, no muted/blocked or staff takeover. Never take sender from request.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .repository import MongoOrderRepository
from .service import get_order, OrderNotFoundError
from .whatsapp_stage_projection import stage_fact, StageNotVerified
from .whatsapp_stage_service_auth import (
    verify_stage_service_request, StageServiceUnauthorized,
)

ORDER = re.compile(r"[1-9][0-9]{5,17}\Z", re.ASCII)
MAX_JSON_BYTES = 2048


def make_whatsapp_stage_router(
    *,
    repository: Any,
    fixed_owner_id: str,
    active_keys: Mapping[str, bytes],
    claim_nonce: Callable[[str, int], bool],
    resolve_verified_sender: Callable[[int, int], Awaitable[str]],
    source_freshness: Callable[[Any], Awaitable[bool]],
    now_fn: Callable[[], int] = lambda: int(time.time()),
) -> APIRouter:
    """No dependency here may be populated from ChatGPT/model instructions.

    source_freshness must verify actual upstream Salla source sync, not merely
    DTO.engine_updated_at. claim_nonce must use durable shared atomic storage.
    """
    if (type(fixed_owner_id) is not str or not fixed_owner_id.strip()
            or not active_keys or not callable(claim_nonce)
            or not callable(resolve_verified_sender)
            or not callable(source_freshness)):
        raise ValueError("unconfigured_verified_order_reader")

    router=APIRouter(prefix="/internal/whatsapp/order-stage", tags=["internal-whatsapp"])

    @router.post("/read")
    async def read_order_stage(request: Request) -> dict[str, Any]:
        def denied() -> HTTPException:
            # Uniform public error regardless of unknown order or wrong buyer.
            return HTTPException(status_code=404, detail="verification_required")
        try:
            body=await request.body()
            if len(body)>MAX_JSON_BYTES:
                raise ValueError("oversize")
            await asyncio.to_thread(verify_stage_service_request,
                method=request.method, path=request.url.path, body=body,
                timestamp=request.headers.get("x-amasi-timestamp",""),
                nonce=request.headers.get("x-amasi-nonce",""),
                key_id=request.headers.get("x-amasi-key-id",""),
                signature=request.headers.get("x-amasi-signature",""),
                active_keys=active_keys, claim_nonce=claim_nonce, now=now_fn(),
            )
        except (StageServiceUnauthorized,ValueError):
            raise HTTPException(status_code=401,detail="service_unauthorized") from None

        try:
            data=json.loads(body)
            if type(data) is not dict or set(data)!={"order_number","conversation_id","message_id"}:
                raise ValueError()
            order_number=data["order_number"]
            conversation_id=data["conversation_id"]
            message_id=data["message_id"]
            if (type(order_number) is not str or ORDER.fullmatch(order_number) is None
                    or type(conversation_id) is not int or conversation_id<=0
                    or type(message_id) is not int or message_id<=0):
                raise ValueError()
            sender=await resolve_verified_sender(conversation_id,message_id)
            if type(sender) is not str or not sender:
                raise ValueError()
            # Exact lookup on fixed tenant only. Database index for
            # (user_id,order_number) must be confirmed before registering.
            dto=await get_order(repository,user_id=fixed_owner_id,
                                order_number=order_number)
            if await source_freshness(dto) is not True:
                raise ValueError()
            result=stage_fact(dto,order_number=order_number,
                              verified_sender=sender,
                              source_checked_at=now_fn(),now=now_fn())
            return result
        except (ValueError,TypeError,KeyError,OrderNotFoundError,StageNotVerified):
            raise denied() from None
        # Unexpected dependency outages fail closed and must never return
        # record or exception detail. Operators can count anonymous failures.
        except Exception:
            raise HTTPException(status_code=503,detail="status_unavailable") from None

    return router
