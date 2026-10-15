"""Read-only Merchant Center supplemental feed for Mezan Google taxonomy.

Salla's public product writer does not expose Google Product Category.  This
router keeps Salla as the primary product source and prepares only the two
columns required by a Merchant Center supplemental source: the exact Google
offer ID and ``google_product_category``.  It never creates a data source and
never writes to Google, Salla, or the Mezan catalog.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from product_google_taxonomy_salla_publish import APPROVED_SOURCES
from product_v2_routes import PRODUCTS


MERCHANT_PRODUCTS_URL = (
    "https://merchantapi.googleapis.com/products/v1/accounts/{account_id}/products"
)
MAX_GOOGLE_PRODUCTS = 10_000


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _approved_filter(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "salla_product_id": {"$nin": [None, ""]},
        "google_category": {"$nin": [None, ""]},
        "classification_source": {"$in": list(APPROVED_SOURCES)},
    }


def match_supplemental_feed_rows(
    approved_rows: list[dict[str, Any]],
    merchant_products: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match Mezan rows to Merchant offer IDs without guessing identifiers."""
    exact_offer_ids = {
        _text(product.get("offerId")): _text(product.get("offerId"))
        for product in merchant_products
        if _text(product.get("offerId"))
    }
    folded: dict[str, list[str]] = {}
    for offer_id in exact_offer_ids:
        folded.setdefault(offer_id.casefold(), []).append(offer_id)

    matched: list[dict[str, str]] = []
    unmatched: list[dict[str, Any]] = []
    seen_offer_ids: set[str] = set()
    for row in approved_rows:
        candidates = [
            _text(row.get("sku")),
            _text(row.get("salla_product_id")),
        ]
        offer_id = next(
            (exact_offer_ids[value] for value in candidates if value in exact_offer_ids),
            "",
        )
        if not offer_id:
            for value in candidates:
                values = folded.get(value.casefold()) or []
                if len(values) == 1:
                    offer_id = values[0]
                    break

        category = _text(row.get("google_category_id") or row.get("google_category"))
        if offer_id and category and offer_id not in seen_offer_ids:
            seen_offer_ids.add(offer_id)
            matched.append({
                "id": offer_id,
                "google_product_category": category,
            })
        else:
            unmatched.append({
                "mezan_product_id": row.get("mezan_product_id") or row.get("id"),
                "salla_product_id": _text(row.get("salla_product_id")),
                "sku": _text(row.get("sku")),
            })

    matched.sort(key=lambda row: row["id"].casefold())
    return {
        "matched": matched,
        "unmatched": unmatched,
        "merchant_products": len(merchant_products),
    }


def supplemental_feed_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=["id", "google_product_category"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + buffer.getvalue()


async def _list_merchant_products(db: Any, user_id: str) -> list[dict[str, Any]]:
    # Import lazily so Product V2 startup stays independent of the much larger
    # integrations package graph. The feed is the only path that needs Google.
    from integrations_control_center.google_merchant_registration import (
        _fresh_google_context,
        _merchant_account_id,
    )

    context = await _fresh_google_context(db, user_id)
    account_id = _merchant_account_id()
    headers = {"Authorization": f"Bearer {context['access_token']}"}
    products: list[dict[str, Any]] = []
    page_token = ""
    async with httpx.AsyncClient(timeout=30.0) as client:
        while len(products) < MAX_GOOGLE_PRODUCTS:
            params: dict[str, Any] = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            try:
                response = await client.get(
                    MERCHANT_PRODUCTS_URL.format(account_id=account_id),
                    headers=headers,
                    params=params,
                )
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "code": "google_merchant_products_network_error",
                        "message": "تعذر قراءة معرفات منتجات Merchant Center.",
                    },
                ) from exc
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": f"google_merchant_products_http_{response.status_code}",
                        "message": "تعذر قراءة منتجات Merchant Center بالصلاحية الحالية.",
                    },
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "code": "google_merchant_products_invalid_json",
                        "message": "أعاد Merchant Center استجابة غير صالحة.",
                    },
                ) from exc
            products.extend(
                product
                for product in (payload.get("products") or [])
                if isinstance(product, dict)
            )
            page_token = _text(payload.get("nextPageToken"))
            if not page_token:
                break
    return products[:MAX_GOOGLE_PRODUCTS]


async def _build_feed(db: Any, user_id: str) -> dict[str, Any]:
    approved_rows = await db[PRODUCTS].find(
        _approved_filter(user_id),
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "sku": 1,
            "google_category": 1,
            "google_category_id": 1,
        },
    ).to_list(length=MAX_GOOGLE_PRODUCTS)
    merchant_products = await _list_merchant_products(db, user_id)
    result = match_supplemental_feed_rows(approved_rows, merchant_products)
    result["approved"] = len(approved_rows)
    return result


def make_product_google_taxonomy_merchant_feed_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/ai-store-operations/product-intelligence/google-taxonomy/merchant-feed",
        tags=["AI Store Operations"],
    )

    @router.get("/preview")
    async def preview(user: dict = Depends(current_user)) -> dict[str, Any]:
        result = await _build_feed(db, str(user["id"]))
        return {
            "ok": True,
            "write_performed": False,
            "approved": result["approved"],
            "matched": len(result["matched"]),
            "unmatched": len(result["unmatched"]),
            "merchant_products": result["merchant_products"],
            "ready": bool(result["matched"]),
            "format": "merchant_center_supplemental_csv",
        }

    @router.get(".csv")
    async def download(user: dict = Depends(current_user)) -> Response:
        result = await _build_feed(db, str(user["id"]))
        if not result["matched"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "google_merchant_offer_ids_not_matched",
                    "message": "لم تتطابق معرفات ميزان مع معرفات Merchant Center؛ لم يُنشأ ملف ناقص.",
                },
            )
        content = supplemental_feed_csv(result["matched"])
        return Response(
            content=content.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    'attachment; filename="mezan-google-taxonomy-supplemental-feed.csv"'
                ),
                "X-Mezan-Feed-Rows": str(len(result["matched"])),
            },
        )

    return router
