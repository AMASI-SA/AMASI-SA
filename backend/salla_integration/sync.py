"""Salla → unified_orders sync engine (Phase 2).

Pulls Orders, Refunds (via Transactions), and Products from Salla and
upserts into the existing `unified_orders` collection using
`orders_db.upsert_order(source="salla_direct")` so:
    • The merge-rules already in place protect Make.com (real-time) data.
    • Excel uploads keep working unchanged.
    • The dashboard, reconciliation, accounts pages see the new source
      automatically (without any code change in those pages).

Run modes
---------
    • Manual: triggered by the UI "Sync Now" button (routes.py).
    • Scheduled: NOT enabled in Phase 2 (per user — only manual button
      for now). When enabled later, just call `run_orders_sync()` on
      a 15-minute timer.

Sync log
--------
Every invocation creates a row in `salla_sync_logs` with start/end
timestamps, source, counts (created/updated/errors), and the cursor
state (page, last order_id). The UI reads this to render a log feed.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from carrier_handoff import advance_carrier_handoff_from_salla_status

from salla_marketing_attribution import promoted_salla_attribution

from .service import SallaError, call_salla

# orders_db is imported at module top-level to keep `salla_direct` writes
# routed through the same merge logic Make/Excel use.
from orders_db import upsert_order


logger = logging.getLogger(__name__)


# Salla's /orders endpoint uses page-based pagination. Default per_page is
# 15; we ask for the max (50) for fewer round-trips.
ORDERS_PER_PAGE = 50
MAX_PAGES_PER_RUN = 40  # 50 * 40 = 2000 orders / run — protects against runaway pulls
# Explicit date-range imports are operator-requested historical jobs. Salla
# currently caps `/orders` at 30 rows even when `per_page=50`, so the normal
# 40-page ceiling silently stopped a 2026-07-01 import after only 1,200 rows.
# The route runs these longer imports in the background; keep a separate hard
# ceiling that covers the approved tax period without allowing an unbounded
# crawl.
MAX_RANGE_PAGES_PER_RUN = 120
MAX_ATTRIBUTION_RECOVERY_DAYS = 3
MAX_ATTRIBUTION_RECOVERY_ORDERS = 500
PRODUCTS_PER_PAGE = 60
MAX_PRODUCT_PAGES = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _store_bank_id(order: dict) -> str:
    """Return Salla's merchant receiving-bank id from an order payload."""
    payment = order.get("payment") or {}
    payment = payment if isinstance(payment, dict) else {}
    bank = order.get("bank") or {}
    bank = bank if isinstance(bank, dict) else {}
    return _str(
        payment.get("store_bank_id")
        or order.get("store_bank_id")
        or bank.get("id")
    )


async def _enrich_order_receiving_bank(db, user_id: str, order: dict) -> dict:
    """Resolve ``payment.store_bank_id`` once at the ingestion boundary.

    The unified order is the durable source for Order Details and Qoyod.
    Downstream pages must not call Salla or maintain their own bank lookup.
    """
    bank_id = _store_bank_id(order)
    if not bank_id:
        return order

    bank = order.get("bank") or {}
    bank = bank if isinstance(bank, dict) else {}
    if bank.get("bank_name") or bank.get("name"):
        return order

    cached = await db.salla_payment_banks.find_one(
        {"user_id": str(user_id), "salla_bank_id": bank_id},
        {"_id": 0, "bank_name": 1, "account_name": 1},
    )
    details = cached if isinstance(cached, dict) else None

    if not details or not details.get("bank_name"):
        try:
            response = await call_salla(
                db, user_id, "GET", f"/payment/banks/{bank_id}")
            payload = response.get("data") if isinstance(response, dict) else None
            if isinstance(payload, dict) and payload.get("bank_name"):
                details = payload
                await db.salla_payment_banks.update_one(
                    {"user_id": str(user_id), "salla_bank_id": bank_id},
                    {"$set": {
                        "bank_name": _str(payload.get("bank_name")),
                        "account_name": _str(payload.get("account_name")),
                        "updated_at": _now(),
                    }},
                    upsert=True,
                )
        except SallaError as exc:
            logger.warning(
                "Could not resolve Salla receiving bank %s for order %s: %s",
                bank_id, order.get("reference_id"), exc,
            )

    if not details or not details.get("bank_name"):
        return order

    enriched = dict(order)
    enriched["bank"] = {
        **bank,
        "id": bank_id,
        "bank_name": _str(details.get("bank_name")),
        "account_name": _str(details.get("account_name")),
    }
    enriched["receiving_bank_name"] = _str(details.get("bank_name"))
    return enriched


async def _refresh_plan_b_status_snapshot(
    db,
    user_id: str,
    order_number: str,
    order_doc: dict,
) -> dict:
    """Upsert a read-only current-status snapshot for Plan B.

    This snapshot uses its own connector and is explicitly ineligible for
    Qoyod processing. Repeating the same status updates the same snapshot
    instead of violating the inbox idempotency index.
    """
    order_number = str(order_number or "").strip()
    status_slug = str(
        order_doc.get("order_status_slug")
        or order_doc.get("order_status")
        or ""
    ).strip().lower()
    status_native = str(order_doc.get("order_status") or status_slug).strip()

    if not order_number or not status_slug:
        return {
            "created": False,
            "updated": False,
            "reason": "missing_order_or_status",
        }

    carrier_handoff_transition = {
        "advanced": False,
        "reason": "evaluation_failed",
    }
    try:
        carrier_handoff_transition = (
            await advance_carrier_handoff_from_salla_status(
                db,
                user_id=str(user_id),
                order_number=order_number,
                status_slug=status_slug,
                status_name=status_native,
                source="mezan_orders_page_status_sync",
            )
        )
    except Exception as exc:
        # Status snapshots must remain available even when the local
        # fulfillment workflow has not been initialized yet.
        carrier_handoff_transition["error"] = str(exc)[:300]

    latest = await db.integration_inbox.find_one(
        {
            "user_id": {"$in": [user_id, "main"]},
            "$or": [
                {"salla_order_number": order_number},
                {"canonical_payload.order_number": order_number},
                {"canonical_payload.order_id": order_number},
            ],
        },
        sort=[("received_at", -1)],
    )

    canonical = dict((latest or {}).get("canonical_payload") or {})
    previous_slug = str(canonical.get("order_status") or "").strip().lower()
    previous_native = str(canonical.get("order_status_native") or "").strip()
    # Always store the current-status snapshot under the authenticated
    # tenant. Legacy traces under "main" may be read as a payload fallback,
    # but must never decide the tenant of the new snapshot.
    snapshot_user_id = str(user_id).strip()

    now = _now()
    metadata = dict(canonical.get("metadata") or {})
    metadata.update({
        "source_event": "order.updated",
        "status_source": "salla_order_details",
        "resynced_at": now,
    })
    canonical.update({
        "order_number": order_number,
        "order_status": status_slug,
        "order_status_native": status_native,
        "metadata": metadata,
    })
    # A status resync must carry the current payment evidence too.  Without
    # this, the newest Plan-B snapshot can shadow the richer unified order
    # and reduce a named bank transfer back to the generic value `bank`.
    for canonical_key in (
        "payment_method", "receiving_bank_name", "payment_receipt_url",
    ):
        value = order_doc.get(canonical_key)
        if value not in (None, ""):
            canonical[canonical_key] = value

    connector_key = "salla_direct_status_resync"
    idempotency_key = (
        f"salla:order:{order_number}:order.updated:{status_slug}"
    )
    trace_id = uuid.uuid4().hex

    snapshot = {
        "trace_id": trace_id,
        "user_id": snapshot_user_id,
        "connector_key": connector_key,
        "idempotency_key": idempotency_key,
        "salla_order_number": order_number,
        "source": connector_key,
        "received_at": now,
        "updated_at": now,
        "canonical_payload": canonical,
        "pipeline_stage": "STATUS_SNAPSHOT",
        "no_qoyod_send": True,
        "eligibility_only": True,
        "manual_send_allowed": False,
        "auto_send_allowed": False,
        "salla_direct_status_resync": {
            "at": now,
            "source_endpoint": "GET /orders/{id}",
            "previous_status_slug": previous_slug or None,
            "previous_status_native": previous_native or None,
            "new_status_slug": status_slug,
            "new_status_native": status_native,
        },
    }

    selector = {
        "user_id": snapshot_user_id,
        "connector_key": connector_key,
        "idempotency_key": idempotency_key,
    }
    try:
        result = await db.integration_inbox.update_one(
            selector,
            {
                "$set": snapshot,
                "$setOnInsert": {
                    "id": uuid.uuid4().hex,
                    "created_at": now,
                },
            },
            upsert=True,
        )
    except DuplicateKeyError:
        await db.integration_inbox.update_one(selector, {"$set": snapshot})
        return {
            "created": False,
            "updated": True,
            "reason": "concurrent_duplicate_snapshot_updated",
            "trace_id": trace_id,
            "status_slug": status_slug,
            "status_native": status_native,
            "no_qoyod_send": True,
            "carrier_handoff_transition": carrier_handoff_transition,
        }

    return {
        "created": result.upserted_id is not None,
        "updated": result.upserted_id is None,
        "trace_id": trace_id,
        "connector_key": connector_key,
        "idempotency_key": idempotency_key,
        "previous_status": previous_slug or None,
        "new_status": status_slug,
        "status_slug": status_slug,
        "status_native": status_native,
        "no_qoyod_send": True,
        "carrier_handoff_transition": carrier_handoff_transition,
    }


