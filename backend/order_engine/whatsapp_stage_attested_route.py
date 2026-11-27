"""Opt-in one-way LightNode -> Mezan verified WhatsApp buyer-proof reader.

The pinned Chatwoot AgentBot *on LightNode* must derive the canonical sender
from the current bot-owned ContactInbox/latest inbound message and sign a
purpose-bound buyer proof. An order number, HTTP client or LLM cannot select
the buyer. Mezan never makes an outbound request to private Chatwoot.

No routes are registered merely by importing this module. OFF by default.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .customer_history import normalize_saudi_mobile
from .service import get_order, OrderNotFoundError
from .whatsapp_stage_projection import stage_fact, StageNotVerified
from .whatsapp_stage_service_auth import (
    ATTESTED_PATH, StageServiceUnauthorized, verify_stage_service_request,
)

BUYER_PURPOSE = "amasi.wa.buyer.v1"
VERSION = "amasi_chatwoot_buyer_proof_v1"
ORDER = re.compile(r"[1-9][0-9]{5,17}\Z", re.ASCII)
DIGEST = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
PHONE = re.compile(r"9665[0-9]{8}\Z", re.ASCII)
MAX_JSON_BYTES = 2048
MAX_PROOF_AGE = 60
ALLOWED_KEYS = frozenset((
    "proof_version", "order_number", "conversation_id", "message_id",
    "account_id", "inbox_id", "attested_at", "buyer_proof",
))


def derive_buyer_proof(key: bytes, *, mobile: str, account_id: int,
                       inbox_id: int, conversation_id: int,
                       message_id: int, attested_at: int) -> str:
    """Domain-separated HMAC; raw phone never traverses the Mezan link."""
    if (type(key) is not bytes or len(key) < 32
            or type(mobile) is not str or PHONE.fullmatch(mobile) is None
            or (account_id, inbox_id) != (1, 2)
            or any(type(v) is not int or v <= 0 for v in (
                conversation_id, message_id, attested_at))):
        raise ValueError("invalid_buyer_proof_input")
    material = "\n".join((
        BUYER_PURPOSE, str(account_id), str(inbox_id),
        str(conversation_id), str(message_id), str(attested_at), mobile,
    )).encode("ascii")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def make_whatsapp_attested_stage_router(
    *, repository: Any, fixed_owner_id: str,
    active_keys: Mapping[str, bytes],
    claim_nonce: Callable[[str, int], bool],
    source_freshness: Callable[[Any], Any],
    rate_limit: Callable[[str], bool] | None = None,
    now_fn: Callable[[], int] = lambda: int(time.time()),
) -> APIRouter:
    if (type(fixed_owner_id) is not str or not fixed_owner_id.strip()
            or not active_keys or not callable(claim_nonce)
            or not callable(source_freshness)):
        raise ValueError("unconfigured_attested_stage_reader")

    router = APIRouter(
        prefix="/internal/whatsapp/order-stage", tags=["internal-whatsapp"]
    )

    @router.post("/attested-read")
    async def attested_read(request: Request) -> dict[str, Any]:
        def denied():
            return HTTPException(status_code=404, detail="verification_required")

        try:
            body = await request.body()
            if len(body) > MAX_JSON_BYTES:
                raise ValueError("oversize")
            await asyncio.to_thread(
                verify_stage_service_request,
                method=request.method, path=request.url.path, body=body,
                timestamp=request.headers.get("x-amasi-timestamp", ""),
                nonce=request.headers.get("x-amasi-nonce", ""),
                key_id=request.headers.get("x-amasi-key-id", ""),
                signature=request.headers.get("x-amasi-signature", ""),
                active_keys=active_keys, claim_nonce=claim_nonce,
                expected_path=ATTESTED_PATH, now=now_fn(),
            )
        except (StageServiceUnauthorized, ValueError):
            raise HTTPException(status_code=401, detail="service_unauthorized") from None

        if rate_limit is not None:
            try:
                if await asyncio.to_thread(
                    rate_limit, request.headers.get("x-amasi-key-id", "")
                ) is not True:
                    raise HTTPException(status_code=429, detail="rate_limited")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=503, detail="rate_limit_unavailable") from None

        try:
            data = json.loads(body)
            if type(data) is not dict or set(data) != ALLOWED_KEYS:
                raise ValueError("unexpected_fields")
            if (data["proof_version"] != VERSION
                    or type(data["order_number"]) is not str
                    or ORDER.fullmatch(data["order_number"]) is None
                    or (data["account_id"], data["inbox_id"]) != (1, 2)
                    or any(type(data[k]) is not int or data[k] <= 0
                           for k in ("account_id", "inbox_id",
                                     "conversation_id", "message_id", "attested_at"))
                    or type(data["buyer_proof"]) is not str
                    or DIGEST.fullmatch(data["buyer_proof"]) is None):
                raise ValueError("invalid_proof")
            now = now_fn()
            if (type(now) is not int or data["attested_at"] > now
                    or now - data["attested_at"] > MAX_PROOF_AGE):
                raise ValueError("expired_proof")
            # The previously validated HMAC key ID fixes the attesting service.
            key = active_keys[request.headers["x-amasi-key-id"]]
            dto = await get_order(
                repository, user_id=fixed_owner_id,
                order_number=data["order_number"]
            )
            customer = getattr(dto, "customer", None)
            buyer = normalize_saudi_mobile(getattr(customer, "mobile", None))
            if not buyer:
                raise ValueError("no_verified_buyer")
            expected = derive_buyer_proof(
                key, mobile=buyer,
                account_id=data["account_id"], inbox_id=data["inbox_id"],
                conversation_id=data["conversation_id"],
                message_id=data["message_id"], attested_at=data["attested_at"],
            )
            if not hmac.compare_digest(expected, data["buyer_proof"]):
                raise ValueError("buyer_proof_mismatch")
            if await source_freshness(dto) is not True:
                raise ValueError("stale_source")
            return stage_fact(
                dto, order_number=data["order_number"],
                verified_sender=buyer, source_checked_at=now, now=now,
            )
        except (ValueError, TypeError, KeyError, OrderNotFoundError, StageNotVerified):
            raise denied() from None
        except Exception:
            raise HTTPException(status_code=503, detail="status_unavailable") from None

    return router
