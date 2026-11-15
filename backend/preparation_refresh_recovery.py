"""Guarded local preparation continuation after an explicit source refresh."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from fastapi import HTTPException


LOGGER = logging.getLogger(__name__)


async def recover_preparation_after_refresh(
    db: Any, *, user_id: str, order_number: str, actor: dict,
    result: dict, authorize: Callable[[Any], Awaitable[Any]],
) -> dict:
    """Keep source-refresh evidence separate from a pending local transition.

    Only explicit callers use this seam. Cached, stale, failed, newly created,
    or differently scoped source results cannot trigger preparation work.
    The existing reconciliation proves approval, allocation and current source
    again; its provider dispatch is permanently disabled for this continuation.
    """
    if not (
        result.get("ok") is True and result.get("found") is True
        and result.get("updated") is True and result.get("created") is False
        and result.get("skipped") is False and not result.get("blocked")
        and result.get("order_number") == order_number
        and result.get("source") == "orders_v2_central_salla_refresh"
        and result.get("refreshed_at")
    ):
        return result

    await authorize(db)
    try:
        from review_direct_preparation_sync import reconcile_direct_preparation_after_review
        return await reconcile_direct_preparation_after_review(
            db, user_id=user_id, order_number=order_number, actor=actor,
            result=result, allow_dispatch=False, authorize=authorize,
        )
    except Exception as failure:
        if isinstance(failure, HTTPException) and failure.status_code in {401, 403}:
            raise
        reason = (failure.detail.get("code")
                  if isinstance(failure, HTTPException) and isinstance(failure.detail, dict)
                  else type(failure).__name__)
        LOGGER.warning("preparation_refresh_recovery_pending reason=%s", reason)
        return {**result, "preparation_status_sync": {
            "state": "pending", "retry_post": False, "reconcile": "get_only",
            "reason": reason,
        }}
    finally:
        # Also fence no-op/pending results and the already-confirmed fast path.
        # A revoked actor must never receive a successful continuation reply.
        await authorize(db)