# ── Salla order → unified_orders document shape ──────────────────────────
def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _money(v: Any, *, _depth: int = 0) -> float:
    """Safely normalize Salla money values into a float.

    Supported examples:
    - 123.45
    - "123.45"
    - {"amount": 123.45, "currency": "SAR"}
    - {"amount": {"amount": 123.45, "currency": "SAR"}}
    - {"value": {"amount": "123.45"}}

    Invalid or excessively nested values resolve to 0.0.
    """
    if v is None or v == "":
        return 0.0

    if _depth > 8:
        return 0.0

    if isinstance(v, bool):
        return 0.0

    if isinstance(v, dict):
        for key in (
            "amount",
            "value",
            "total",
            "price",
            "sub_total",
            "subtotal",
        ):
            if key in v and v.get(key) not in (None, ""):
                return _money(v.get(key), _depth=_depth + 1)
        return 0.0

    if isinstance(v, (list, tuple)):
        for candidate in v:
            amount = _money(candidate, _depth=_depth + 1)
            if amount != 0.0:
                return amount
        return 0.0

    try:
        return float(v)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _media_url(v: Any, *, _depth: int = 0) -> str:
    """Extract a stable URL from Salla media fields without losing raw data."""
    if v in (None, "") or _depth > 8:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for key in ("url", "original", "src", "full", "medium", "thumbnail"):
            result = _media_url(v.get(key), _depth=_depth + 1)
            if result:
                return result
        return ""
    if isinstance(v, (list, tuple)):
        for candidate in v:
            result = _media_url(candidate, _depth=_depth + 1)
            if result:
                return result
    return ""


