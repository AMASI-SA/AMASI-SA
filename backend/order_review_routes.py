"""Stage-one order review workflow.

This module owns only the review stage.  It deliberately does not create
preparation batches or move items into procurement/shipping.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from order_engine.models import OrderDTO
from order_engine.repository import MongoOrderRepository
from order_engine.service import InvalidOrderCursorError, OrderNotFoundError, get_order, list_orders
from order_item_engine.mapper import map_order_item_identities
from order_engine.product_image_enrichment import enrich_order_item_images
from salla_integration.auto_sync import schedule_salla_auto_sync
from salla_integration.service import SallaError, call_salla
from salla_integration.sync import resync_single_order


WORKFLOWS = "order_review_workflows"
PREFERENCES = "product_option_image_preferences"
EVENTS = "order_review_events"
REVIEWED_STATUS_NAMES = {"تم المراجعة", "تمت المراجعة"}

_PERSONAL_OPTION_HINTS = (
    "اسم", "نقش", "كتابة", "رسالة", "اهداء", "إهداء", "تهنئة", "رقم الجوال",
    "name", "engraving", "inscription", "message", "gift message", "phone",
)
_VISUAL_OPTION_PREFIXES = (
    "لون", "اللون", "مقاس", "المقاس", "خامة", "الخامة", "مادة", "المادة",
    "color", "colour", "size", "material",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _is_personal_option(name: str) -> bool:
    normalized = _normalized(name)
    if any(normalized.startswith(prefix.casefold()) for prefix in _VISUAL_OPTION_PREFIXES):
        return False
    return any(hint.casefold() in normalized for hint in _PERSONAL_OPTION_HINTS)


def build_image_preference_identity(item: Any) -> tuple[str, str, dict[str, str]]:
    """Return stable catalogue key + option signature for learned images.

    Customer-specific text is excluded because it must not create a new image
    mapping for every engraving/name/message.
    """
    product_key = next(
        (
            f"{kind}:{_text(value)}"
            for kind, value in (
                ("variant", getattr(item, "variant_id", None)),
                ("product", getattr(item, "product_id", None)),
                ("parent", getattr(item, "parent_product_id", None)),
                ("sku", getattr(item, "sku", None)),
            )
            if _text(value)
        ),
        f"item:{_text(getattr(item, 'order_item_id', None))}",
    )

    option_values: dict[str, str] = {}
    for option in getattr(item, "options", None) or []:
        name = _text(getattr(option, "name", None))
        value = _text(getattr(option, "value", None))
        if name and value and not _is_personal_option(name):
            option_values[_normalized(name)] = _normalized(value)

    for name in ("color", "size", "material"):
        value = _text(getattr(item, name, None))
        if value:
            option_values.setdefault(name, _normalized(value))

    canonical = json.dumps(option_values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hashlib.sha256(f"{product_key}|{canonical}".encode("utf-8")).hexdigest()
    return product_key, signature, option_values


def _can_review(user: Any) -> bool:
    if not isinstance(user, dict):
        return False
    role = _normalized(user.get("role"))
    if role == "owner" or user.get("is_owner") is True:
        return True
    if "orders.manage" in set(user.get("denied_permissions") or []):
        return False
    if role in {"admin", "operations"}:
        return True
    return "orders.manage" in set(user.get("extra_permissions") or [])


def _require_reviewer(user: Any) -> dict:
    if not _can_review(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "order_review_permission_required", "message": "تحتاج صلاحية إدارة الطلبات لمراجعة الطلب."},
        )
    return user


def _merchant_user_id(user: dict[str, Any]) -> str:
    """Resolve the store data owner while preserving the employee actor id."""
    if _normalized(user.get("role")) == "owner" or user.get("is_owner") is True:
        return _text(user.get("id"))
    owner_id = _text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "employee_store_not_linked",
                "message": "حساب الموظف غير مربوط بمالك المتجر؛ لا يمكن فتح بيانات الطلبات بأمان.",
            },
        )
    return owner_id


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _reviewed_status_id(response: Any) -> Any:
    for row in _walk_dicts(response):
        if _text(row.get("name")) not in REVIEWED_STATUS_NAMES:
            continue
        status_id = row.get("id") or row.get("status_id")
        if status_id not in (None, ""):
            return int(status_id) if _text(status_id).isdigit() else status_id
    return None


class ReviewItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    selected_image_url: Optional[str] = Field(default=None, max_length=3000)
    preparation_note: Optional[str] = Field(default=None, max_length=1200)
    internal_note: Optional[str] = Field(default=None, max_length=2000)


class CompleteReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


async def _ensure_indexes(db: Any) -> None:
    await db[WORKFLOWS].create_index([("user_id", 1), ("order_number", 1)], unique=True)
    await db[PREFERENCES].create_index(
        [("user_id", 1), ("product_key", 1), ("option_signature", 1)], unique=True
    )
    await db[EVENTS].create_index([("user_id", 1), ("order_number", 1), ("occurred_at", -1)])


def _state_map(workflow: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("order_item_id")): dict(row)
        for row in (workflow or {}).get("items", [])
        if isinstance(row, dict) and _text(row.get("order_item_id"))
    }


async def _preference_map(db: Any, user_id: str, identities: list[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    keys = [build_image_preference_identity(item)[:2] for item in identities]
    clauses = [{"product_key": product_key, "option_signature": signature} for product_key, signature in keys]
    if not clauses:
        return {}
    docs = await db[PREFERENCES].find(
        {"user_id": user_id, "$or": clauses}, {"_id": 0}
    ).to_list(len(clauses))
    return {(doc["product_key"], doc["option_signature"]): doc for doc in docs}


def _item_view(item: Any, saved: Optional[dict[str, Any]], preference: Optional[dict[str, Any]]) -> dict[str, Any]:
    saved = saved or {}
    gallery = []
    for url in [*(getattr(item, "image_urls", None) or []), getattr(item, "image_url", None)]:
        normalized = _text(url)
        if normalized and normalized not in gallery:
            gallery.append(normalized)

    if _text(saved.get("selected_image_url")):
        selected = _text(saved["selected_image_url"])
        source = saved.get("selected_image_source") or "manual"
    elif (
        preference
        and _text(preference.get("selected_image_url"))
        and _text(preference.get("selected_image_url")) in gallery
    ):
        selected = _text(preference["selected_image_url"])
        source = "learned_preference"
    else:
        selected = _text(getattr(item, "image_url", None)) or (gallery[0] if gallery else "")
        source = "source_default"

    return {
        **item.model_dump(mode="json"),
        "gallery": gallery,
        "review_status": saved.get("review_status") or "pending_review",
        "selected_image_url": selected or None,
        "selected_image_source": source,
        "preparation_note": saved.get("preparation_note") or "",
        "internal_note": saved.get("internal_note") or "",
        "item_revision": int(saved.get("revision") or 0),
    }


async def _review_item_identities(db: Any, user_id: str, order: OrderDTO) -> list[Any]:
    """Build review items and refresh incomplete galleries once from Salla.

    Normal order reads remain local. The review stage is the one place where
    seeing every catalogue image is operationally required, so a product with
    a one-image cache is refreshed and the full gallery is persisted locally.
    """
    identities = await enrich_order_item_images(
        db, user_id=user_id, items=map_order_item_identities(order)
    )
    candidates = {
        (_text(getattr(item, "product_id", None)), _text(getattr(item, "sku", None)))
        for item in identities
        if _text(getattr(item, "product_id", None)) or _text(getattr(item, "sku", None))
    }
    incomplete_candidates = {
        (_text(getattr(item, "product_id", None)), _text(getattr(item, "sku", None)))
        for item in identities
        if len({
            _text(url)
            for url in [
                getattr(item, "image_url", None),
                *(getattr(item, "image_urls", None) or []),
            ]
            if _text(url)
        }) <= 1
    }
    product_ids = [product_id for product_id, _ in candidates if product_id]
    skus = [sku for _, sku in candidates if sku]
    clauses = []
    if product_ids:
        clauses.append({"product_id": {"$in": product_ids}})
    if skus:
        clauses.append({"sku": {"$in": skus}})
    fresh_product_ids: set[str] = set()
    fresh_skus: set[str] = set()
    if clauses:
        cursor = db.salla_products.find(
            {"user_id": user_id, "$or": clauses},
            {
                "_id": 0, "product_id": 1, "sku": 1,
                "gallery_refreshed_at": 1, "images": 1,
            },
        )
        async for cached in cursor:
            if not _text(cached.get("gallery_refreshed_at")):
                continue
            # A previous partial sync may have marked a one-image gallery as
            # complete. Stage one needs the full catalogue gallery so the
            # reviewer can choose the image matching the customer's options.
            cached_images = cached.get("images") if isinstance(cached.get("images"), list) else []
            if len(cached_images) <= 1:
                continue
            fresh_product_ids.add(_text(cached.get("product_id")))
            fresh_skus.add(_text(cached.get("sku")))

    targets = {
        (product_id, sku)
        for product_id, sku in candidates
        if (product_id, sku) in incomplete_candidates or not (
            (product_id and product_id in fresh_product_ids)
            or (sku and sku in fresh_skus)
        )
    }
    refreshed = False
    for product_id, sku in sorted(targets):
        product = None
        try:
            if product_id:
                response = await call_salla(db, user_id, "GET", f"/products/{product_id}")
                product = response.get("data") if isinstance(response, dict) else None
            # Some Salla product-detail responses can be partial. Search by
            # exact SKU as a fallback because the listing response includes
            # the complete `images` array used in the merchant catalogue.
            detail_images = product.get("images") if isinstance(product, dict) and isinstance(product.get("images"), list) else []
            if sku and len(detail_images) <= 1:
                response = await call_salla(
                    db, user_id, "GET", "/products",
                    params={"keyword": sku, "per_page": 10},
                )
                rows = response.get("data") if isinstance(response, dict) else []
                product = next(
                    (
                        row for row in rows
                        if isinstance(row, dict) and _normalized(row.get("sku")) == _normalized(sku)
                    ),
                    None,
                )
        except SallaError:
            # If fetching by id failed, still try the SKU listing endpoint.
            if not sku:
                continue
            try:
                response = await call_salla(
                    db, user_id, "GET", "/products",
                    params={"keyword": sku, "per_page": 10},
                )
                rows = response.get("data") if isinstance(response, dict) else []
                product = next(
                    (
                        row for row in rows
                        if isinstance(row, dict) and _normalized(row.get("sku")) == _normalized(sku)
                    ),
                    None,
                )
            except SallaError:
                continue

        if not isinstance(product, dict):
            continue
        images = product.get("images") if isinstance(product.get("images"), list) else []
        cached_product_id = _text(product.get("id")) or product_id
        if not cached_product_id or not (
            images or product.get("main_image") or product.get("thumbnail")
        ):
            continue
        await db.salla_products.update_one(
            {"user_id": user_id, "product_id": cached_product_id},
            {"$set": {
                "user_id": user_id,
                "product_id": cached_product_id,
                "name": _text(product.get("name")),
                "sku": _text(product.get("sku")) or sku,
                "main_image": product.get("main_image") or "",
                "thumbnail": product.get("thumbnail") or "",
                "images": images,
                "gallery_refreshed_at": _now(),
            }, "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
        refreshed = True

    if refreshed:
        identities = await enrich_order_item_images(db, user_id=user_id, items=identities)
    return identities


async def _detail(db: Any, user_id: str, order: OrderDTO) -> dict[str, Any]:
    identities = await _review_item_identities(db, user_id, order)
    workflow = await db[WORKFLOWS].find_one(
        {"user_id": user_id, "order_number": order.order_number}, {"_id": 0}
    )
    states = _state_map(workflow)
    preferences = await _preference_map(db, user_id, identities)
    item_views = []
    for item in identities:
        product_key, signature, _ = build_image_preference_identity(item)
        item_views.append(_item_view(item, states.get(item.order_item_id), preferences.get((product_key, signature))))
    return {
        "order": order.model_dump(mode="json"),
        "stage": (workflow or {}).get("stage") or "pending_review",
        "revision": int((workflow or {}).get("revision") or 0),
        "items": item_views,
    }


async def _refresh_review_source_once(db: Any, user_id: str, order_number: str) -> bool:
    """Refresh the order from Salla once when the reviewer explicitly opens it.

    List polling remains light and never opens Salla Order Details. Opening the
    review drawer is an explicit merchant action, so it is the correct boundary
    for retrieving the receipt image and authoritative line-item options/files.
    Failures are non-blocking: the locally cached review still opens.
    """
    try:
        existing = await db.unified_orders.find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0, "order_review_source_refreshed_at": 1},
        )
        if _text((existing or {}).get("order_review_source_refreshed_at")):
            return False

        result = await resync_single_order(db, user_id, order_number)
        if not isinstance(result, dict) or not result.get("ok") or not result.get("found"):
            return False

        refreshed_at = _now()
        await db.unified_orders.update_one(
            {"user_id": user_id, "order_number": order_number},
            {"$set": {
                "order_review_source_refreshed_at": refreshed_at,
                "order_review_source_refresh_mode": "explicit_review_open",
            }},
        )
        return True
    except Exception:
        return False


async def _sync_salla_reviewed(db: Any, user_id: str, order: OrderDTO) -> tuple[str, Optional[str]]:
    internal_id = _text(order.source.source_order_id) or _text(order.order_id)
    if not internal_id:
        return "pending", "missing_salla_order_id"
    try:
        statuses = await call_salla(db, user_id, "GET", "/orders/statuses")
        status_id = _reviewed_status_id(statuses)
        if status_id is None:
            return "pending", "reviewed_status_not_found"
        await call_salla(db, user_id, "POST", f"/orders/{internal_id}/status", json={"status_id": status_id})
        return "sent", None
    except SallaError as exc:
        return "pending", f"salla_{exc.status_code}"


def make_order_review_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/order-reviews-v1", tags=["order-review-stage-one"])
    repository = MongoOrderRepository(db)

    @router.get("")
    async def list_pending_reviews(
        limit: int = Query(15, ge=1, le=50),
        cursor: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        merchant_id = _merchant_user_id(reviewer)
        # Non-blocking, throttled Salla Direct ingestion. It reads only the
        # light order list and order items, performs no Qoyod API calls, and
        # never delays the local queue response.
        schedule_salla_auto_sync(db, merchant_id)
        try:
            page = await list_orders(
                repository, user_id=merchant_id, limit=limit,
                cursor=cursor, status_group="under_review",
            )
        except InvalidOrderCursorError as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_orders_cursor"}) from exc
        numbers = [order.order_number for order in page.items]
        completed = set()
        if numbers:
            docs = await db[WORKFLOWS].find(
                {"user_id": merchant_id, "order_number": {"$in": numbers}, "stage": "reviewed"},
                {"_id": 0, "order_number": 1},
            ).to_list(len(numbers))
            completed = {_text(doc.get("order_number")) for doc in docs}
        return {
            "items": [order.model_dump(mode="json") for order in page.items if order.order_number not in completed],
            "next_cursor": page.next_cursor,
            "skipped_invalid": page.skipped_invalid,
        }

    @router.get("/{order_number}")
    async def get_review_detail(order_number: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        merchant_id = _merchant_user_id(reviewer)
        try:
            order = await get_order(repository, user_id=merchant_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc

        if await _refresh_review_source_once(db, merchant_id, order_number):
            try:
                order = await get_order(repository, user_id=merchant_id, order_number=order_number)
            except OrderNotFoundError:
                pass
        return await _detail(db, merchant_id, order)

    @router.patch("/{order_number}/items/{order_item_id:path}")
    async def update_review_item(
        order_number: str,
        order_item_id: str,
        payload: ReviewItemPatch,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = str(reviewer["id"])
        await _ensure_indexes(db)
        try:
            order = await get_order(repository, user_id=user_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        identities = await _review_item_identities(db, user_id, order)
        target = next((item for item in identities if item.order_item_id == order_item_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "order_item_not_found"})

        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order.order_number}, {"_id": 0}
        )
        if (workflow or {}).get("stage") == "reviewed":
            raise HTTPException(status_code=409, detail={"code": "review_already_completed", "message": "تمت مراجعة الطلب ولا يمكن تعديل نسخته المعتمدة من هذه المرحلة."})
        revision = int((workflow or {}).get("revision") or 0)
        if revision != payload.expected_revision:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict", "message": "عدّل موظف آخر الطلب؛ حدّث البيانات ثم أعد المحاولة."})

        states = _state_map(workflow)
        current = states.get(order_item_id, {"order_item_id": order_item_id, "review_status": "pending_review", "revision": 0})
        changed = payload.model_fields_set - {"expected_revision"}
        if "selected_image_url" in changed:
            selected = _text(payload.selected_image_url)
            allowed = {_text(url) for url in [*(target.image_urls or []), target.image_url] if _text(url)}
            if not selected or selected not in allowed:
                raise HTTPException(status_code=422, detail={"code": "image_not_in_product_gallery", "message": "اختر صورة من صور هذا المنتج."})
            current["selected_image_url"] = selected
            current["selected_image_source"] = "manual"
        if "preparation_note" in changed:
            current["preparation_note"] = _text(payload.preparation_note)
        if "internal_note" in changed:
            current["internal_note"] = _text(payload.internal_note)
        current.update({
            "order_item_id": order_item_id,
            "review_status": "pending_review",
            "revision": int(current.get("revision") or 0) + 1,
            "updated_at": _now(),
            "updated_by": actor_id,
            "updated_by_name": _text(reviewer.get("name") or reviewer.get("email")),
        })
        states[order_item_id] = current
        new_doc = {
            "user_id": user_id,
            "order_number": order.order_number,
            "order_id": order.order_id,
            "stage": "pending_review",
            "revision": revision + 1,
            "items": list(states.values()),
            "updated_at": _now(),
            "updated_by": actor_id,
        }
        if workflow:
            result = await db[WORKFLOWS].replace_one(
                {"user_id": user_id, "order_number": order.order_number, "revision": revision}, new_doc
            )
            if not result.matched_count:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        else:
            new_doc["created_at"] = new_doc["updated_at"]
            try:
                await db[WORKFLOWS].insert_one(new_doc)
            except DuplicateKeyError as exc:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"}) from exc

        if "selected_image_url" in changed:
            product_key, signature, option_values = build_image_preference_identity(target)
            await db[PREFERENCES].update_one(
                {"user_id": user_id, "product_key": product_key, "option_signature": signature},
                {"$set": {
                    "selected_image_url": current["selected_image_url"],
                    "option_values": option_values,
                    "source_order_number": order.order_number,
                    "source_order_item_id": order_item_id,
                    "updated_at": _now(),
                    "updated_by": actor_id,
                }, "$setOnInsert": {"created_at": _now()}},
                upsert=True,
            )
        await db[EVENTS].insert_one({
            "user_id": user_id, "order_number": order.order_number,
            "order_item_id": order_item_id, "event_type": "review_item_updated",
            "changed_fields": sorted(changed), "occurred_at": _now(), "actor_id": actor_id,
        })
        return await _detail(db, user_id, order)

    @router.post("/{order_number}/complete")
    async def complete_review(
        order_number: str,
        payload: CompleteReviewRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = str(reviewer["id"])
        await _ensure_indexes(db)
        try:
            order = await get_order(repository, user_id=user_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        identities = await _review_item_identities(db, user_id, order)
        if not identities:
            raise HTTPException(status_code=409, detail={"code": "order_has_no_items", "message": "لا يمكن اعتماد طلب بلا منتجات."})
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order.order_number}, {"_id": 0}
        )
        if (workflow or {}).get("stage") == "reviewed":
            return {"ok": True, "already_reviewed": True, "order_number": order.order_number}
        revision = int((workflow or {}).get("revision") or 0)
        if revision != payload.expected_revision:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict", "message": "حدّث بيانات الطلب قبل الاعتماد."})
        states = _state_map(workflow)
        preferences = await _preference_map(db, user_id, identities)
        now = _now()
        frozen_items = []
        for item in identities:
            product_key, signature, _ = build_image_preference_identity(item)
            view = _item_view(item, states.get(item.order_item_id), preferences.get((product_key, signature)))
            frozen_items.append({
                **states.get(item.order_item_id, {}),
                "order_item_id": item.order_item_id,
                "review_status": "reviewed",
                "selected_image_url": view["selected_image_url"],
                "selected_image_source": view["selected_image_source"],
                "preparation_note": view["preparation_note"],
                "internal_note": view["internal_note"],
                "reviewed_at": now,
                "reviewed_by": actor_id,
                "reviewed_by_name": _text(reviewer.get("name") or reviewer.get("email")),
                "revision": int(states.get(item.order_item_id, {}).get("revision") or 0) + 1,
            })

        # The order must remain visible in stage one when Salla rejects or
        # cannot confirm the status transition.  The employee can retry
        # without losing any previously saved images or notes.
        sync_status, sync_error = await _sync_salla_reviewed(db, user_id, order)
        if sync_status != "sent":
            await db[EVENTS].insert_one({
                "user_id": user_id,
                "order_number": order.order_number,
                "event_type": "order_review_salla_sync_failed",
                "error_code": sync_error,
                "occurred_at": _now(),
                "actor_id": actor_id,
            })
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "salla_review_status_sync_failed",
                    "message": "حُفظت الصور والملاحظات، لكن لم تعتمد سلة حالة «تمت المراجعة». بقي الطلب في هذه الصفحة ويمكن إعادة المحاولة.",
                    "reason": sync_error,
                },
            )
        new_doc = {
            "user_id": user_id, "order_number": order.order_number, "order_id": order.order_id,
            "stage": "reviewed", "revision": revision + 1, "items": frozen_items,
            "reviewed_at": now, "reviewed_by": actor_id,
            "reviewed_by_name": _text(reviewer.get("name") or reviewer.get("email")),
            "salla_status_sync": "sent", "salla_status_sync_error": None,
            "salla_status_sync_at": _now(), "updated_at": now, "updated_by": actor_id,
        }
        if workflow:
            result = await db[WORKFLOWS].replace_one(
                {"user_id": user_id, "order_number": order.order_number, "revision": revision}, new_doc
            )
            if not result.matched_count:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        else:
            new_doc["created_at"] = now
            try:
                await db[WORKFLOWS].insert_one(new_doc)
            except DuplicateKeyError as exc:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"}) from exc
        await db[EVENTS].insert_one({
            "user_id": user_id, "order_number": order.order_number,
            "event_type": "order_review_completed", "item_count": len(frozen_items),
            "occurred_at": now, "actor_id": actor_id,
        })
        return {
            "ok": True, "order_number": order.order_number, "stage": "reviewed",
            "reviewed_item_count": len(frozen_items), "salla_status_sync": "sent",
            "salla_status_sync_error": None,
        }

    return router
