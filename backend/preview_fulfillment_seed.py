"""Preview-only fulfillment test-data generator.

This router exists on the ``main``/Preview line only.  It creates deterministic
Mezan/Salla-shaped products and orders inside the current Preview database so
review, reviewed-product aggregation, preparation-file generation, PDF layout,
and stage transitions can be exercised without touching Salla, Qoyod, or the
Production database.

Safety rules:
- Runtime must positively identify itself as Preview/development/localhost.
- Only the store owner may create or delete the data.
- Every document is tagged with ``PREVIEW_SEED_ID``.
- Reset deletes only tagged documents and preparation artifacts whose orders
  belong to the seed set.
"""
from __future__ import annotations

import base64
import io
import os
from datetime import timedelta
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict

from order_review_routes import EVENTS, WORKFLOWS, _merchant_user_id, _require_reviewer, _text
from preparation_file_registry import REGISTRY
from reviewed_preparation_batches import BATCHES
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS, PRODUCTS
from tz_utils import riyadh_now_aware


PREVIEW_SEED_ID = "mezan-preview-fulfillment-v1"
PREVIEW_SEED_VERSION = 1
CREATE_CONFIRMATION = "CREATE_PREVIEW_TEST_DATA"
RESET_CONFIRMATION = "DELETE_PREVIEW_TEST_DATA"

PRODUCT_DEFINITIONS = (
    {
        "id": "990001",
        "sku": "PV-NECKLACE-001",
        "name": "سلسال بالاسم — اختبار Preview",
        "image_label": "NECKLACE",
        "image_colors": ("#f3e8ff", "#7e22ce"),
        "category": {
            "root_id": "pv-accessories",
            "root_name": "إكسسوارات Preview",
            "id": "pv-necklaces",
            "name": "سلاسل Preview",
        },
    },
    {
        "id": "990002",
        "sku": "PV-WATCH-001",
        "name": "ساعة نسائية — اختبار Preview",
        "image_label": "WATCH",
        "image_colors": ("#fef3c7", "#b45309"),
        "category": {
            "root_id": "pv-accessories",
            "root_name": "إكسسوارات Preview",
            "id": "pv-watches",
            "name": "ساعات Preview",
        },
    },
    {
        "id": "990003",
        "sku": "PV-BAG-001",
        "name": "شنطة كوتش — اختبار Preview",
        "image_label": "BAG",
        "image_colors": ("#dcfce7", "#047857"),
        "category": {
            "root_id": "pv-fashion",
            "root_name": "أزياء Preview",
            "id": "pv-bags",
            "name": "شنط Preview",
        },
    },
)

# Fifteen order lines total exactly 50 pieces.  Selecting all 50 plus the watch
# creates 16 PDF cards and therefore exercises the second-page boundary.
NECKLACE_QUANTITIES = (4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3)


class PreviewSeedMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal[CREATE_CONFIRMATION, RESET_CONFIRMATION]


def preview_runtime_details(env: dict[str, str] | None = None) -> dict[str, Any]:
    values = dict(os.environ if env is None else env)
    explicit = " ".join(
        _text(values.get(key)).casefold()
        for key in (
            "MEZAN_RUNTIME_ENV",
            "APP_ENV",
            "ENVIRONMENT",
            "NODE_ENV",
        )
    )
    urls = " ".join(
        _text(values.get(key)).casefold()
        for key in (
            "FRONTEND_URL",
            "PUBLIC_URL",
            "BACKEND_URL",
            "REACT_APP_BACKEND_URL",
            "CORS_ORIGINS",
            "EMERGENT_URL",
        )
    )
    joined = f"{explicit} {urls}".strip()
    explicit_preview = any(
        token in {"preview", "development", "dev", "test", "local"}
        for token in explicit.split()
    )
    url_preview = "preview" in urls or "localhost" in urls or "127.0.0.1" in urls
    production_signal = "mezansalla.com" in urls and "preview" not in urls
    allowed = bool((explicit_preview or url_preview) and not production_signal)
    return {
        "available": allowed,
        "runtime_signal": joined[:500],
        "reason": "preview_runtime_confirmed" if allowed else "preview_runtime_not_confirmed",
    }