def _normalize_date(v: Any) -> Optional[str]:
    """Salla ISO with TZ → YYYY-MM-DD."""
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    # Salla returns dict {"date": "2024-...", "timezone": "Asia/Riyadh", ...}
    if isinstance(v, dict):
        return _normalize_date(v.get("date"))
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.split("+")[0].split("Z")[0], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def _legacy_item_image_url(item: dict, product: dict) -> str:
    """Return the first usable Salla order-item image URL."""
    candidates = [
        item.get("thumbnail"),
        item.get("product_thumbnail"),
        item.get("image_url"),
        item.get("image"),
        item.get("images"),
        product.get("main_image"),
        product.get("thumbnail"),
        product.get("image"),
        product.get("images"),
    ]

    def extract(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()

        if isinstance(value, dict):
            for key in (
                "url",
                "original",
                "medium",
                "thumbnail",
                "small",
            ):
                result = extract(value.get(key))
                if result:
                    return result
            return ""

        if isinstance(value, list):
            for entry in value:
                result = extract(entry)
                if result:
                    return result

        return ""

    for candidate in candidates:
        result = extract(candidate)
        if result:
            return result

    return ""


def _legacy_option_value(value: Any) -> Any:
    """Extract a customer-visible value while avoiding raw JSON in legacy UI."""
    if isinstance(value, dict):
        for key in (
            "value",
            "name",
            "label",
            "text",
            "option_value",
            "title",
        ):
            candidate = value.get(key)
            if candidate in (None, "", [], {}):
                continue

            extracted = _legacy_option_value(candidate)
            if extracted not in (None, "", [], {}):
                return extracted

        return None

    if isinstance(value, list):
        values = [
            _legacy_option_value(entry)
            for entry in value
        ]
        values = [
            value
            for value in values
            if value not in (None, "", [], {})
        ]
        return values

    return value


def _legacy_item_options(item: dict) -> list[dict]:
    """Normalize Salla options for legacy products[] consumers."""
    result: list[dict] = []

    raw_options = item.get("options") or []
    if isinstance(raw_options, dict):
        raw_options = [raw_options]

    for option in raw_options:
        if not isinstance(option, dict):
            continue

        name = _str(
            option.get("name")
            or option.get("label")
            or option.get("key")
            or option.get("option")
        )
        raw_value = next(
            (
                candidate
                for candidate in (
                    option.get("value"),
                    option.get("values"),
                    option.get("selected"),
                    option.get("choice"),
                    option.get("text"),
                )
                if candidate not in (None, "", [], {})
            ),
            None,
        )
        value = _legacy_option_value(raw_value)

        if not name or value in (None, "", [], {}):
            continue

        result.append({
            "name": name,
            "value": value,
        })

    return result


def _salla_order_to_doc(salla_order: dict) -> dict:
    """Map Salla /orders payload → unified_orders document fields.

    Salla nests heavily; we extract only the fields the rest of the system
    needs. The full raw payload is kept in raw_by_source['salla_direct']
    via upsert_order(raw=...).
    """
    # Salla shapes nested objects: customer, payment_method, shipping,
    # amounts.total, status, etc. We defend against missing keys
    # because store configurations vary widely.
    customer = salla_order.get("customer") or {}
    amounts = salla_order.get("amounts") or {}
    total_obj = amounts.get("total") or {}
    shipping_obj = amounts.get("shipping_cost") or {}
    discount_obj = amounts.get("discounts") or {}
    tax_obj = amounts.get("tax") or {}
    subtotal_obj = amounts.get("sub_total") or amounts.get("subtotal") or {}

    payment_method_obj = salla_order.get("payment_method") or {}
    payment_method = (
        payment_method_obj
        or (salla_order.get("payment") or {}).get("method")
        or ""
    )
    if isinstance(payment_method, dict):
        payment_method = payment_method.get("name") or payment_method.get("code") or ""

    shipping_company = ""
    first_shipment: dict = {}
    shipment = salla_order.get("shipments") or []
    if shipment and isinstance(shipment, list):
        first_shipment = shipment[0] or {}
        if not isinstance(first_shipment, dict):
            first_shipment = {}
        shipping_company = (
            (first_shipment.get("courier") or {}).get("name")
            or first_shipment.get("courier_name")
            or ""
        )
    shipping_label_url = _media_url(
        first_shipment.get("label_url")
        or first_shipment.get("label")
        or first_shipment.get("awb_url")
        or first_shipment.get("waybill_url")
    )
    if not shipping_company:
        shipping = salla_order.get("shipping") or {}
        if isinstance(shipping, dict):
            shipping_company = (shipping.get("company") or {}).get("name") or shipping.get("company_name") or ""

    status_obj = salla_order.get("status") or {}
    if isinstance(status_obj, dict):
        order_status = status_obj.get("name") or status_obj.get("customized") or ""
        order_status_slug = status_obj.get("slug") or ""
    else:
        order_status = str(status_obj)
        order_status_slug = ""

    payment_status = ""
    payment_obj = salla_order.get("payment") or {}
    if not isinstance(payment_obj, dict):
        payment_obj = {}
    payment_status = payment_obj.get("status") or ""

    # Salla collection facts. Keep these separate from order status: an order
    # can be under review while still having a positive remaining balance.
    payment_actions = salla_order.get("payment_actions") or {}
    if not isinstance(payment_actions, dict):
        payment_actions = {}
    remaining_action = payment_actions.get("remaining_action") or {}
    refund_action = payment_actions.get("refund_action") or {}
    if not isinstance(remaining_action, dict):
        remaining_action = {}
    if not isinstance(refund_action, dict):
        refund_action = {}

    paid_amount = _money(
        remaining_action.get("paid_amount")
        or refund_action.get("paid_amount")
        or salla_order.get("paid_amount")
    )
    remaining_amount = _money(
        remaining_action.get("remaining_amount")
        or salla_order.get("remaining_amount")
    )
    has_remaining_amount = bool(
        remaining_action.get("has_remaining_amount")
        or remaining_amount > 0
    )
    checkout_url = _str(
        remaining_action.get("checkout_url")
        or salla_order.get("checkout_url")
    )

    # Bank-transfer evidence is supplied by Salla on the order itself:
    # bank.bank_name identifies the merchant receiving account, while
    # receipt_image is the customer's uploaded proof of transfer.
    bank = salla_order.get("bank") or {}
    if not isinstance(bank, dict):
        bank = {}
    # Keep this aligned with Order Details' bank discovery.  Depending on
    # the Salla order shape, the actual receiving bank may be exposed on
    # the root order, the bank/payment objects, or the payment-method
    # object's human-readable name rather than in the generic code `bank`.
    payment_method_bank = payment_method_obj if isinstance(
        payment_method_obj, dict) else {}
    receiving_bank_name = _str(
        bank.get("bank_name")
        or bank.get("name")
        or salla_order.get("receiving_bank_name")
        or salla_order.get("receiving_bank")
        or salla_order.get("bank_name")
        or payment_obj.get("receiving_bank_name")
        or payment_obj.get("receiving_bank")
        or payment_obj.get("bank_name")
        or payment_obj.get("bank")
        or payment_method_bank.get("receiving_bank_name")
        or payment_method_bank.get("receiving_bank")
        or payment_method_bank.get("bank_name")
        or payment_method_bank.get("bank")
        or payment_method_bank.get("name")
        or payment_method_bank.get("label")
    )
    payment_receipt_url = _media_url(
        salla_order.get("receipt_image")
        or salla_order.get("payment_receipt_url")
        or payment_obj.get("receipt_url")
        or payment_obj.get("receipt_image")
    )

    if remaining_amount > 0:
        payment_collection_status = "partial" if paid_amount > 0 else "unpaid"
    elif paid_amount > 0:
        payment_collection_status = "paid"
    else:
        payment_collection_status = "unknown"

    # Products — authoritative legacy projection from /orders/items.
    items = salla_order.get("items") or []
    products: list[dict] = []

    for it in items:
        if not isinstance(it, dict):
            continue

        prod = it.get("product") or {}
        if not isinstance(prod, dict):
            prod = {}

        amounts_item = it.get("amounts") or {}
        if not isinstance(amounts_item, dict):
            amounts_item = {}

        options = _legacy_item_options(it)

        products.append({
            "order_item_id": _str(it.get("id")),
            "product_id": _str(
                prod.get("id")
                or it.get("product_id")
            ),
            "parent_product_id": _str(
                prod.get("parent_id")
                or it.get("parent_product_id")
            ),
            "variant_id": _str(
                it.get("product_sku_id")
                or it.get("variant_id")
                or prod.get("variant_id")
            ),
            "name": _str(
                prod.get("name")
                or it.get("name")
            ),
            "sku": _str(
                prod.get("sku")
                or it.get("sku")
            ),
            "barcode": _str(
                prod.get("barcode")
                or it.get("barcode")
                or it.get("gtin")
                or it.get("mpn")
            ),
            "quantity": float(it.get("quantity") or 0),
            "price": _money(
                amounts_item.get("price_without_tax")
                or amounts_item.get("price")
                or it.get("price")
            ),
            "total": _money(
                amounts_item.get("total")
                or it.get("total")
            ),
            "discount": _money(
                amounts_item.get("total_discount")
                or amounts_item.get("discount")
                or it.get("discount")
            ),
            "tax": _money(
                amounts_item.get("tax")
                or it.get("tax")
            ),
            "image_url": _legacy_item_image_url(it, prod),
            "options": options,
            "custom_fields": [
                value
                for key in (
                    "custom_fields",
                    "customizations",
                    "personalization",
                    "attachments",
                    "files",
                )
                for value in (
                    it.get(key)
                    if isinstance(it.get(key), list)
                    else [it.get(key)]
                )
                if isinstance(value, dict)
            ],
        })

    order_date_raw = (salla_order.get("date") or {}).get("date") if isinstance(salla_order.get("date"), dict) else salla_order.get("date")
    order_date = _normalize_date(salla_order.get("date") or salla_order.get("created_at"))

    return {
        "order_id": _str(salla_order.get("id")),
        "order_number": _str(salla_order.get("reference_id") or salla_order.get("id")),
        "order_date": order_date,
        "order_date_raw": _str(order_date_raw),
        "order_date_inferred": False,
        "order_status": _str(order_status),
        "order_status_slug": _str(order_status_slug),
        "payment_status": _str(payment_status),
        "paid_amount": paid_amount,
        "remaining_amount": remaining_amount,
        "has_remaining_amount": has_remaining_amount,
        "is_pending_payment": (
            salla_order.get("is_pending_payment")
            if isinstance(salla_order.get("is_pending_payment"), bool)
            else None
        ),
        "payment_collection_status": payment_collection_status,
        "payment_checkout_url": checkout_url,
        "receiving_bank_name": receiving_bank_name,
        "receiving_bank_id": _store_bank_id(salla_order),
        "payment_receipt_url": payment_receipt_url,
        "customer_name": _str(customer.get("full_name") or customer.get("first_name") or ""),
        "customer_mobile": _str(customer.get("mobile") or customer.get("phone") or ""),
        "payment_method": _str(payment_method),
        "shipping_company": _str(shipping_company),
        "shipping_label_url": shipping_label_url,
        "shipping_cost": _money(shipping_obj),
        "subtotal": _money(subtotal_obj),
        "discount": _money(discount_obj),
        "tax": _money(tax_obj),
        "total_amount": _money(total_obj),
        "currency": _str(total_obj.get("currency") if isinstance(total_obj, dict) else "") or "SAR",
        "source": _str(salla_order.get("source") or "salla_direct"),
        # Keep Salla's raw payload for audit while promoting only the stable
        # marketing fields needed by ad attribution.  This does not change the
        # order, accounting, fulfilment or Qoyod source of truth.
        **promoted_salla_attribution(salla_order),
        "products": products,
    }


# ── Sync log helpers ──────────────────────────────────────────────────────
async def create_sync_log(db, user_id: str, kind: str) -> str:
    log_id = str(uuid.uuid4())
    await db.salla_sync_logs.insert_one({
        "id": log_id,
        "user_id": user_id,
        "kind": kind,            # "orders" | "products" | "refunds"
        "status": "running",
        "started_at": _now(),
        "ended_at": None,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors_count": 0,
        "pages_fetched": 0,
        "last_error": None,
        "errors_sample": [],
        "cursor": {},
    })
    return log_id


async def finish_sync_log(db, log_id: str, status: str, *, extra: Optional[dict] = None) -> None:
    payload: dict = {"status": status, "ended_at": _now()}
    if extra:
        payload.update(extra)
    await db.salla_sync_logs.update_one({"id": log_id}, {"$set": payload})


async def update_sync_log(db, log_id: str, **counters) -> None:
    inc = {k: v for k, v in counters.items() if isinstance(v, (int, float))}
    set_ = {k: v for k, v in counters.items() if not isinstance(v, (int, float))}
    update: dict = {}
    if inc:
        update["$inc"] = inc
    if set_:
        update["$set"] = set_
    if update:
        await db.salla_sync_logs.update_one({"id": log_id}, update)


# ── Public sync routines ──────────────────────────────────────────────────
async def run_orders_sync(
    db,
    user_id: str,
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    updated_since_hours: Optional[int] = None,
    log_id: Optional[str] = None,
    recover_marketing_attribution: bool = False,
) -> dict:
    """Pull orders from Salla and upsert into unified_orders.

    Parameters
    ----------
    from_date / to_date : optional ISO YYYY-MM-DD bounds (Salla filters by
        creation date when both are present).
    updated_since_hours : if set, ask Salla for orders updated in the last
        N hours (uses the `updated_at_gt` filter). Convenient for cron-style
        incremental syncs.
    """
    if recover_marketing_attribution:
        if not from_date or not to_date:
            raise ValueError(
                "marketing attribution recovery requires from_date and to_date"
            )
        try:
            recovery_start = datetime.strptime(from_date, "%Y-%m-%d").date()
            recovery_end = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("invalid attribution recovery date range") from exc
        recovery_days = (recovery_end - recovery_start).days + 1
        if recovery_days < 1 or recovery_days > MAX_ATTRIBUTION_RECOVERY_DAYS:
            raise ValueError(
                "marketing attribution recovery is limited to three calendar days"
            )

    log_id = log_id or await create_sync_log(db, user_id, "orders")

    created = 0
    updated = 0
    skipped = 0
    errors_count = 0
    errors_sample: list[dict] = []
    pages_fetched = 0
    last_total_pages = 0
    attribution_details_fetched = 0
    attribution_orders_recovered = 0
    attribution_recovery_errors = 0

    try:
        page = 1
        max_pages = (
            MAX_RANGE_PAGES_PER_RUN
            if from_date and to_date
            else MAX_PAGES_PER_RUN
        )
        while page <= max_pages:
            params: dict = {"page": page, "per_page": ORDERS_PER_PAGE, "format": "light"}
            if from_date:
                params["from_date"] = from_date
            if to_date:
                params["to_date"] = to_date
            if updated_since_hours:
                params["updated_at_gt"] = (
                    (_now() - timedelta(hours=updated_since_hours)).strftime("%Y-%m-%d %H:%M:%S")
                )

            try:
                resp = await call_salla(db, user_id, "GET", "/orders", params=params)
            except SallaError as e:
                errors_count += 1
                errors_sample.append({"page": page, "error": str(e)[:300]})
                await finish_sync_log(db, log_id, "failed", extra={
                    "created": created, "updated": updated, "skipped": skipped,
                    "errors_count": errors_count, "errors_sample": errors_sample[:20],
                    "pages_fetched": pages_fetched, "last_error": str(e)[:500],
                })
                raise

            data = resp.get("data") or []
            pages_fetched += 1
            if not data:
                break

            for raw in data:
                try:
                    if (
                        recover_marketing_attribution
                        and attribution_details_fetched
                        < MAX_ATTRIBUTION_RECOVERY_ORDERS
                    ):
                        internal_id = _str(raw.get("id"))
                        if internal_id:
                            attribution_details_fetched += 1
                            try:
                                details_response = await call_salla(
                                    db,
                                    user_id,
                                    "GET",
                                    f"/orders/{internal_id}",
                                )
                                details = (
                                    details_response.get("data")
                                    if isinstance(details_response, dict)
                                    else None
                                )
                                if isinstance(details, dict):
                                    raw = details
                                    recovered = promoted_salla_attribution(details)
                                    if (
                                        recovered.get("campaign_id")
                                        or recovered.get("utm_campaign")
                                        or recovered.get("campaign_name")
                                    ):
                                        attribution_orders_recovered += 1
                            except Exception:
                                attribution_recovery_errors += 1
                            await asyncio.sleep(0.1)
                    raw = await _enrich_order_receiving_bank(
                        db, user_id, raw)
                    doc = _salla_order_to_doc(raw)
                    if not doc.get("order_number"):
                        skipped += 1
                        continue
                    res = await upsert_order(
                        db, user_id, doc["order_number"], doc,
                        source="salla_direct", raw=raw,
                    )
                    await _refresh_plan_b_status_snapshot(
                        db, user_id, doc["order_number"], doc
                    )
                    if res.get("created"):
                        created += 1
                    else:
                        updated += 1
                except Exception as exc:  # pragma: no cover — defensive
                    errors_count += 1
                    if len(errors_sample) < 20:
                        errors_sample.append({
                            "order_id": str(raw.get("id") or "")[:60],
                            "error": str(exc)[:300],
                        })

            # Salla may cap /orders pages at 30 rows even when a larger
            # per_page value is requested. Do not treat a short page as the
            # final page; otherwise a 30-day sync stops after the newest 30
            # orders. Prefer pagination metadata and otherwise continue until
            # Salla returns an empty page, bounded by MAX_PAGES_PER_RUN.
            pagination = resp.get("pagination") or {}
            total_pages = int(
                pagination.get("totalPages")
                or pagination.get("total_pages")
                or pagination.get("last_page")
                or 0
            )
            last_total_pages = max(last_total_pages, total_pages)
            current_page = int(
                pagination.get("currentPage")
                or pagination.get("current_page")
                or pagination.get("page")
                or page
            )

            if total_pages and current_page >= total_pages:
                break

            page += 1
            # Be polite to Salla's rate limiter
            await asyncio.sleep(0.25 if from_date and to_date else 0.15)

        truncated = bool(last_total_pages and last_total_pages > max_pages)
        await finish_sync_log(db, log_id, "completed", extra={
            "created": created, "updated": updated, "skipped": skipped,
            "errors_count": errors_count, "errors_sample": errors_sample[:20],
            "pages_fetched": pages_fetched,
            "source_total_pages": last_total_pages,
            "truncated": truncated,
            "attribution_details_fetched": attribution_details_fetched,
            "attribution_orders_recovered": attribution_orders_recovered,
            "attribution_recovery_errors": attribution_recovery_errors,
        })
    except asyncio.CancelledError:
        await finish_sync_log(db, log_id, "interrupted", extra={
            "created": created, "updated": updated, "skipped": skipped,
            "errors_count": errors_count,
            "errors_sample": errors_sample[:20],
            "pages_fetched": pages_fetched,
            "last_error": "sync_task_cancelled_before_completion",
            "attribution_details_fetched": attribution_details_fetched,
            "attribution_orders_recovered": attribution_orders_recovered,
            "attribution_recovery_errors": attribution_recovery_errors,
        })
        raise
    except Exception as exc:
        await finish_sync_log(db, log_id, "failed", extra={
            "created": created, "updated": updated, "skipped": skipped,
            "errors_count": errors_count + 1,
            "errors_sample": (errors_sample + [{"error": str(exc)[:300]}])[:20],
            "pages_fetched": pages_fetched,
            "last_error": str(exc)[:500],
            "attribution_details_fetched": attribution_details_fetched,
            "attribution_orders_recovered": attribution_orders_recovered,
            "attribution_recovery_errors": attribution_recovery_errors,
        })
        raise

    return {
        "log_id": log_id,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors_count": errors_count,
        "pages_fetched": pages_fetched,
        "source_total_pages": last_total_pages,
        "truncated": bool(last_total_pages and last_total_pages > max_pages),
        "attribution_details_fetched": attribution_details_fetched,
        "attribution_orders_recovered": attribution_orders_recovered,
        "attribution_recovery_errors": attribution_recovery_errors,
    }


async def _fetch_salla_order_items(
    db,
    user_id: str,
    internal_order_id: str,
) -> list[dict]:
    """Fetch authoritative line items for one Salla order.

    Salla Order Details does not embed line items. They are retrieved
    separately through GET /orders/items?order_id=<internal id>.
    """
    internal_order_id = str(internal_order_id or "").strip()

    if not internal_order_id:
        raise RuntimeError("Salla order items missing internal order id")

    response = await call_salla(
        db,
        user_id,
        "GET",
        "/orders/items",
        params={"order_id": internal_order_id},
    )

    rows = response.get("data") if isinstance(response, dict) else None

    if not isinstance(rows, list):
        raise RuntimeError(
            "Salla List Order Items returned invalid payload: "
            f"internal_order_id={internal_order_id}"
        )

    return [
        dict(row)
        for row in rows
        if isinstance(row, dict)
    ]


def _order_item_sku(item: dict) -> str:
    product = item.get("product") or {}
    variant = item.get("variant") or {}
    return _str(
        item.get("sku")
        or (product.get("sku") if isinstance(product, dict) else None)
        or (variant.get("sku") if isinstance(variant, dict) else None)
    )


async def _enrich_missing_order_item_skus(
    db, user_id: str, items: list[dict],
) -> list[dict]:
    """Resolve omitted order-item SKUs from Salla's read-only catalogue.

    Order Items can carry only a variant/product id. Qoyod requires the real
    merchant SKU, so query Salla's documented variant/product detail endpoint
    rather than inventing an accounting identity. Failed or blank lookups stay
    blank and are refused by the existing Qoyod SKU guard.
    """
    cache: dict[str, str] = {}
    enriched: list[dict] = []
    for original in items:
        item = dict(original)
        if _order_item_sku(item):
            enriched.append(item)
            continue

        product = item.get("product") or {}
        variant = item.get("variant") or {}
        variant_id = _str(
            item.get("product_sku_id")
            or item.get("variant_id")
            or (variant.get("id") if isinstance(variant, dict) else None)
        )
        product_id = _str(
            item.get("product_id")
            or (product.get("id") if isinstance(product, dict) else None)
        )
        candidates = []
        if variant_id:
            candidates.append((
                f"/products/variants/{variant_id}", "variant_details"))
        if product_id:
            candidates.append((f"/products/{product_id}", "product_details"))

        for endpoint, source in candidates:
            sku = cache.get(endpoint)
            if sku is None:
                try:
                    response = await call_salla(
                        db, user_id, "GET", endpoint)
                    payload = (
                        response.get("data")
                        if isinstance(response, dict)
                        else None
                    )
                    sku = _str(
                        payload.get("sku")
                        if isinstance(payload, dict)
                        else None
                    )
                except Exception as exc:  # keep the hard SKU guard closed
                    logger.warning(
                        "Could not resolve Salla SKU from %s: %s",
                        endpoint, exc,
                    )
                    sku = ""
                cache[endpoint] = sku
            if sku:
                item["sku"] = sku
                item["_mezan_sku_resolution"] = {
                    "source": source,
                    "endpoint": endpoint,
                }
                break
        enriched.append(item)
    return enriched


_SHIPMENT_CLEARABLE_FIELDS = {
    "label",
    "label_url",
    "shipping_number",
    "tracking_number",
    "tracking_link",
    "tracking_url",
    "status",
}

_SHIPMENT_CONTEXT_FIELDS = {
    "ship_to",
    "shipping_address",
    "address",
    "courier",
    "courier_name",
    "company",
    "company_name",
    "method",
    "shipping_method",
    "service",
    "recipient",
    "receiver",
    "pickup_address",
    "branch",
    "total_weight",
}


def _shipment_context(row: Any) -> dict:
    if not isinstance(row, dict):
        return {}
    return {
        key: value
        for key, value in row.items()
        if key in _SHIPMENT_CONTEXT_FIELDS and value not in (None, "", [], {})
    }


def _embedded_shipment_context(row: dict, embedded_rows: list[dict]) -> dict:
    shipment_id = _str(row.get("id"))
    if shipment_id:
        for embedded in embedded_rows:
            if _str(embedded.get("id")) == shipment_id:
                return _shipment_context(embedded)
    if len(embedded_rows) == 1:
        return _shipment_context(embedded_rows[0])
    return {}


def _merge_shipment_payload(base: dict, overlay: dict) -> dict:
    merged = dict(base or {})
    for key, value in (overlay or {}).items():
        if key in _SHIPMENT_CLEARABLE_FIELDS or value not in (None, "", [], {}):
            merged[key] = value
    return merged


async def _fetch_salla_shipment_details(
    db,
    user_id: str,
    internal_order_id: str,
    embedded_shipments: Any,
) -> list[dict]:
    """Return current shipment rows without losing order delivery context.

    The shipment-list/detail endpoints remain authoritative for labels, tracking
    and status.  Salla may omit the shipping address and courier from those
    responses, or return an empty list before a shipment is created.  In that
    case we preserve only address/courier context from Order Details and strip
    any embedded label/tracking fields so cancelled labels cannot reappear.
    """
    embedded_rows = [
        dict(row)
        for row in (embedded_shipments or [])
        if isinstance(row, dict)
    ]
    rows: list[dict] = []
    listed_succeeded = False

    try:
        response = await call_salla(
            db,
            user_id,
            "GET",
            "/shipments",
            params={
                "order_id": internal_order_id,
                "per_page": 50,
            },
        )
        listed = response.get("data") if isinstance(response, dict) else None
        if isinstance(listed, list):
            listed_succeeded = True
            rows = [dict(row) for row in listed if isinstance(row, dict)]
        else:
            rows = embedded_rows
    except SallaError as exc:
        logger.warning(
            "Could not list Salla shipments for order %s: %s",
            internal_order_id,
            exc,
        )
        rows = embedded_rows

    if listed_succeeded and not rows:
        return [
            context
            for context in (_shipment_context(row) for row in embedded_rows)
            if context
        ]

    async def enrich(row: dict) -> dict:
        base = (
            _embedded_shipment_context(row, embedded_rows)
            if listed_succeeded
            else {}
        )
        merged = _merge_shipment_payload(base, row)
        shipment_id = _str(row.get("id"))
        if not shipment_id:
            return merged

        try:
            response = await call_salla(
                db,
                user_id,
                "GET",
                f"/shipments/{shipment_id}",
            )
        except SallaError as exc:
            logger.warning(
                "Could not fetch Salla shipment %s details: %s",
                shipment_id,
                exc,
            )
            return merged

        details = response.get("data") if isinstance(response, dict) else None
        if not isinstance(details, dict):
            return merged
        return _merge_shipment_payload(merged, details)

    if not rows:
        return []

    return list(await asyncio.gather(*(enrich(row) for row in rows)))

async def _fetch_salla_order_details(
    db,
    user_id: str,
    order_number: str,
) -> dict | None:
    """Resolve the internal Salla id, then fetch authoritative details."""
    search_resp = await call_salla(
        db,
        user_id,
        "GET",
        "/orders",
        params={
            "reference_id": order_number,
            "format": "light",
            "per_page": 10,
        },
    )
    rows = search_resp.get("data") if isinstance(search_resp, dict) else None
    if not isinstance(rows, list):
        rows = []

    match = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        reference_id = str(row.get("reference_id") or "").strip()
        row_id = str(row.get("id") or "").strip()
        if reference_id == order_number or row_id == order_number:
            match = row
            break

    if match is None and len(rows) == 1 and isinstance(rows[0], dict):
        match = rows[0]
    if match is None:
        return None

    internal_id = str(match.get("id") or "").strip()
    if not internal_id:
        raise RuntimeError(
            f"Salla search result missing internal id: {order_number}"
        )

    details_resp = await call_salla(
        db,
        user_id,
        "GET",
        f"/orders/{internal_id}",
    )
    details = details_resp.get("data") if isinstance(details_resp, dict) else None
    if not isinstance(details, dict):
        raise RuntimeError(
            f"Salla Order Details returned invalid payload: {order_number}"
        )

    actual_reference = str(
        details.get("reference_id") or details.get("order_number") or ""
    ).strip()
    if actual_reference and actual_reference != order_number:
        raise RuntimeError(
            "Salla Order Details reference mismatch: "
            f"expected={order_number} actual={actual_reference}"
        )

    items, shipments = await asyncio.gather(
        _fetch_salla_order_items(
            db,
            user_id,
            internal_id,
        ),
        _fetch_salla_shipment_details(
            db,
            user_id,
            internal_id,
            details.get("shipments"),
        ),
    )
    items = await _enrich_missing_order_item_skus(db, user_id, items)

    enriched_details = dict(details)
    enriched_details = await _enrich_order_receiving_bank(
        db, user_id, enriched_details)
    enriched_details["items"] = items
    # Preserve an authoritative empty list so cancelled labels cannot survive
    # from an embedded historical shipment snapshot.
    enriched_details["shipments"] = shipments

    return enriched_details


async def fetch_single_order_status(
    db,
    user_id: str,
    order_number: str,
) -> dict:
    """Read the current Salla order/payment facts without persisting them.

    This is the shared narrow network boundary for both the guarded sender and
    the operator's read-only payment check. It never writes to MongoDB and it
    never calls Qoyod.
    """
    order_number = str(order_number or "").strip()
    if not order_number:
        return {
            "ok": False,
            "found": False,
            "error": "missing_order_number",
            "stage": "validate_order_number",
        }

    stage = "search_order"
    try:
        search_resp = await call_salla(
            db,
            user_id,
            "GET",
            "/orders",
            params={
                "reference_id": order_number,
                "format": "light",
                "per_page": 10,
            },
        )
        rows = search_resp.get("data") if isinstance(search_resp, dict) else None
        if not isinstance(rows, list):
            rows = []

        match = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            reference_id = str(row.get("reference_id") or "").strip()
            row_id = str(row.get("id") or "").strip()
            if reference_id == order_number or row_id == order_number:
                match = row
                break
        if match is None:
            return {
                "ok": True,
                "found": False,
                "error": "not_found_in_salla",
                "stage": stage,
            }

        internal_id = str(match.get("id") or "").strip()
        if not internal_id:
            raise RuntimeError(
                f"Salla search result missing internal id: {order_number}"
            )

        stage = "fetch_order_status"
        details_resp = await call_salla(
            db,
            user_id,
            "GET",
            f"/orders/{internal_id}",
        )
        details = (
            details_resp.get("data")
            if isinstance(details_resp, dict)
            else None
        )
        if not isinstance(details, dict):
            raise RuntimeError(
                f"Salla Order Details returned invalid payload: {order_number}"
            )

        actual_reference = str(
            details.get("reference_id")
            or details.get("order_number")
            or ""
        ).strip()
        if actual_reference and actual_reference != order_number:
            raise RuntimeError(
                "Salla Order Details reference mismatch: "
                f"expected={order_number} actual={actual_reference}"
            )

        stage = "map_order_status"
        doc = _salla_order_to_doc(details)
        current_slug = str(
            doc.get("order_status_slug")
            or doc.get("order_status")
            or ""
        ).strip().lower()
        if not current_slug:
            raise RuntimeError(
                f"Salla Order Details missing status: {order_number}"
            )

        return {
            "ok": True,
            "found": True,
            "order_number": order_number,
            "order": doc,
            "status_slug": current_slug,
            "status_native": doc.get("order_status"),
        }
    except SallaError as exc:
        return {
            "ok": False,
            "found": False,
            "error": str(exc),
            "stage": stage,
            "exception_type": type(exc).__name__,
            "needs_reauth": exc.needs_reauth,
        }
    except Exception as exc:
        return {
            "ok": False,
            "found": False,
            "error": "status_refresh_stage_failed",
            "stage": stage,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc)[:300],
            "order_number": order_number,
        }


