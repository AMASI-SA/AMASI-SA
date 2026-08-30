"""Order Engine repository contracts and Mongo implementation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional, Protocol


_REVIEW_PARENT_VALUES = [
    "under review",
    "waiting review",
    "pending review",
    "بإنتظار المراجعة",
    "بانتظار المراجعة",
    "انتظار المراجعة",
]


@dataclass(frozen=True)
class OrderDiscoveryRow:
    order_number: str
    order_date: str
    salla_raw: dict[str, Any]
    current_status: Optional[str] = None


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

    async def get_salla_orders(
        self,
        *,
        user_id: str,
        order_numbers: list[str],
    ) -> list[OrderDiscoveryRow]: ...


_STATUS_PATTERNS: dict[str, str] = {
    "under_review": r"^(under[_ ]?review|waiting[_ ]?review|pending[_ ]?review|بإنتظار المراجعة|بانتظار المراجعة|انتظار المراجعة)$",
    "reviewed": r"^(reviewed|تمت المراجعة|تم المراجعة)$",
    "processing": r"^(processing|in[_ ]?progress|قيد التنفيذ|جاري التنفيذ)$",
    "completed": r"^(completed|delivered|تم التنفيذ|تم التوصيل)$",
    "shipping": r"^(shipping|shipped|delivering|out[_ ]?for[_ ]?delivery|جاري التوصيل|تم الشحن)$",
    "cancelled": r"^(cancelled|canceled|deleted|ملغي|ملغى|محذوف)$",
    "refunded": r"^(refunded|returned|restored|مسترجع|تم الاسترجاع)$",
}


_ATTRIBUTION_FIELDS = (
    "source",
    "source_native",
    "order_source",
    "traffic_source",
    "marketing_source",
    "ad_platform_source",
    "source_name",
    "channel",
    "platform",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "campaign_id",
    "campaign_name",
    "ad_squad_id",
    "ad_squad_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "sc_click_id",
    "sc_cookie1",
    "fbclid",
    "gclid",
    "ttclid",
    "source_campaign_id",
    "source_campaign_name",
    "device",
    "is_gift",
    "gift",
    "gift_order",
    "order_type",
    "type",
    "mezan_read_at",
)

# Durable normalized fields owned by the V2 Order Engine read model.  A modern
# Salla Order Details response can be intentionally light and omit shipping
# objects.  `orders_db` preserves these root fields from richer webhooks, so V2
# rehydrates the provider-shaped read payload from them without calling legacy
# pages or mutating storage.
_V2_CANONICAL_ROOT_FIELDS = tuple(dict.fromkeys((
    *_ATTRIBUTION_FIELDS,
    "customer_name",
    "customer_mobile",
    "payment_method",
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
    "receiving_bank_name",
    "payment_receipt_url",
    "shipping_company",
    "shipping_company_code",
    "shipping_method",
    "shipping_status",
    "shipment_status",
    "tracking_number",
    "tracking_url",
    "shipping_label_url",
    "shipping_address",
    "shipping_address_raw",
    "shipping_city",
    "customer_city",
    "shipping_district",
    "shipping_street",
    "shipping_national_address",
    "shipping_short_address",
    "shipping_postal_code",
    "shipping_building_number",
    "shipping_additional_number",
    "shipping_country",
    "shipping_latitude",
    "shipping_longitude",
)))


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fill_missing(target: dict[str, Any], key: str, value: Any) -> None:
    if not _present(target.get(key)) and _present(value):
        target[key] = deepcopy(value)


def _v2_address_fallback(raw: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    durable = row.get("shipping_address_raw")
    if isinstance(durable, dict):
        address = deepcopy(durable)
    elif isinstance(row.get("shipping_address"), dict):
        address = deepcopy(row["shipping_address"])
    else:
        address = {}
        if _present(row.get("shipping_address")):
            address["address_line"] = row.get("shipping_address")

    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    _fill_missing(address, "country", row.get("shipping_country") or customer.get("country"))
    _fill_missing(address, "country_code", customer.get("country_code"))
    _fill_missing(address, "city", row.get("shipping_city") or row.get("customer_city") or customer.get("city"))

    district = row.get("shipping_district")
    if _present(district):
        if not _present(address.get("district")):
            address["district"] = {"name": district}
        _fill_missing(address, "block", district)

    _fill_missing(address, "street", row.get("shipping_street"))
    _fill_missing(address, "short_address", row.get("shipping_short_address") or row.get("shipping_national_address"))
    _fill_missing(address, "postal_code", row.get("shipping_postal_code"))
    _fill_missing(address, "building_number", row.get("shipping_building_number"))
    _fill_missing(address, "additional_number", row.get("shipping_additional_number"))
    _fill_missing(address, "latitude", row.get("shipping_latitude"))
    _fill_missing(address, "longitude", row.get("shipping_longitude"))
    _fill_missing(address, "address_line", customer.get("location"))

    return address if any(_present(value) for value in address.values()) else {}


def _apply_v2_root_fallbacks(raw: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    hydrated = deepcopy(raw)

    for field in _ATTRIBUTION_FIELDS:
        _fill_missing(hydrated, field, row.get(field))

    customer = deepcopy(hydrated.get("customer")) if isinstance(hydrated.get("customer"), dict) else {}
    _fill_missing(customer, "full_name", row.get("customer_name"))
    _fill_missing(customer, "mobile", row.get("customer_mobile"))
    _fill_missing(customer, "city", row.get("shipping_city") or row.get("customer_city"))
    _fill_missing(customer, "country", row.get("shipping_country"))
    if customer:
        hydrated["customer"] = customer

    shipping = deepcopy(hydrated.get("shipping")) if isinstance(hydrated.get("shipping"), dict) else {}
    provider_address = None
    if isinstance(shipping.get("address"), dict) and shipping.get("address"):
        provider_address = deepcopy(shipping["address"])
    elif isinstance(hydrated.get("shipping_address"), dict) and hydrated.get("shipping_address"):
        provider_address = deepcopy(hydrated["shipping_address"])

    address = provider_address or _v2_address_fallback(hydrated, row)
    if address and provider_address is None:
        _fill_missing(hydrated, "shipping_address", address)

    _fill_missing(shipping, "company_name", row.get("shipping_company"))
    _fill_missing(shipping, "company_code", row.get("shipping_company_code"))
    _fill_missing(shipping, "method", row.get("shipping_method"))
    _fill_missing(shipping, "status", row.get("shipping_status") or row.get("shipment_status"))
    _fill_missing(shipping, "tracking_number", row.get("tracking_number"))
    _fill_missing(shipping, "tracking_url", row.get("tracking_url"))
    _fill_missing(shipping, "label_url", row.get("shipping_label_url"))
    if address:
        _fill_missing(shipping, "address", address)
    if shipping:
        hydrated["shipping"] = shipping

    for field in (
        "payment_method",
        "paid_amount",
        "remaining_amount",
        "has_remaining_amount",
        "payment_collection_status",
        "payment_checkout_url",
        "receiving_bank_name",
        "payment_receipt_url",
        "shipping_label_url",
    ):
        _fill_missing(hydrated, field, row.get(field))

    return hydrated


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


def _normalized_text_expression(value: Any) -> dict[str, Any]:
    return {
        "$toLower": {
            "$trim": {
                "input": {
                    "$replaceAll": {
                        "input": {"$toString": {"$ifNull": [value, ""]}},
                        "find": "_",
                        "replacement": " ",
                    }
                }
            }
        }
    }


def _effective_status_expression() -> dict[str, Any]:
    """Use customized child state only while the order remains in review.

    Historical rows can retain ``customized = تم المراجعة`` after their current
    top-level status has moved to execution, delivery or another workflow. The
    current top-level state must win once it leaves the review parent.
    """
    current = "$order_status"
    customized = _customized_status_expression()
    current_normalized = _normalized_text_expression(current)
    return {
        "$let": {
            "vars": {
                "current": current,
                "customized": customized,
                "current_normalized": current_normalized,
            },
            "in": {
                "$cond": [
                    {
                        "$and": [
                            {"$ne": ["$$current_normalized", ""]},
                            {"$not": [{"$in": ["$$current_normalized", _REVIEW_PARENT_VALUES]}]},
                        ]
                    },
                    "$$current",
                    {
                        "$ifNull": [
                            "$$customized",
                            {
                                "$ifNull": [
                                    "$$current",
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
                    },
                ]
            },
        }
    }


def _normalized_status_expression() -> dict[str, Any]:
    return _normalized_text_expression(_effective_status_expression())


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
            "order_status": 1,
            "raw_by_source.salla_direct": 1,
            **{field: 1 for field in _V2_CANONICAL_ROOT_FIELDS},
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
                "order_status": 1,
                "raw_by_source.salla_direct": 1,
                **{field: 1 for field in _V2_CANONICAL_ROOT_FIELDS},
            },
        )
        return self._to_discovery_row(row)

    async def get_salla_orders(
        self,
        *,
        user_id: str,
        order_numbers: list[str],
    ) -> list[OrderDiscoveryRow]:
        """Load an exact tenant-scoped order set with one Mongo query."""
        normalized = list(dict.fromkeys(
            str(value or "").strip()
            for value in order_numbers
            if str(value or "").strip()
        ))
        if not normalized:
            return []
        cursor = self._collection.find(
            {
                "user_id": str(user_id),
                "order_number": {"$in": normalized},
                "raw_by_source.salla_direct": {"$exists": True},
            },
            {
                "_id": 0,
                "order_number": 1,
                "order_date": 1,
                "order_status": 1,
                "raw_by_source.salla_direct": 1,
                **{field: 1 for field in _V2_CANONICAL_ROOT_FIELDS},
            },
        )
        rows: list[OrderDiscoveryRow] = []
        async for row in cursor:
            mapped = self._to_discovery_row(row)
            if mapped is not None:
                rows.append(mapped)
        return rows

    @staticmethod
    def _to_discovery_row(row: Optional[dict[str, Any]]) -> Optional[OrderDiscoveryRow]:
        if not isinstance(row, dict):
            return None
        order_number = str(row.get("order_number") or "").strip()
        order_date = str(row.get("order_date") or "").strip()
        raw_by_source = row.get("raw_by_source")
        if not isinstance(raw_by_source, dict):
            return None
        raw_provider = raw_by_source.get("salla_direct")
        if not isinstance(raw_provider, dict) or not order_number or not order_date:
            return None

        # Provider payload stays authoritative.  Missing V2 operational
        # fields are rehydrated from the durable normalized root snapshot so a
        # light Salla response cannot erase address, courier or payment evidence.
        salla_raw = _apply_v2_root_fallbacks(raw_provider, row)

        current_status = str(row.get("order_status") or "").strip() or None
        return OrderDiscoveryRow(
            order_number=order_number,
            order_date=order_date,
            salla_raw=salla_raw,
            current_status=current_status,
        )
