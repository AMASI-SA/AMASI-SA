"""Order Engine repository contracts and Mongo implementation.

Architecture
------------
The repository is the only Order Engine layer allowed to know:

- MongoDB collection names
- Mongo query shapes
- `unified_orders`
- `raw_by_source.salla_direct`

Service, routes and frontend must not depend on Mongo document structure.

Sprint 001 uses `unified_orders` only as a temporary discovery bridge.
Authoritative order facts still come from the preserved Salla raw payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class OrderDiscoveryRow:
    """Minimal repository result required by the Order Engine service."""

    order_number: str
    order_date: str
    salla_raw: dict[str, Any]


class OrderRepository(Protocol):
    """Storage-agnostic contract consumed by Order Engine services."""

    async def list_salla_orders(
        self,
        *,
        user_id: str,
        limit: int,
        before_order_date: Optional[str] = None,
        before_order_number: Optional[str] = None,
    ) -> list[OrderDiscoveryRow]:
        """Return newest Salla-backed discovery rows."""

    async def get_salla_order(
        self,
        *,
        user_id: str,
        order_number: str,
    ) -> Optional[OrderDiscoveryRow]:
        """Return one exact Salla-backed discovery row."""


class MongoOrderRepository:
    """Mongo implementation of the OrderRepository contract.

    Read-only by design.

    No insert, update, delete, replace or bulk-write methods belong here
    during Sprint 001.
    """

    def __init__(self, db: Any):
        self._collection = db.unified_orders

    async def list_salla_orders(
        self,
        *,
        user_id: str,
        limit: int,
        before_order_date: Optional[str] = None,
        before_order_number: Optional[str] = None,
    ) -> list[OrderDiscoveryRow]:
        query: dict[str, Any] = {
            "user_id": str(user_id),
            "raw_by_source.salla_direct": {"$exists": True},
        }

        if before_order_date and before_order_number:
            query["$or"] = [
                {"order_date": {"$lt": before_order_date}},
                {
                    "order_date": before_order_date,
                    "order_number": {"$lt": before_order_number},
                },
            ]

        projection = {
            "_id": 0,
            "order_number": 1,
            "order_date": 1,
            "raw_by_source.salla_direct": 1,
        }

        cursor = (
            self._collection.find(query, projection)
            .sort([("order_date", -1), ("order_number", -1)])
            .limit(int(limit))
        )

        rows: list[OrderDiscoveryRow] = []

        async for row in cursor:
            mapped = self._to_discovery_row(row)
            if mapped is not None:
                rows.append(mapped)

        return rows

    async def get_salla_order(
        self,
        *,
        user_id: str,
        order_number: str,
    ) -> Optional[OrderDiscoveryRow]:
        row = await self._collection.find_one(
            {
                "user_id": str(user_id),
                "order_number": str(order_number),
                "raw_by_source.salla_direct": {"$exists": True},
            },
            {
                "_id": 0,
                "order_number": 1,
                "order_date": 1,
                "raw_by_source.salla_direct": 1,
            },
        )

        return self._to_discovery_row(row)

    @staticmethod
    def _to_discovery_row(
        row: Optional[dict[str, Any]],
    ) -> Optional[OrderDiscoveryRow]:
        if not isinstance(row, dict):
            return None

        order_number = str(row.get("order_number") or "").strip()
        order_date = str(row.get("order_date") or "").strip()

        raw_by_source = row.get("raw_by_source")
        if not isinstance(raw_by_source, dict):
            return None

        salla_raw = raw_by_source.get("salla_direct")
        if not isinstance(salla_raw, dict):
            return None

        if not order_number or not order_date:
            return None

        return OrderDiscoveryRow(
            order_number=order_number,
            order_date=order_date,
            salla_raw=salla_raw,
        )