def _require_preview_owner(user: Any) -> tuple[dict[str, Any], str]:
    reviewer = _require_reviewer(user)
    role = _text(reviewer.get("role")).casefold()
    if role != "owner" and reviewer.get("is_owner") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "preview_seed_owner_required",
                "message": "إنشاء بيانات Preview متاح لمالك المتجر فقط.",
            },
        )
    runtime = preview_runtime_details()
    if not runtime["available"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "preview_seed_runtime_required",
                "message": "تم منع العملية لأن الخادم لم يثبت أنه بيئة Preview.",
            },
        )
    return reviewer, _merchant_user_id(reviewer)


def _image_data_uri(label: str, background: str, accent: str) -> str:
    image = Image.new("RGB", (720, 720), background)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((90, 90, 630, 630), radius=80, fill="white", outline=accent, width=18)
    draw.ellipse((250, 205, 470, 425), fill=background, outline=accent, width=14)
    draw.rectangle((175, 500, 545, 570), fill=accent)
    draw.text((360, 535), label, anchor="mm", fill="white")
    draw.text((360, 650), "MEZAN PREVIEW", anchor="mm", fill=accent)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _product_documents(user_id: str, now_iso: str) -> list[dict[str, Any]]:
    documents = []
    for definition in PRODUCT_DEFINITIONS:
        category = definition["category"]
        image = _image_data_uri(
            definition["image_label"],
            definition["image_colors"][0],
            definition["image_colors"][1],
        )
        root = {
            "id": category["root_id"],
            "name": category["root_name"],
            "children": [{
                "id": category["id"],
                "name": category["name"],
                "parent_id": category["root_id"],
            }],
        }
        documents.append({
            "user_id": user_id,
            "salla_product_id": definition["id"],
            "product_id": definition["id"],
            "sku": definition["sku"],
            "name": definition["name"],
            "main_image": image,
            "images": [image],
            "categories": [{
                "id": category["id"],
                "name": category["name"],
                "parent_id": category["root_id"],
                "path": f"{category['root_name']} / {category['name']}",
            }],
            "raw_salla": {
                "id": int(definition["id"]),
                "name": definition["name"],
                "sku": definition["sku"],
                "main_image": image,
                "images": [image],
                "categories": [root],
            },
            "source": "preview_seed",
            "preview_seed_id": PREVIEW_SEED_ID,
            "preview_seed_version": PREVIEW_SEED_VERSION,
            "created_at": now_iso,
            "updated_at": now_iso,
        })
    return documents


def _item_payload(
    *,
    order_number: str,
    item_id: int,
    product: dict[str, Any],
    quantity: int,
    customer_index: int,
) -> dict[str, Any]:
    image = _image_data_uri(
        product["image_label"],
        product["image_colors"][0],
        product["image_colors"][1],
    )
    names = ("سارة", "نورة", "ريم", "جود", "تالا", "لين", "غلا", "شهد")
    colors = ("ذهبي", "فضي", "ذهبي وردي")
    name_value = names[customer_index % len(names)]
    color_value = colors[customer_index % len(colors)]
    price = 75 if product["id"] == "990001" else (120 if product["id"] == "990002" else 180)
    return {
        "id": item_id,
        "quantity": quantity,
        "product": {
            "id": int(product["id"]),
            "name": product["name"],
            "sku": product["sku"],
            "main_image": image,
            "images": [image],
            "url": f"https://preview.invalid/products/{product['id']}",
        },
        "variant": {
            "id": item_id + 500000,
            "sku": f"{product['sku']}-{color_value}",
        },
        "options": [
            {"name": "اللون", "value": color_value},
            {"name": "المقاس", "value": "متوسط"},
        ],
        "custom_fields": [
            {"name": "الاسم", "value": name_value},
            {"name": "رسالة الإهداء", "value": f"تجربة ملف Preview للطلب {order_number}"},
        ],
        "amounts": {
            "price_without_tax": {"amount": price, "currency": "SAR"},
            "total_discount": {"amount": 0, "currency": "SAR"},
            "tax": {"amount": 0, "currency": "SAR"},
            "total": {"amount": price * quantity, "currency": "SAR"},
        },
    }