async def refresh_single_order_status(
    db,
    user_id: str,
    order_number: str,
) -> dict:
    """Refresh the persisted Plan-B snapshot used by the guarded sender."""
    result = await fetch_single_order_status(db, user_id, order_number)
    if not result.get("ok") or not result.get("found"):
        return result

    doc = result.get("order") or {}
    try:
        snapshot = await _refresh_plan_b_status_snapshot(
            db,
            user_id,
            str(order_number).strip(),
            doc,
        )
    except Exception as exc:
        return {
            "ok": False,
            "found": False,
            "error": "status_refresh_stage_failed",
            "stage": "plan_b_status_snapshot",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc)[:300],
            "order_number": str(order_number).strip(),
        }

    return {
        "ok": True,
        "found": True,
        "plan_b_status_snapshot": snapshot,
        "status_slug": snapshot.get("status_slug") or result.get("status_slug"),
        "status_native": snapshot.get("status_native"),
    }


async def resync_single_order(db, user_id: str, order_number: str) -> dict:
    """Pull one authoritative Order Details payload from Salla.

    This path updates Mezan storage only. It performs no Qoyod API calls.

    On an unexpected failure, return a safe stage diagnostic so operators
    can identify the failing boundary without exposing tokens or raw payloads.
    """
    order_number = str(order_number).strip()
    if not order_number:
        return {
            "ok": False,
            "found": False,
            "error": "missing_order_number",
        }

    stage = "snapshot_before"

    try:
        before = await db.unified_orders.find_one(
            {
                "user_id": user_id,
                "order_number": order_number,
            },
            {
                "_id": 0,
                "order_status": 1,
                "payment_status": 1,
                "total_amount": 1,
                "payment_method": 1,
                "updated_at": 1,
                "products": 1,
                "total_product_cost": 1,
            },
        )

        stage = "fetch_order_details"

        try:
            raw = await _fetch_salla_order_details(
                db,
                user_id,
                order_number,
            )
        except SallaError as exc:
            return {
                "ok": False,
                "found": False,
                "error": str(exc),
                "stage": stage,
                "exception_type": type(exc).__name__,
                "needs_reauth": exc.needs_reauth,
            }

        if raw is None:
            return {
                "ok": True,
                "found": False,
                "before": before,
                "error": "not_found_in_salla",
                "stage": stage,
            }

        stage = "map_order"

        def _container_shape(value):
            if isinstance(value, list):
                first_keys = []
                if value and isinstance(value[0], dict):
                    first_keys = sorted(str(key) for key in value[0].keys())
                return {
                    "type": "list",
                    "count": len(value),
                    "first_item_keys": first_keys,
                }

            if isinstance(value, dict):
                return {
                    "type": "dict",
                    "count": len(value),
                    "keys": sorted(str(key) for key in value.keys()),
                }

            if value is None:
                return {
                    "type": "missing",
                    "count": 0,
                }

            return {
                "type": type(value).__name__,
                "count": 0,
            }

        raw_shape = {
            "top_level_keys": sorted(str(key) for key in raw.keys()),
            "candidate_containers": {
                key: _container_shape(raw.get(key))
                for key in (
                    "items",
                    "products",
                    "order_items",
                    "lines",
                    "line_items",
                    "data",
                )
            },
        }

        doc = _salla_order_to_doc(raw)

        if not doc.get("order_number"):
            return {
                "ok": False,
                "found": False,
                "error": "order_number_missing_in_payload",
                "stage": stage,
            }

        current_slug = str(
            doc.get("order_status_slug")
            or doc.get("order_status")
            or ""
        ).strip().lower()

        if not current_slug:
            raise RuntimeError(
                f"Salla Order Details missing status: {order_number}"
            )

        stage = "upsert_order"
        res = await upsert_order(
            db,
            user_id,
            doc["order_number"],
            doc,
            source="salla_direct",
            raw=raw,
        )

        stage = "plan_b_snapshot"
        plan_b_snapshot = await _refresh_plan_b_status_snapshot(
            db,
            user_id,
            doc["order_number"],
            doc,
        )

        stage = "load_post_upsert"
        post = await db.unified_orders.find_one(
            {
                "user_id": user_id,
                "order_number": order_number,
            },
            {
                "_id": 0,
                "order_number": 1,
                "products": 1,
                "total_amount": 1,
                "total_product_cost": 1,
            },
        )

        adjustment = None

        if post is not None:
            # COGS enrichment is non-critical and must never fail resync.
            try:
                from product_costs import attach_cost_to_order_doc

                cost_patch = await attach_cost_to_order_doc(
                    db,
                    user_id,
                    post,
                )

                await db.unified_orders.update_one(
                    {
                        "user_id": user_id,
                        "order_number": order_number,
                    },
                    {"$set": cost_patch},
                )

                post["total_product_cost"] = cost_patch.get(
                    "total_product_cost"
                )
            except Exception:
                pass

            stage = "adjustment_audit"
            adjustment = await _record_order_adjustment(
                db,
                user_id,
                order_number,
                before,
                post,
                reason="resync",
            )

        stage = "final_snapshot"
        after = await db.unified_orders.find_one(
            {
                "user_id": user_id,
                "order_number": order_number,
            },
            {
                "_id": 0,
                "raw_by_source": 0,
                "raw_by_user": 0,
                "products": 0,
            },
        )

        return {
            "ok": True,
            "found": True,
            "created": bool(res.get("created")),
            "updated": not bool(res.get("created")),
            "before": before,
            "after": after,
            "adjustment": adjustment,
            "plan_b_status_snapshot": plan_b_snapshot,
            "salla_raw_shape": raw_shape,
        }

    except Exception as exc:
        return {
            "ok": False,
            "found": False,
            "error": "resync_stage_failed",
            "stage": stage,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc)[:300],
            "order_number": order_number,
            "salla_raw_shape": (
                raw_shape
                if "raw_shape" in locals()
                else None
            ),
        }


