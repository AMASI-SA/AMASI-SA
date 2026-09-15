"""Isolated V3 snapshot persistence used only for parity evaluation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from .compatibility import build_compatibility_order
from .gateway import SallaOrdersGateway
from .merge_policy import decide_shadow_merge
from .normalizer import normalize_order_items


COLLECTION = "salla_orders_v3_shadow"


def _text(value: Any) -> str:
    return str(value or "").strip()


class SallaOrdersShadowEngine:
    def __init__(
        self,
        db: Any,
        *,
        gateway: Optional[SallaOrdersGateway] = None,
    ) -> None:
        self.db = db
        self.gateway = gateway or SallaOrdersGateway(db)

    async def sync_order(
        self,
        *,
        user_id: str,
        store_id: str,
        light_order: dict[str, Any],
        fetch_details: bool = False,
        event_created_at: Any = None,
    ) -> dict[str, Any]:
        internal_id = _text(light_order.get("id"))
        order_number = _text(
            light_order.get("reference_id") or light_order.get("order_number")
        )
        if not internal_id or not order_number:
            return {"ok": False, "error": "missing_order_identity"}

        key = f"{_text(user_id)}:{_text(store_id)}:{internal_id}"
        collection = getattr(self.db, COLLECTION)
        base = deepcopy(light_order)
        if fetch_details:
            details = await self.gateway.get_light_order_details(user_id, internal_id)
            base.update(details)
            base["id"] = details.get("id") or internal_id
            base["reference_id"] = details.get("reference_id") or order_number

        fetched_at = datetime.now(timezone.utc).isoformat()
        items_error = None
        items_payload_valid = False
        status = "failed"
        normalized_items = []
        try:
            raw_items = await self.gateway.get_order_items(user_id, internal_id)
            normalized_items = normalize_order_items(
                raw_items,
                order_number=order_number,
            )
            items_payload_valid = True
            status = "succeeded"
        except Exception as exc:
            items_error = type(exc).__name__

        candidate = build_compatibility_order(
            base,
            normalized_items=normalized_items,
            items_sync_status=status,
            items_payload_valid=items_payload_valid,
            items_sync_error=items_error,
            items_synced_at=fetched_at,
            event_created_at=event_created_at,
            sync_revision=1,
        )
        now = datetime.now(timezone.utc)
        persisted = False
        for _attempt in range(3):
            latest_row = await collection.find_one({"_id": key}) or {}
            latest_order = latest_row.get("compatibility_order") or {}
            expected_revision = int(latest_order.get("sync_revision") or 0)
            merged = decide_shadow_merge(
                latest_order,
                candidate,
                items_sync_status=status,
                items_payload_valid=items_payload_valid,
            )
            revision_filter: Any = expected_revision
            if not latest_row:
                revision_filter = {"$exists": False}
            try:
                write_result = await collection.update_one(
                    {
                        "_id": key,
                        "compatibility_order.sync_revision": revision_filter,
                    },
                    {
                        "$setOnInsert": {
                            "_id": key,
                            "created_at": now,
                        },
                        "$set": {
                            "user_id": _text(user_id),
                            "store_id": _text(store_id),
                            "internal_order_id": internal_id,
                            "order_number": order_number,
                            "compatibility_order": merged,
                            "shadow_only": True,
                            "excluded_from_operational_reads": True,
                            "updated_at": now,
                        },
                    },
                    upsert=not bool(latest_row),
                )
            except DuplicateKeyError:
                continue
            persisted = bool(
                getattr(write_result, "matched_count", 0)
                or getattr(write_result, "upserted_id", None) is not None
            )
            if persisted:
                break

        if not persisted:
            return {
                "ok": False,
                "error": "concurrent_update_conflict",
                "order_number": order_number,
                "internal_order_id": internal_id,
                "items_sync_status": status,
                "items_payload_valid": items_payload_valid,
                "shadow_only": True,
            }
        return {
            "ok": True,
            "order_number": order_number,
            "internal_order_id": internal_id,
            "items_sync_status": status,
            "items_payload_valid": items_payload_valid,
            "items_count": len(normalized_items) if items_payload_valid else None,
            "shadow_only": True,
        }
