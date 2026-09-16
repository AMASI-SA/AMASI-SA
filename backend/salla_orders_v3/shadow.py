"""Pure Salla V3 fetch/normalization used by the fenced queue worker."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .compatibility import build_compatibility_order
from .gateway import SallaOrdersGateway
from .normalizer import normalize_order_items


def _text(value: Any) -> str:
    return str(value or "").strip()


class SallaOrdersShadowEngine:
    """Build a candidate snapshot without performing any persistence."""

    def __init__(
        self,
        db: Any,
        *,
        gateway: Optional[SallaOrdersGateway] = None,
    ) -> None:
        self.db = db
        self.gateway = gateway or SallaOrdersGateway(db)

    async def prepare_order_snapshot(
        self,
        *,
        user_id: str,
        store_id: str,
        light_order: dict[str, Any],
        event_created_at: Any = None,
        signal_revision: int = 1,
        before_items: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> dict[str, Any]:
        """Fetch and normalize one order; the caller owns the write fence."""
        internal_id = _text(light_order.get("id") or light_order.get("order_id"))
        order_number = _text(
            light_order.get("reference_id") or light_order.get("order_number")
        )
        if not internal_id or not order_number:
            return {"ok": False, "error": "missing_order_identity"}

        details = await self.gateway.get_light_order_details(user_id, internal_id)
        details_internal_id = _text(details.get("id") or details.get("order_id"))
        details_order_number = _text(
            details.get("reference_id") or details.get("order_number")
        )
        if details_internal_id and details_internal_id != internal_id:
            raise RuntimeError("Salla Order Details internal identity mismatch")
        if details_order_number and details_order_number != order_number:
            raise RuntimeError("Salla Order Details reference identity mismatch")
        # The Light webhook/list row is only a scheduling signal. Details is
        # always fetched and is the sole canonical order snapshot; do not
        # retain omitted or explicitly cleared fields from another revision.
        base = deepcopy(details)
        base["id"] = details_internal_id or internal_id
        base["reference_id"] = details_order_number or order_number

        fetched_at = datetime.now(timezone.utc).isoformat()
        items_error = None
        items_payload_valid = False
        status = "failed"
        normalized_items: list[dict[str, Any]] = []
        try:
            if before_items is not None and await before_items() is not True:
                raise RuntimeError("Salla V3 lease lost before Order Items")
            raw_items = await self.gateway.get_order_items(user_id, internal_id)
            normalized_items = normalize_order_items(
                raw_items,
                order_number=order_number,
            )
            items_payload_valid = True
            status = "succeeded"
        except Exception as exc:
            items_error = type(exc).__name__

        revision = max(1, int(signal_revision))
        candidate = build_compatibility_order(
            base,
            normalized_items=normalized_items,
            items_sync_status=status,
            items_payload_valid=items_payload_valid,
            items_sync_error=items_error,
            items_synced_at=fetched_at if items_payload_valid else None,
            items_attempted_at=fetched_at,
            event_created_at=event_created_at,
            sync_revision=revision,
        )
        candidate["signal_revision"] = revision
        candidate["items_success_signal_revision"] = (
            revision if items_payload_valid else None
        )
        return {
            "ok": True,
            "order_number": order_number,
            "internal_order_id": internal_id,
            "items_sync_status": status,
            "items_payload_valid": items_payload_valid,
            "items_count": len(normalized_items) if items_payload_valid else None,
            "error_type": items_error,
            "compatibility_order": candidate,
            "shadow_only": True,
        }

    async def sync_order(self, **kwargs: Any) -> dict[str, Any]:
        """Compatibility alias; remains pure and performs no database write."""
        return await self.prepare_order_snapshot(**kwargs)