def _summarise_items(products) -> list[dict]:
    """Compact representation of an order's items used for diffing.

    Each entry: { key, name, sku, product_id, quantity, price }.
    `key` = first non-empty of (sku, product_id, name) — used to align
    rows between two snapshots.
    """
    out: list[dict] = []
    for p in (products or []):
        sku = str(p.get("sku") or "").strip()
        pid = str(p.get("product_id") or p.get("id") or "").strip()
        name = str(p.get("name") or "").strip()
        key = sku or pid or name
        if not key:
            continue
        try:
            qty = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            price = float(p.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        out.append({
            "key": key, "name": name, "sku": sku, "product_id": pid,
            "quantity": qty, "price": price,
        })
    return out


def _diff_items(old_items: list[dict], new_items: list[dict]) -> dict:
    """Return { added, removed, modified } between two item snapshots."""
    by_key_old = {it["key"]: it for it in old_items}
    by_key_new = {it["key"]: it for it in new_items}
    added: list[dict] = []
    removed: list[dict] = []
    modified: list[dict] = []
    for k, n in by_key_new.items():
        if k not in by_key_old:
            added.append(n)
        else:
            o = by_key_old[k]
            if (round(float(o.get("quantity") or 0), 4)
                    != round(float(n.get("quantity") or 0), 4)
                    or round(float(o.get("price") or 0), 2)
                    != round(float(n.get("price") or 0), 2)):
                modified.append({
                    "key": k, "name": n.get("name") or o.get("name"),
                    "before": {"quantity": o.get("quantity"),
                               "price": o.get("price")},
                    "after":  {"quantity": n.get("quantity"),
                               "price": n.get("price")},
                })
    for k, o in by_key_old.items():
        if k not in by_key_new:
            removed.append(o)
    return {"added": added, "removed": removed, "modified": modified}


async def _record_order_adjustment(
    db, user_id: str, order_number: str,
    before: dict | None, after: dict | None, reason: str = "resync",
) -> dict | None:
    """Iter-91 Phase 2 — persist a diff row in `order_adjustments` whenever
    a resync (or any future hook) detects a meaningful change.

    Meaningful change = total_amount differs OR items list differs.
    Returns the stored row dict (or None when no change was detected).
    """
    if before is None or after is None:
        return None

    old_total = round(float(before.get("total_amount") or 0), 2)
    new_total = round(float(after.get("total_amount") or 0), 2)
    old_items = _summarise_items(before.get("products"))
    new_items = _summarise_items(after.get("products"))
    items_diff = _diff_items(old_items, new_items)
    items_changed = bool(
        items_diff["added"] or items_diff["removed"] or items_diff["modified"]
    )
    total_changed = (old_total != new_total)
    if not total_changed and not items_changed:
        return None

    old_cogs = round(float(before.get("total_product_cost") or 0), 2)
    new_cogs = round(float(after.get("total_product_cost") or 0), 2)

    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "order_number": order_number,
        "reason": reason,
        "old_total": old_total,
        "new_total": new_total,
        "delta_total": round(new_total - old_total, 2),
        "old_cogs": old_cogs,
        "new_cogs": new_cogs,
        "delta_cogs": round(new_cogs - old_cogs, 2),
        "items_changed": items_changed,
        "total_changed": total_changed,
        "items_diff": items_diff,
        "created_at": _now(),
    }
    await db.order_adjustments.insert_one(row)
    row.pop("_id", None)
    return row


