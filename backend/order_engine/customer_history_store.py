"""Mongo read adapter for complete customer-history lookup."""
from __future__ import annotations

import re
from typing import Any, Optional

from .repository import (
    MongoOrderRepository,
    OrderDiscoveryRow,
    _V2_CANONICAL_ROOT_FIELDS,
)


_MOBILE_FIELDS = (
    "customer_mobile",
    "raw_by_source.salla_direct.customer.mobile",
    "raw_by_source.salla_direct.customer.phone",
)
_EMAIL_FIELDS = (
    "customer_email",
    "raw_by_source.salla_direct.customer.email",
)


def _mobile_exact_values(normalized_mobile: str) -> list[Any]:
    local = normalized_mobile[3:]
    national = f"0{local}"
    values: list[Any] = [
        normalized_mobile,
        f"+{normalized_mobile}",
        f"00{normalized_mobile}",
        national,
        local,
    ]
    for value in (normalized_mobile, national, local):
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            pass
    return values


def _mobile_regex(normalized_mobile: str) -> Optional[str]:
    if not re.fullmatch(r"9665\d{8}", str(normalized_mobile or "")):
        return None
    local = normalized_mobile[3:]
    separated_digits = r"\D*".join(re.escape(digit) for digit in local)
    return rf"^\D*(?:(?:\+|00)?966|0)?\D*{separated_digits}\D*$"


class MongoCustomerHistoryStore:
    """Read matching orders across the complete unified-orders collection."""

    def __init__(self, collection: Any):
        self._collection = collection

    async def find_customer_orders(
        self,
        *,
        user_id: str,
        normalized_mobile: Optional[str],
        normalized_email: Optional[str],
        exclude_order_number: str,
    ) -> list[OrderDiscoveryRow]:
        identity_clauses: list[dict[str, Any]] = []

        mobile_pattern = _mobile_regex(str(normalized_mobile or ""))
        if mobile_pattern and normalized_mobile:
            exact_values = _mobile_exact_values(normalized_mobile)
            for field in _MOBILE_FIELDS:
                identity_clauses.append({field: {"$in": exact_values}})
                identity_clauses.append({field: {"$regex": mobile_pattern}})

        email = str(normalized_email or "").strip()
        if email:
            email_pattern = f"^{re.escape(email)}$"
            for field in _EMAIL_FIELDS:
                identity_clauses.append(
                    {field: {"$regex": email_pattern, "$options": "i"}}
                )

        if not identity_clauses:
            return []

        query = {
            "user_id": str(user_id),
            "order_number": {"$ne": str(exclude_order_number)},
            "raw_by_source.salla_direct": {"$exists": True},
            "$or": identity_clauses,
        }
        projection = {
            "_id": 0,
            "order_number": 1,
            "order_date": 1,
            "order_status": 1,
            "raw_by_source.salla_direct": 1,
            **{field: 1 for field in _V2_CANONICAL_ROOT_FIELDS},
        }
        cursor = self._collection.find(query, projection).sort(
            [("order_date", -1), ("order_number", -1)]
        )

        rows: list[OrderDiscoveryRow] = []
        async for row in cursor:
            mapped = MongoOrderRepository._to_discovery_row(row)
            if mapped is not None:
                rows.append(mapped)
        return rows