def _order_document(
    *,
    user_id: str,
    order_number: str,
    sequence: int,
    product: dict[str, Any],
    quantity: int,
    reviewed: bool,
    created_at,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    item_id = 8800000 + sequence
    item = _item_payload(
        order_number=order_number,
        item_id=item_id,
        product=product,
        quantity=quantity,
        customer_index=sequence,
    )
    order_item_id = f"salla:{order_number}:{item_id}"
    customer_name = f"عميل Preview {sequence:02d}"
    shipping_company = "iMile" if sequence % 3 else "مندوب الرياض"
    status_payload = {
        "slug": "under_review",
        "name": "بانتظار المراجعة",
    }
    if reviewed:
        status_payload["customized"] = {
            "slug": "reviewed",
            "name": "تمت المراجعة",
        }

    subtotal = float(item["amounts"]["total"]["amount"])
    shipping_amount = 25.0
    total = subtotal + shipping_amount
    raw = {
        "id": 1900000000 + sequence,
        "reference_id": order_number,
        "date": {
            "date": created_at.strftime("%Y-%m-%d %H:%M:%S.000000"),
            "timezone": "Asia/Riyadh",
        },
        "status": status_payload,
        "customer": {
            "id": 7900000 + sequence,
            "full_name": customer_name,
            "mobile": f"05099{sequence:05d}"[-10:],
            "email": f"preview{sequence:02d}@example.test",
            "is_guest": False,
        },
        "payment_method": {"code": "mada", "name": "مدى"},
        "payment": {
            "status": "paid",
            "reference": f"PV-TX-{sequence:04d}",
            "paid_at": created_at.isoformat(),
        },
        "amounts": {
            "sub_total": {"amount": subtotal, "currency": "SAR"},
            "shipping_cost": {"amount": shipping_amount, "currency": "SAR"},
            "discounts": {"amount": 0, "currency": "SAR"},
            "tax": {"amount": 0, "currency": "SAR"},
            "total": {"amount": total, "currency": "SAR"},
        },
        "shipments": [{
            "courier": {
                "name": shipping_company,
                "code": "imile" if shipping_company == "iMile" else "riyadh_delegate",
            },
            "status": "created",
            "shipping_address": {
                "country": {"name": "السعودية", "code": "SA"},
                "city": {"name": "الرياض" if sequence % 2 else "جدة"},
                "district": "حي Preview",
                "street": "شارع الاختبار",
                "postal_code": "12345",
            },
        }],
        "items": [item],
        "tags": [{"name": "PREVIEW TEST"}],
        "customer_notes": "طلب تجريبي داخل Preview فقط",
    }
    now_iso = riyadh_now_aware().isoformat()
    document = {
        "user_id": user_id,
        "order_number": order_number,
        "order_date": created_at.isoformat(),
        "order_status": "بانتظار المراجعة",
        "order_status_slug": "under_review",
        "customer_name": customer_name,
        "customer_mobile": raw["customer"]["mobile"],
        "payment_method": "mada",
        "shipping_company": shipping_company,
        "shipping_city": raw["shipments"][0]["shipping_address"]["city"]["name"],
        "total_amount": total,
        "currency": "SAR",
        "data_source": "preview_seed",
        "raw_by_source": {"salla_direct": raw},
        "preview_seed_id": PREVIEW_SEED_ID,
        "preview_seed_version": PREVIEW_SEED_VERSION,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    workflow = None
    if reviewed:
        selected_image = item["product"]["main_image"]
        workflow = {
            "user_id": user_id,
            "order_number": order_number,
            "order_id": str(raw["id"]),
            "stage": "reviewed",
            "revision": 1,
            "items": [{
                "order_item_id": order_item_id,
                "review_status": "reviewed",
                "supplier_export": True,
                "selected_image_url": selected_image,
                "selected_image_source": "preview_seed",
                "preparation_note": "اختبار صورة واسم ومواصفات ملف التجهيز",
                "revision": 1,
                "updated_at": now_iso,
                "updated_by": user_id,
            }],
            "operational_items": [],
            "reviewed_at": now_iso,
            "reviewed_by": user_id,
            "reviewed_by_name": "مالك Preview",
            "created_at": now_iso,
            "updated_at": now_iso,
            "updated_by": user_id,
            "preview_seed_id": PREVIEW_SEED_ID,
            "preview_seed_version": PREVIEW_SEED_VERSION,
        }
    return document, workflow


def build_preview_seed_documents(user_id: str) -> dict[str, Any]:
    now = riyadh_now_aware()
    now_iso = now.isoformat()
    products = _product_documents(user_id, now_iso)
    product_by_id = {row["salla_product_id"]: definition for row, definition in zip(products, PRODUCT_DEFINITIONS)}

    orders: list[dict[str, Any]] = []
    workflows: list[dict[str, Any]] = []
    sequence = 1

    necklace = product_by_id["990001"]
    for index, quantity in enumerate(NECKLACE_QUANTITIES):
        order_number = str(990820001 + index)
        order, workflow = _order_document(
            user_id=user_id,
            order_number=order_number,
            sequence=sequence,
            product=necklace,
            quantity=quantity,
            reviewed=True,
            created_at=now - timedelta(minutes=sequence * 7),
        )
        orders.append(order)
        workflows.append(workflow)
        sequence += 1

    for product_id, quantity, count in (("990002", 10, 1), ("990003", 1, 2)):
        for _ in range(count):
            order_number = str(990820001 + len(orders))
            order, workflow = _order_document(
                user_id=user_id,
                order_number=order_number,
                sequence=sequence,
                product=product_by_id[product_id],
                quantity=quantity,
                reviewed=True,
                created_at=now - timedelta(minutes=sequence * 7),
            )
            orders.append(order)
            workflows.append(workflow)
            sequence += 1

    # Two pending orders to exercise the previous review stage.
    for product_id in ("990001", "990003"):
        order_number = str(990820001 + len(orders))
        order, _ = _order_document(
            user_id=user_id,
            order_number=order_number,
            sequence=sequence,
            product=product_by_id[product_id],
            quantity=1,
            reviewed=False,
            created_at=now - timedelta(minutes=sequence * 7),
        )
        orders.append(order)
        sequence += 1

    return {
        "products": products,
        "orders": orders,
        "workflows": [row for row in workflows if row],
        "order_numbers": [row["order_number"] for row in orders],
        "reviewed_order_numbers": [row["order_number"] for row in orders[:18]],
        "pending_order_numbers": [row["order_number"] for row in orders[18:]],
        "summary": {
            "products": 3,
            "orders": 20,
            "reviewed_orders": 18,
            "pending_orders": 2,
            "reviewed_quantity": 62,
            "necklace_quantity": 50,
            "watch_quantity": 10,
            "bag_quantity": 2,
        },
    }


async def _seed_batch_ids(db: Any, user_id: str, order_numbers: list[str]) -> list[str]:
    rows = await db[BATCHES].find(
        {
            "user_id": user_id,
            "order_numbers": {"$in": order_numbers},
        },
        {"_id": 0, "id": 1},
    ).to_list(500)
    return [_text(row.get("id")) for row in rows if _text(row.get("id"))]


async def reset_preview_seed(db: Any, user_id: str) -> dict[str, int]:
    seed = build_preview_seed_documents(user_id)
    order_numbers = seed["order_numbers"]
    batch_ids = await _seed_batch_ids(db, user_id, order_numbers)

    deleted: dict[str, int] = {}
    deleted["registry"] = int((await db[REGISTRY].delete_many({
        "user_id": user_id,
        "$or": [
            {"batch_id": {"$in": batch_ids}},
            {"preview_seed_id": PREVIEW_SEED_ID},
        ],
    })).deleted_count)
    deleted["allocations"] = int((await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
        "user_id": user_id,
        "$or": [
            {"order_number": {"$in": order_numbers}},
            {"batch_id": {"$in": batch_ids}},
        ],
    })).deleted_count)
    deleted["batches"] = int((await db[BATCHES].delete_many({
        "user_id": user_id,
        "$or": [
            {"id": {"$in": batch_ids}},
            {"preview_seed_id": PREVIEW_SEED_ID},
        ],
    })).deleted_count)
    deleted["events"] = int((await db[EVENTS].delete_many({
        "user_id": user_id,
        "$or": [
            {"order_number": {"$in": order_numbers}},
            {"batch_id": {"$in": batch_ids}},
            {"preview_seed_id": PREVIEW_SEED_ID},
        ],
    })).deleted_count)
    deleted["workflows"] = int((await db[WORKFLOWS].delete_many({
        "user_id": user_id,
        "$or": [
            {"order_number": {"$in": order_numbers}},
            {"preview_seed_id": PREVIEW_SEED_ID},
        ],
    })).deleted_count)
    deleted["orders"] = int((await db.unified_orders.delete_many({
        "user_id": user_id,
        "preview_seed_id": PREVIEW_SEED_ID,
    })).deleted_count)
    deleted["products"] = int((await db[PRODUCTS].delete_many({
        "user_id": user_id,
        "preview_seed_id": PREVIEW_SEED_ID,
    })).deleted_count)
    return deleted


