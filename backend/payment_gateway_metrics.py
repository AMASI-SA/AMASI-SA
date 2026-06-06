"""Payment-Gateway Metrics — single source of truth (Iter-81).

Replaces ad-hoc per-page aggregations with one canonical service that
every page in the system reads from. Implements the user's required
priority chain:

    1. Settlement-file actual_* fields (Salla / Tamara / Tabby)
    2. Salla Direct
    3. Make.com
    4. Excel
    5. Estimated commission rates  ← fallback

Each row in the response represents ONE canonical payment method with
the fields the merchant listed: gross / fees / fee_vat / refund_full /
refund_partial / net / expected_in_assets / orders_count /
refund_orders_count / cancelled_orders_count.

The canonical method list is the one the user enumerated:
سلة، مدى، Apple Pay، STC Pay، البطاقة الائتمانية، تمارا، تابي،
إمكان، تحويل بنكي، الدفع عند الاستلام.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user_from_db


# ── Canonical registry ────────────────────────────────────────────────
# Each entry: canonical_key → (display_ar, type, alias_list, estimated_fee_rate)
# Aliases are case/whitespace-insensitive — they cover every variant we
# have observed coming from Excel / Make / Salla / settlement files.
PAYMENT_METHOD_REGISTRY: dict[str, dict] = {
    "salla": {
        "name_ar": "سلة",
        "type": "gateway",
        "aliases": ["salla", "سلة", "سله", "سلّة", "salla_pay", "سلة باي"],
        "estimated_fee_rate": 1.75,
        "estimated_vat_rate": 15.0,
    },
    "mada": {
        "name_ar": "مدى",
        "type": "gateway",
        "aliases": ["mada", "مدى"],
        "estimated_fee_rate": 1.0,
        "estimated_vat_rate": 15.0,
    },
    "applepay": {
        "name_ar": "Apple Pay",
        "type": "gateway",
        "aliases": ["apple pay", "applepay", "apple_pay", "آبل باي", "أبل باي"],
        "estimated_fee_rate": 1.75,
        "estimated_vat_rate": 15.0,
    },
    "stcpay": {
        "name_ar": "STC Pay",
        "type": "gateway",
        "aliases": ["stc pay", "stcpay", "stc_pay", "اس تي سي باي", "stc"],
        "estimated_fee_rate": 1.75,
        "estimated_vat_rate": 15.0,
    },
    "credit_card": {
        "name_ar": "البطاقة الائتمانية",
        "type": "gateway",
        "aliases": ["credit", "credit_card", "creditcard", "credit card",
                    "visa", "mastercard", "البطاقة الائتمانية",
                    "بطاقة ائتمانية", "بطاقة ائتمان"],
        "estimated_fee_rate": 2.75,
        "estimated_vat_rate": 15.0,
    },
    "tamara": {
        "name_ar": "تمارا",
        "type": "gateway",
        "aliases": ["tamara", "تمارا"],
        "estimated_fee_rate": 6.99,
        "estimated_vat_rate": 15.0,
    },
    "tabby": {
        "name_ar": "تابي",
        "type": "gateway",
        "aliases": ["tabby", "تابي"],
        "estimated_fee_rate": 6.99,
        "estimated_vat_rate": 15.0,
    },
    "emkan": {
        "name_ar": "إمكان",
        "type": "gateway",
        "aliases": ["emkan", "إمكان", "امكان"],
        "estimated_fee_rate": 6.99,
        "estimated_vat_rate": 15.0,
    },
    "bank_transfer": {
        "name_ar": "تحويل بنكي",
        "type": "bank",
        "aliases": ["bank transfer", "bank_transfer", "تحويل بنكي",
                    "حوالة بنكية", "حواله بنكيه", "wire transfer"],
        "estimated_fee_rate": 0.0,
        "estimated_vat_rate": 0.0,
    },
    "cod": {
        "name_ar": "الدفع عند الاستلام",
        "type": "cod",
        "aliases": ["cod", "cash on delivery", "الدفع عند الاستلام",
                    "دفع عند الاستلام", "نقدا عند الاستلام", "cash"],
        "estimated_fee_rate": 0.0,
        "estimated_vat_rate": 0.0,
    },
}


def _build_alias_index() -> dict[str, str]:
    """Reverse-lookup table: normalized alias → canonical_key."""
    out: dict[str, str] = {}
    for key, meta in PAYMENT_METHOD_REGISTRY.items():
        out[_fold(key)] = key
        for alias in meta["aliases"]:
            out[_fold(alias)] = key
    return out


def _fold(s: str) -> str:
    """Lowercase + strip + fold Arabic letter variants so 'البطاقة الإئتمانية'
    matches 'البطاقة الائتمانية'. Mirrors payment_methods._normalize_arabic."""
    if not s:
        return ""
    s = s.strip().lower()
    table = str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ـ": "",
    })
    return s.translate(table)


_ALIAS_INDEX = _build_alias_index()


def resolve_canonical(raw: Optional[str]) -> Optional[str]:
    """Map any inbound payment-method string to a canonical_key, or
    None if unrecognized (caller can bucket those under '_other')."""
    if not raw:
        return None
    s = _fold(str(raw))
    if not s:
        return None
    # Exact alias match first
    if s in _ALIAS_INDEX:
        return _ALIAS_INDEX[s]
    # Substring fallback — handles "البطاقة الائتمانية (Visa)" etc.
    for alias, key in _ALIAS_INDEX.items():
        if alias and alias in s:
            return key
    return None


# ── Aggregator ────────────────────────────────────────────────────────
async def compute_metrics(
    db,
    user_id: str,
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """Aggregate unified_orders into per-method metrics with proper
    priority. Returns one row per canonical method (zero rows
    omitted) plus a '_other' bucket for unrecognized methods.

    Priority: when `payment_fee_status == 'actual'` (set by the
    settlement-import service) we use `actual_payment_fee`,
    `actual_payment_vat`, `actual_net_amount`, `actual_refund_amount`,
    `actual_partial_refund_amount`. Otherwise we estimate from
    `total_amount` and the registry's estimated_fee_rate.
    """
    match: dict = {"user_id": user_id}
    date_clause: dict = {}
    if from_date:
        date_clause["$gte"] = from_date
    if to_date:
        date_clause["$lte"] = to_date
    if date_clause:
        match["order_date"] = date_clause

    pipeline = [
        {"$match": match},
        {"$project": {
            "_id": 0,
            "order_status": 1,
            "total_amount": {"$ifNull": ["$total_amount", 0]},
            "payment_method": {"$ifNull": ["$payment_method", ""]},
            "actual_payment_method": {"$ifNull": ["$actual_payment_method", ""]},
            "payment_fee_status": {"$ifNull": ["$payment_fee_status", "estimated"]},
            "actual_payment_fee": {"$ifNull": ["$actual_payment_fee", 0]},
            "actual_payment_vat": {"$ifNull": ["$actual_payment_vat", 0]},
            "actual_net_amount": {"$ifNull": ["$actual_net_amount", None]},
            "actual_refund_amount": {"$ifNull": ["$actual_refund_amount", 0]},
            "actual_partial_refund_amount": {"$ifNull": ["$actual_partial_refund_amount", 0]},
        }},
    ]

    # Per-method buckets
    buckets: dict[str, dict] = {}

    def _zero():
        return {
            "orders_count": 0,
            "refunded_orders_count": 0,
            "cancelled_orders_count": 0,
            "actual_orders_count": 0,
            "gross": 0.0,
            "fees": 0.0,
            "fees_vat": 0.0,
            "refund_full": 0.0,
            "refund_partial": 0.0,
            "net": 0.0,           # gross − fees − vat − refunds
            "expected_in_assets": 0.0,  # net  (settles into the gateway account)
        }

    async for row in db.unified_orders.aggregate(pipeline):
        raw_method = row.get("actual_payment_method") or row.get("payment_method")
        canon = resolve_canonical(raw_method) or "_other"
        bkt = buckets.setdefault(canon, _zero())
        bkt["orders_count"] += 1

        order_status = (row.get("order_status") or "").strip()
        if "ملغ" in order_status or "cancel" in order_status.lower():
            bkt["cancelled_orders_count"] += 1
            # Cancelled orders contribute neither gross nor refunds.
            continue

        gross = float(row.get("total_amount") or 0)
        is_actual = (row.get("payment_fee_status") == "actual")

        if is_actual:
            bkt["actual_orders_count"] += 1
            fee = float(row.get("actual_payment_fee") or 0)
            vat = float(row.get("actual_payment_vat") or 0)
            rfull = float(row.get("actual_refund_amount") or 0)
            rpart = float(row.get("actual_partial_refund_amount") or 0)
            net = row.get("actual_net_amount")
            if net is None:
                net = gross - fee - vat - rfull - rpart
            else:
                net = float(net)
        else:
            # Estimated using registry rates
            meta = PAYMENT_METHOD_REGISTRY.get(canon)
            rate = (meta or {}).get("estimated_fee_rate", 0.0)
            vat_rate = (meta or {}).get("estimated_vat_rate", 0.0)
            fee = round(gross * rate / 100, 4)
            vat = round(fee * vat_rate / 100, 4)
            rfull = 0.0
            rpart = 0.0
            net = gross - fee - vat

        if rfull > 0 or rpart > 0:
            bkt["refunded_orders_count"] += 1

        bkt["gross"] += gross
        bkt["fees"] += fee
        bkt["fees_vat"] += vat
        bkt["refund_full"] += rfull
        bkt["refund_partial"] += rpart
        bkt["net"] += net
        bkt["expected_in_assets"] += net

    # Materialize result rows in registry order (deterministic), drop
    # all-zero buckets so the UI stays uncluttered.
    rows: list[dict] = []
    for key, meta in PAYMENT_METHOD_REGISTRY.items():
        b = buckets.get(key)
        if not b or b["orders_count"] == 0:
            continue
        rows.append({
            "key": key,
            "name_ar": meta["name_ar"],
            "type": meta["type"],
            "orders_count": b["orders_count"],
            "actual_orders_count": b["actual_orders_count"],
            "refunded_orders_count": b["refunded_orders_count"],
            "cancelled_orders_count": b["cancelled_orders_count"],
            "gross": round(b["gross"], 2),
            "fees": round(b["fees"], 2),
            "fees_vat": round(b["fees_vat"], 2),
            "refund_full": round(b["refund_full"], 2),
            "refund_partial": round(b["refund_partial"], 2),
            "refund_total": round(b["refund_full"] + b["refund_partial"], 2),
            "net": round(b["net"], 2),
            "expected_in_assets": round(b["expected_in_assets"], 2),
            "coverage_pct": round(
                (b["actual_orders_count"] / b["orders_count"]) * 100, 2,
            ) if b["orders_count"] else 0.0,
        })

    # Tail bucket for unrecognized methods so totals reconcile even
    # when the merchant has a new gateway not in the registry yet.
    other = buckets.get("_other")
    if other and other["orders_count"] > 0:
        rows.append({
            "key": "_other",
            "name_ar": "أخرى",
            "type": "unknown",
            "orders_count": other["orders_count"],
            "actual_orders_count": other["actual_orders_count"],
            "refunded_orders_count": other["refunded_orders_count"],
            "cancelled_orders_count": other["cancelled_orders_count"],
            "gross": round(other["gross"], 2),
            "fees": round(other["fees"], 2),
            "fees_vat": round(other["fees_vat"], 2),
            "refund_full": round(other["refund_full"], 2),
            "refund_partial": round(other["refund_partial"], 2),
            "refund_total": round(other["refund_full"] + other["refund_partial"], 2),
            "net": round(other["net"], 2),
            "expected_in_assets": round(other["expected_in_assets"], 2),
            "coverage_pct": 0.0,
        })

    totals = {
        "gross": round(sum(r["gross"] for r in rows), 2),
        "fees": round(sum(r["fees"] for r in rows), 2),
        "fees_vat": round(sum(r["fees_vat"] for r in rows), 2),
        "refund_full": round(sum(r["refund_full"] for r in rows), 2),
        "refund_partial": round(sum(r["refund_partial"] for r in rows), 2),
        "refund_total": round(sum(r["refund_total"] for r in rows), 2),
        "net": round(sum(r["net"] for r in rows), 2),
        "orders_count": sum(r["orders_count"] for r in rows),
        "actual_orders_count": sum(r["actual_orders_count"] for r in rows),
        "refunded_orders_count": sum(r["refunded_orders_count"] for r in rows),
        "cancelled_orders_count": sum(r["cancelled_orders_count"] for r in rows),
    }

    return {
        "from_date": from_date,
        "to_date": to_date,
        "rows": rows,
        "totals": totals,
        "registry": [
            {"key": k, "name_ar": v["name_ar"], "type": v["type"],
             "estimated_fee_rate": v["estimated_fee_rate"]}
            for k, v in PAYMENT_METHOD_REGISTRY.items()
        ],
    }


# ── Route ─────────────────────────────────────────────────────────────
def attach_payment_gateway_metrics_routes(api_router: APIRouter, db) -> None:
    router = APIRouter(tags=["payment-gateway-metrics"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("/payment-gateway-metrics")
    async def get_metrics(
        from_date: Optional[str] = Query(default=None),
        to_date: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ):
        return await compute_metrics(db, user["id"], from_date=from_date, to_date=to_date)

    api_router.include_router(router)
