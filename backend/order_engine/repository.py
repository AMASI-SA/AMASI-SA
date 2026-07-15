"""Order Engine repository contracts and Mongo implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class OrderDiscoveryRow:
    order_number: str
    order_date: str
    salla_raw: dict[str, Any]


class OrderRepository(Protocol):
    async def list_salla_orders(
        self,
        *,
        user_id: str,
        limit: int,
        before_order_date: Optional[str] = None,
        before_order_number: Optional[str] = None,
        status_group: Optional[str] = None,
        status_exact: Optional[str] = None,
    ) -> list[OrderDiscoveryRow]: ...

    async def get_salla_order(
        self,
        *,
        user_id: str,
        order_number: str,
    ) -> Optional[OrderDiscoveryRow]: ...


_STATUS_PATTERNS: dict[str, str] = {
    "under_review": r"^(under[_ ]?review|waiting[_ ]?review|pending[_ ]?review|بإنتظار المراجعة|بانتظار المراجعة|انتظار المراجعة)$",
    "reviewed": r"^(reviewed|تمت المراجعة|تم المراجعة)$",
    "processing": r"^(processing|in[_ ]?progress|قيد التنفيذ|جاري التنفيذ)$",
    "completed": r"^(completed|delivered|تم التنفيذ|تم التوصيل)$",
    "shipping": r"^(shipping|shipped|delivering|out[_ ]?for[_ ]?delivery|جاري التوصيل|تم الشحن)$",
    "cancelled": r"^(cancelled|canceled|deleted|ملغي|ملغى|محذوف)$",
    "refunded": r"^(refunded|returned|restored|مسترجع|تم الاسترجاع)$",
}


def _customized_status_expression() -> dict[str, Any]:
    customized = "$raw_by_source.salla_direct.status.customized"
    return {
        "$let": {
            "vars": {"customized": customized},
            "in": {
                "$cond": [
                    {"$eq": [{"$type": "$$customized"}, "object"]},
                    {
                        "$ifNull": [
                            "$$customized.name",
                            {
                                "$ifNull": [
                                    "$$customized.label",
                                    {
                                        "$ifNull": [
                                            "$$customized.title",
                                            "$$customized.slug",
                                        ]
                                    },
                                ]
                            },
                        ]
                    },
                    "$$customized",
                ]
            },
        }
    }


def _effective_status_expression() -> dict[str, Any]:
    return {
        "$ifNull": [
            _customized_status_expression(),
            {
                "$ifNull": [
                    "$order_status",
                    {
                        "$ifNull": [
                            "$raw_by_source.salla_direct.status.name",
                            {
                                "$ifNull": [
                                    "$raw_by_source.salla_direct.status.slug",
                                    "$order_status_slug",
                                ]
                            },
                        ]
                    },
                ]
            },
        ]
    }


def _normalized_status_expression() -> dict[str, Any]:
    return {
        "$toLower": {
            "$trim": {
                "input": {
                    "$replaceAll": {
                        "input": {"$toString": _effective_status_expression()},
                        "find": "_",
                        "replacement": " ",
                    }
                }
            }
        }
    }


class MongoOrderRepository:
    def __init__(self, db: Any):
        self._collection = db.unified_orders

    async def list_salla_orders(
        self,
        *,
        user_id: str,
        limit: int,
        before_order_date: Optional[str] = None,
        before_order_number: Optional[str] = None,
        status_group: Optional[str] = None,
        status_exact: Optional[str] = None,
    ) -> list[OrderDiscoveryRow]:
        query: dict[str, Any] = {
            "user_id": str(user_id),
            "raw_by_source.salla_direct": {"$exists": True},
        }
        and_clauses: list[dict[str, Any]] = []

        exact = " ".join(str(status_exact or "").replace("_", " ").strip().casefold().split())
        if exact:
            and_clauses.append({"$expr": {"$eq": [_normalized_status_expression(), exact]}})
        else:
            pattern = _STATUS_PATTERNS.get(str(status_group or "").strip())
            if pattern:
                and_clauses.append(
                    {
                        "$expr": {
                            "$regexMatch": {
                                "input": {"$toString": _effective_status_expression()},
                                "regex": pattern,
                                "options": "i",
                            }
                        }
                    }
                )

        if before_order_date and before_order_number:
            and_clauses.append(
                {
                    "$or": [
                        {"order_date": {"$lt": before_order_date}},
                        {
                            "order_date": before_order_date,
                            "order_number": {"$lt": before_order_number},
                        },
                    ]
                }
            )

        if and_clauses:
            query["$and"] = and_clauses

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
    def _to_discovery_row(row: Optional[dict[str, Any]]) -> Optional[OrderDiscoveryRow]:
        if not isinstance(row, dict):
            return None
        order_number = str(row.get("order_number") or "").strip()
        order_date = str(row.get("order_date") or "").strip()
        raw_by_source = row.get("raw_by_source")
        if not isinstance(raw_by_source, dict):
            return None
        salla_raw = raw_by_source.get("salla_direct")
        if not isinstance(salla_raw, dict) or not order_number or not order_date:
            return None
        return OrderDiscoveryRow(
            order_number=order_number,
            order_date=order_date,
            salla_raw=salla_raw,
        )