async def preview_seed_status(db: Any, user_id: str) -> dict[str, Any]:
    seed = build_preview_seed_documents(user_id)
    order_numbers = seed["order_numbers"]
    batch_ids = await _seed_batch_ids(db, user_id, order_numbers)
    return {
        "available": preview_runtime_details()["available"],
        "seed_id": PREVIEW_SEED_ID,
        "created": bool(await db.unified_orders.count_documents({
            "user_id": user_id,
            "preview_seed_id": PREVIEW_SEED_ID,
        })),
        "counts": {
            "orders": int(await db.unified_orders.count_documents({
                "user_id": user_id,
                "preview_seed_id": PREVIEW_SEED_ID,
            })),
            "products": int(await db[PRODUCTS].count_documents({
                "user_id": user_id,
                "preview_seed_id": PREVIEW_SEED_ID,
            })),
            "reviewed_workflows": int(await db[WORKFLOWS].count_documents({
                "user_id": user_id,
                "preview_seed_id": PREVIEW_SEED_ID,
                "stage": "reviewed",
            })),
            "batches": len(batch_ids),
        },
        "expected": seed["summary"],
    }


def make_preview_fulfillment_seed_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(
        prefix="/preview-fulfillment-seed-v1",
        tags=["Preview Fulfillment Seed"],
    )

    @router.get("/status")
    async def status_view(user: dict = Depends(current_user)) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        runtime = preview_runtime_details()
        if not runtime["available"]:
            return {
                "available": False,
                "created": False,
                "reason": runtime["reason"],
            }
        return await preview_seed_status(db, _merchant_user_id(reviewer))

    @router.post("/create")
    async def create_seed(
        payload: PreviewSeedMutation,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        if payload.confirmation != CREATE_CONFIRMATION:
            raise HTTPException(status_code=422, detail={"code": "preview_seed_confirmation_required"})
        reviewer, user_id = _require_preview_owner(user)
        deleted = await reset_preview_seed(db, user_id)
        seed = build_preview_seed_documents(user_id)

        if seed["products"]:
            await db[PRODUCTS].insert_many(seed["products"], ordered=True)
        if seed["orders"]:
            await db.unified_orders.insert_many(seed["orders"], ordered=True)
        if seed["workflows"]:
            await db[WORKFLOWS].insert_many(seed["workflows"], ordered=True)
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "event_type": "preview_fulfillment_seed_created",
            "preview_seed_id": PREVIEW_SEED_ID,
            "occurred_at": riyadh_now_aware().isoformat(),
            "actor_id": _text(reviewer.get("id")),
            "summary": seed["summary"],
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
        result = await preview_seed_status(db, user_id)
        result.update({"ok": True, "deleted_before_create": deleted})
        return result

    @router.delete("/reset")
    async def delete_seed(
        payload: PreviewSeedMutation,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        if payload.confirmation != RESET_CONFIRMATION:
            raise HTTPException(status_code=422, detail={"code": "preview_seed_confirmation_required"})
        _reviewer, user_id = _require_preview_owner(user)
        deleted = await reset_preview_seed(db, user_id)
        return {
            "ok": True,
            "available": True,
            "created": False,
            "seed_id": PREVIEW_SEED_ID,
            "deleted": deleted,
        }

    return router


__all__ = [
    "CREATE_CONFIRMATION",
    "NECKLACE_QUANTITIES",
    "PREVIEW_SEED_ID",
    "RESET_CONFIRMATION",
    "build_preview_seed_documents",
    "make_preview_fulfillment_seed_router",
    "preview_runtime_details",
    "reset_preview_seed",
]
