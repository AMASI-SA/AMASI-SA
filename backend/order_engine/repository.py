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
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "device",
    "is_gift",
    "gift",
    "gift_order",
    "order_type",
    "type",
    "mezan_read_at",
)

_ROOT_FALLBACK_FIELDS = (
    "order_id",
    "order_date_raw",
    "order_status",
    "order_status_slug",
    "payment_status",
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
    "receiving_bank_name",
    "payment_receipt_url",
    "customer_name",
    "customer_mobile",
    "customer_email",
    "payment_method",
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
    "products",
    "subtotal",
    "shipping_cost",
    "discount",
    "tax",
    "total_amount",
    "currency",
)


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _set_missing(target: dict[str, Any], key: str, value: Any) -> None:
    if not _has_value(target.get(key)) and _has_value(value):
        target[key] = deepcopy(value)


def _merge_missing(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(target or {})
    for key, value in (source or {}).items():
        _set_missing(merged, str(key), value)
    return merged


def _root_shipping_address(row: dict[str, Any]) -> dict[str, Any]:
    address: dict[str, Any] = {}
    for candidate in (row.get("shipping_address_raw"), row.get("shipping_address")):
        if isinstance(candidate, dict):
            address = _merge_missing(address, candidate)
        elif _has_value(candidate):
            _set_missing(address, "formatted", candidate)

    values = {
        "city": row.get("shipping_city") or row.get("customer_city"),
        "district": row.get("shipping_district"),
        "street": row.get("shipping_street"),
        "short_address": row.get("shipping_short_address") or row.get("shipping_national_address"),
        "postal_code": row.get("shipping_postal_code"),
        "building_number": row.get("shipping_building_number"),
        "additional_number": row.get("shipping_additional_number"),
        "country": row.get("shipping_country"),
        "latitude": row.get("shipping_latitude"),
        "longitude": row.get("shipping_longitude"),
    }
    for key, value in values.items():
        _set_missing(address, key, value)
    return address


def _build_v2_read_payload(
    raw_provider: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    """Return the V2 canonical read payload without I/O or mutation.

    ``raw_by_source.salla_direct`` can be a reduced API snapshot after a manual
    diagnostic, while ``unified_orders`` root fields retain richer verified
    webhook facts under first-writer/fill-empty merge rules.  V2 consumers read
    one reconstructed payload so Orders V2 and Fulfillment V2 cannot disagree.
    """
    payload = deepcopy(raw_provider or {})

    for field in _ATTRIBUTION_FIELDS:
        _set_missing(payload, field, row.get(field))

    _set_missing(payload, "id", row.get("order_id"))
    _set_missing(payload, "reference_id", row.get("order_number"))
    _set_missing(payload, "date", row.get("order_date_raw") or row.get("order_date"))
    _set_missing(payload, "status_slug", row.get("order_status_slug"))
    if not _has_value(payload.get("status")) and _has_value(row.get("order_status")):
        payload["status"] = {
            "slug": row.get("order_status_slug") or row.get("order_status"),
            "name": row.get("order_status"),
        }

    customer = deepcopy(payload.get("customer")) if isinstance(payload.get("customer"), dict) else {}
    _set_missing(customer, "full_name", row.get("customer_name"))
    _set_missing(customer, "mobile", row.get("customer_mobile"))
    _set_missing(customer, "email", row.get("customer_email"))

    address = _root_shipping_address(row)
    provider_address = payload.get("shipping_address")
    if isinstance(provider_address, dict):
        address = _merge_missing(provider_address, address)
    if address:
        payload["shipping_address"] = deepcopy(address)
        _set_missing(customer, "shipping_address", address)
    if customer:
        payload["customer"] = customer

    _set_missing(payload, "payment_method", row.get("payment_method"))
    for field in (
        "paid_amount",
        "remaining_amount",
        "has_remaining_amount",
        "payment_collection_status",
        "payment_checkout_url",
        "receiving_bank_name",
        "payment_receipt_url",
        "shipping_label_url",
    ):
        _set_missing(payload, field, row.get(field))

    payment = deepcopy(payload.get("payment")) if isinstance(payload.get("payment"), dict) else {}
    _set_missing(payment, "status", row.get("payment_status"))
    _set_missing(payment, "paid_amount", row.get("paid_amount"))
    _set_missing(payment, "remaining_amount", row.get("remaining_amount"))
    _set_missing(payment, "has_remaining_amount", row.get("has_remaining_amount"))
    _set_missing(payment, "collection_status", row.get("payment_collection_status"))
    _set_missing(payment, "checkout_url", row.get("payment_checkout_url"))
    _set_missing(payment, "receiving_bank_name", row.get("receiving_bank_name"))
    _set_missing(payment, "receipt_url", row.get("payment_receipt_url"))
    if payment:
        payload["payment"] = payment

    shipping = deepcopy(payload.get("shipping")) if isinstance(payload.get("shipping"), dict) else {}
    _set_missing(shipping, "company_name", row.get("shipping_company"))
    _set_missing(shipping, "company_code", row.get("shipping_company_code"))
    _set_missing(shipping, "method", row.get("shipping_method"))
    _set_missing(shipping, "status", row.get("shipping_status") or row.get("shipment_status"))
    _set_missing(shipping, "tracking_number", row.get("tracking_number"))
    _set_missing(shipping, "tracking_url", row.get("tracking_url"))
    _set_missing(shipping, "label_url", row.get("shipping_label_url"))
    if address:
        current_address = shipping.get("address") if isinstance(shipping.get("address"), dict) else {}
        shipping["address"] = _merge_missing(current_address, address)
    if shipping:
        payload["shipping"] = shipping

    if not _has_value(payload.get("items")) and isinstance(row.get("products"), list):
        payload["items"] = deepcopy(row.get("products"))

    amounts = deepcopy(payload.get("amounts")) if isinstance(payload.get("amounts"), dict) else {}
    _set_missing(amounts, "sub_total", row.get("subtotal"))
    _set_missing(amounts, "shipping_cost", row.get("shipping_cost"))
    _set_missing(amounts, "discount", row.get("discount"))
    _set_missing(amounts, "tax", row.get("tax"))
    _set_missing(amounts, "total", row.get("total_amount"))
    _set_missing(amounts, "currency", row.get("currency"))
    if amounts:
        payload["amounts"] = amounts
    _set_missing(payload, "total_amount", row.get("total_amount"))
    _set_missing(payload, "currency", row.get("currency"))
    return payload



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
            **{field: 1 for field in (*_ATTRIBUTION_FIELDS, *_ROOT_FALLBACK_FIELDS)},
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
                **{field: 1 for field in (*_ATTRIBUTION_FIELDS, *_ROOT_FALLBACK_FIELDS)},
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
        raw_provider = raw_by_source.get("salla_direct")
        if not isinstance(raw_provider, dict) or not order_number or not order_date:
            return None

        # V2 reads one canonical payload. The provider raw snapshot remains
        # first authority, while missing facts are filled from durable root fields
        # preserved by unified_orders merge rules. This is read-only and performs
        # no Salla or database write.
        salla_raw = _build_v2_read_payload(raw_provider, row)

        current_status = str(row.get("order_status") or "").strip() or None
        return OrderDiscoveryRow(
            order_number=order_number,
            order_date=order_date,
            salla_raw=salla_raw,
            current_status=current_status,
        )