async def run_products_sync(db, user_id: str) -> dict:
    """Pull products into salla_products collection (cached metadata).

    We DON'T merge products into the existing `product_costs` catalogue
    (that's user-curated); instead we keep a separate cache the UI can
    use to lookup product names/images by `product_id`.
    """
    log_id = await create_sync_log(db, user_id, "products")
    created = 0
    updated = 0
    pages_fetched = 0
    errors_count = 0
    errors_sample: list[dict] = []

    try:
        page = 1
        while page <= MAX_PRODUCT_PAGES:
            params = {"page": page, "per_page": PRODUCTS_PER_PAGE}
            try:
                resp = await call_salla(db, user_id, "GET", "/products", params=params)
            except SallaError as e:
                errors_count += 1
                errors_sample.append({"page": page, "error": str(e)[:300]})
                await finish_sync_log(db, log_id, "failed", extra={
                    "created": created, "updated": updated,
                    "errors_count": errors_count, "errors_sample": errors_sample[:20],
                    "pages_fetched": pages_fetched, "last_error": str(e)[:500],
                })
                raise

            data = resp.get("data") or []
            pages_fetched += 1
            if not data:
                break

            for prod in data:
                if not isinstance(prod, dict):
                    continue
                pid = str(prod.get("id") or "")
                if not pid:
                    continue
                doc = {
                    "user_id": user_id,
                    "product_id": pid,
                    "name": prod.get("name") or "",
                    "sku": prod.get("sku") or "",
                    "status": (prod.get("status") or "").lower(),
                    "price": _money((prod.get("price") or {}).get("amount") if isinstance(prod.get("price"), dict) else prod.get("price")),
                    "main_image": prod.get("main_image") or "",
                    "thumbnail": prod.get("thumbnail") or "",
                    "images": prod.get("images") if isinstance(prod.get("images"), list) else [],
                    "gallery_refreshed_at": _now(),
                    "url": prod.get("url") or "",
                    "updated_at": _now(),
                }
                res = await db.salla_products.update_one(
                    {"user_id": user_id, "product_id": pid},
                    {"$set": doc, "$setOnInsert": {"created_at": _now()}},
                    upsert=True,
                )
                if res.upserted_id:
                    created += 1
                else:
                    updated += 1

            pagination = resp.get("pagination") or {}
            total_pages = int(pagination.get("totalPages") or pagination.get("total_pages") or 0)
            if total_pages and page >= total_pages:
                break
            if len(data) < PRODUCTS_PER_PAGE:
                break
            page += 1
            await asyncio.sleep(0.15)

        await finish_sync_log(db, log_id, "completed", extra={
            "created": created, "updated": updated,
            "errors_count": errors_count, "errors_sample": errors_sample[:20],
            "pages_fetched": pages_fetched,
        })
    except Exception as exc:
        await finish_sync_log(db, log_id, "failed", extra={
            "created": created, "updated": updated,
            "errors_count": errors_count + 1,
            "errors_sample": (errors_sample + [{"error": str(exc)[:300]}])[:20],
            "pages_fetched": pages_fetched,
            "last_error": str(exc)[:500],
        })
        raise

    return {
        "log_id": log_id, "created": created, "updated": updated,
        "errors_count": errors_count, "pages_fetched": pages_fetched,
    }


# ── Sources comparison report ────────────────────────────────────────────
async def compute_sources_comparison(db, user_id: str, *, from_date: Optional[str] = None,
                                     to_date: Optional[str] = None) -> dict:
    """Group unified_orders by their data sources and return counts +
    totals so the merchant can verify Salla Direct vs Make vs Excel."""
    match: dict = {"user_id": user_id}
    if from_date:
        match["order_date"] = {"$gte": from_date}
    if to_date:
        match.setdefault("order_date", {})["$lte"] = to_date

    # Aggregate by the touched-source flags
    pipeline = [
        {"$match": match},
        {"$project": {
            "order_number": 1,
            "total_amount": {"$ifNull": ["$total_amount", 0]},
            "data_source": 1,
            "has_make": {"$cond": [{"$ifNull": ["$last_make_update_at", False]}, 1, 0]},
            "has_excel": {"$cond": [{"$ifNull": ["$last_excel_import_at", False]}, 1, 0]},
            "has_salla": {"$cond": [{"$gt": [{"$type": "$raw_by_source.salla_direct"}, "missing"]}, 1, 0]},
        }},
    ]
    cursor = db.unified_orders.aggregate(pipeline)
    rows = [r async for r in cursor]

    def _bucket():
        return {"orders": 0, "amount": 0.0}

    by_source = {
        "make_only": _bucket(),
        "excel_only": _bucket(),
        "salla_only": _bucket(),
        "make_and_salla": _bucket(),
        "excel_and_salla": _bucket(),
        "make_excel_and_salla": _bucket(),
        "make_and_excel": _bucket(),
        "unknown": _bucket(),
    }
    grand = _bucket()
    in_salla_set: set[str] = set()
    in_make_set: set[str] = set()
    in_excel_set: set[str] = set()

    for r in rows:
        m, e, s = bool(r.get("has_make")), bool(r.get("has_excel")), bool(r.get("has_salla"))
        if m and e and s:
            key = "make_excel_and_salla"
        elif m and s:
            key = "make_and_salla"
        elif e and s:
            key = "excel_and_salla"
        elif m and e:
            key = "make_and_excel"
        elif m:
            key = "make_only"
        elif e:
            key = "excel_only"
        elif s:
            key = "salla_only"
        else:
            key = "unknown"
        bkt = by_source[key]
        bkt["orders"] += 1
        bkt["amount"] += float(r.get("total_amount") or 0)
        grand["orders"] += 1
        grand["amount"] += float(r.get("total_amount") or 0)
        ordn = str(r.get("order_number") or "")
        if s:
            in_salla_set.add(ordn)
        if m:
            in_make_set.add(ordn)
        if e:
            in_excel_set.add(ordn)

    # Round amounts
    for v in by_source.values():
        v["amount"] = round(v["amount"], 2)
    grand["amount"] = round(grand["amount"], 2)

    return {
        "from_date": from_date,
        "to_date": to_date,
        "totals": grand,
        "by_combination": by_source,
        "per_source_totals": {
            "make": {
                "orders": sum(v["orders"] for k, v in by_source.items() if "make" in k),
                "amount": round(sum(v["amount"] for k, v in by_source.items() if "make" in k), 2),
            },
            "excel": {
                "orders": sum(v["orders"] for k, v in by_source.items() if "excel" in k),
                "amount": round(sum(v["amount"] for k, v in by_source.items() if "excel" in k), 2),
            },
            "salla_direct": {
                "orders": sum(v["orders"] for k, v in by_source.items() if "salla" in k),
                "amount": round(sum(v["amount"] for k, v in by_source.items() if "salla" in k), 2),
            },
        },
        # Set-diff helpers: orders Salla has but Make/Excel don't
        "missing_from_make": sorted(in_salla_set - in_make_set)[:50],
        "missing_from_salla": sorted((in_make_set | in_excel_set) - in_salla_set)[:50],
        "missing_from_make_count": len(in_salla_set - in_make_set),
        "missing_from_salla_count": len((in_make_set | in_excel_set) - in_salla_set),
    }


async def ensure_sync_indexes(db) -> None:
    await db.salla_sync_logs.create_index([("user_id", 1), ("started_at", -1)])
    await db.salla_products.create_index(
        [("user_id", 1), ("product_id", 1)], unique=True
    )
