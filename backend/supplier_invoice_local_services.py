"""Draft-only service selections, scoped to scanned pieces and customer choices."""
from __future__ import annotations

import hashlib
import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

LOCAL_SERVICES = "mezan_supplier_invoice_local_services_v1"
LOCAL_SOURCE = "supplier_invoice_local"


def _text(value: Any) -> str:
    return str(value or "").strip()


class OptionCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str | None = Field(default=None, max_length=200)
    value_id: str | None = Field(default=None, max_length=200)
    option_name: str | None = Field(default=None, max_length=500)
    value_name: str | None = Field(default=None, max_length=1000)


class LocalServiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    piece_ids: list[str] = Field(min_length=1, max_length=5000)
    option_condition: OptionCondition | None = None


def _options(piece: dict) -> list[dict]:
    result = {}
    for field in ("service_specifications_snapshot", "specifications_snapshot"):
        for row in piece.get(field) or []:
            name = _text(row.get("name") or row.get("label") or row.get("title"))
            value = _text(row.get("value") or row.get("answer") or row.get("text"))
            if name and value:
                result[(name.casefold(), value.casefold())] = {"option_name": name, "value_name": value}
    for field in ("product_options_snapshot", "options_normalized", "selected_options"):
        values = piece.get(field)
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            name, value = _text(name), _text(value)
            if name and value:
                result[(name.casefold(), value.casefold())] = {"option_name": name, "value_name": value}
    return list(result.values())


async def _pieces(db, *, owner, session, product, product_id, piece_ids, mongo_session=None):
    import supplier_receiving_routes as base
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    ids = list(dict.fromkeys(_text(value) for value in piece_ids or [] if _text(value)))
    if piece_ids and len(ids) != len(piece_ids):
        raise HTTPException(422, detail={"code": "supplier_invoice_local_piece_ids_invalid"})
    selector = {"user_id": owner, "supplier_receiving_session_id": _text(session.get("id")),
        "product_id": {"$in": base._supplier_receiving_product_identifiers(product, product_id)}}
    if ids:
        selector["piece_id"] = {"$in": ids}
    rows = await db[base.PIECES].find(selector, {"_id": 0}, **kwargs).to_list(5000)
    if ids and {row.get("piece_id") for row in rows} != set(ids):
        raise HTTPException(409, detail={"code": "supplier_invoice_local_piece_scope", "message": "اختر القطع الموجودة في مسودة الفاتورة الحالية."})
    scanned = await db[base.RECEIVING_EVENTS].find({"user_id": owner, "session_id": session["id"],
        "event_type": "supplier_piece_scanned", "piece_id": {"$in": [row["piece_id"] for row in rows]}},
        {"_id": 0, "piece_id": 1}, **kwargs).to_list(5000)
    scanned_ids = {row["piece_id"] for row in scanned}
    if ids and set(ids) - scanned_ids:
        raise HTTPException(409, detail={"code": "supplier_invoice_local_piece_scope"})
    return [row for row in rows if row["piece_id"] in scanned_ids]


def invoice_service(resource: dict, condition: dict | None = None) -> dict:
    import supplier_receiving_routes as base
    amount = base._halalas(resource.get("unit_cost"))
    return {"service_id": _text(resource.get("id")), "service_name": _text(resource.get("name")),
        "service_code": _text(resource.get("code")) or None, "unit": _text(resource.get("unit")) or "job",
        "required_quantity": 1.0, "reference_unit_price_halalas": amount,
        "reference_price_complete": amount is not None, "unit_price_halalas": int(amount or 0),
        "linked_to_product": False, "add_to_product": False, "supplier_invoice_required": True,
        "invoice_scope": True, "scope": "invoice", "eligibility_source": LOCAL_SOURCE,
        "eligibility_condition": dict(condition) if condition else None, "customer_selected": bool(condition)}


async def service_candidates(db, *, context, session, product_id, piece_ids=None, mongo_session=None):
    import supplier_receiving_routes as base
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    product = await base._supplier_receiving_product(db, user_id=context["merchant_id"], product_id=product_id, mongo_session=mongo_session)
    pieces = await _pieces(db, owner=context["merchant_id"], session=session, product=product,
        product_id=product_id, piece_ids=piece_ids, mongo_session=mongo_session)
    choices = {}
    for piece in pieces:
        for choice in _options(piece):
            key = (choice["option_name"].casefold(), choice["value_name"].casefold())
            choices.setdefault(key, {**choice, "piece_ids": []})["piece_ids"].append(piece["piece_id"])
    options = list(choices.values())
    category = _text(product.get("cost_category_id"))
    resources = await db[base.RESOURCES].find({"user_id": context["merchant_id"], "kind": "service",
        "track_inventory": {"$ne": True}, "status": {"$ne": "inactive"}, "category_ids": category},
        {"_id": 0, "id": 1, "name": 1, "code": 1, "unit": 1, "unit_cost": 1, "category_ids": 1}, **kwargs).sort("name", 1).to_list(5000) if category else []
    return {"ok": True, "scope": "invoice", "invoice_scope": True, "permanent_for_future_orders": False,
        "category_required": not bool(category), "message": "لا يوجد تصنيف لإضافة خدمة له" if not category else None,
        "product": {"id": _text(product.get("mezan_product_id") or product.get("id")), "salla_product_id": _text(product.get("salla_product_id")),
            "name": _text(product.get("name")), "cost_category_id": category or None, "cost_category_name": product.get("cost_category_name")},
        "selected_options": options,
        "services": [{**row, "unit_price_halalas": int(base._halalas(row.get("unit_cost")) or 0)} for row in resources]}


async def add_local_service(db, *, context, actor, session, product_id, service_id, piece_ids,
                            option_condition=None, mongo_session=None):
    import supplier_receiving_routes as base
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    if not piece_ids:
        raise HTTPException(422, detail={"code": "supplier_invoice_local_piece_ids_required"})
    fresh = await db[base.SESSIONS].find_one({"user_id": context["merchant_id"], "id": session["id"],
        "opened_by": context["actor_id"], "status": "open"}, {"_id": 0}, **kwargs)
    if not fresh:
        raise HTTPException(409, detail={"code": "supplier_receiving_session_closed"})
    candidates = await service_candidates(db, context=context, session=fresh, product_id=product_id, piece_ids=piece_ids, mongo_session=mongo_session)
    if candidates["category_required"]:
        raise HTTPException(422, detail={"code": "supplier_invoice_product_category_required", "message": candidates["message"]})
    resource = next((row for row in candidates["services"] if row["id"] == service_id), None)
    if resource is None:
        raise HTTPException(422, detail={"code": "supplier_invoice_service_category_mismatch", "message": "اختر خدمة من تصنيف المنتج الحالي."})
    product = await base._supplier_receiving_product(db, user_id=context["merchant_id"], product_id=product_id, mongo_session=mongo_session)
    pieces = await _pieces(db, owner=context["merchant_id"], session=fresh, product=product, product_id=product_id, piece_ids=piece_ids, mongo_session=mongo_session)
    condition = {key: _text(value) for key, value in (option_condition or {}).items() if _text(value)} or None
    if condition:
        if not ((condition.get("option_name") and condition.get("value_name")) or (condition.get("option_id") and condition.get("value_id"))):
            raise HTTPException(422, detail={"code": "supplier_invoice_option_condition_invalid"})
        for piece in pieces:
            tokens = base._supplier_piece_option_tokens(piece) | base.selected_option_tokens(piece)
            if not base._supplier_option_binding_matches(condition, tokens):
                raise HTTPException(422, detail={"code": "supplier_invoice_option_not_selected", "message": "الخيار المحدد ليس من اختيارات العميل لهذه القطع."})
    at = datetime.now(timezone.utc)
    service = invoice_service(resource, condition)
    # A session write fences concurrent close/cancel and this addition in the
    # existing Mongo transaction. No product or physical-piece plan is edited.
    changed = await db[base.SESSIONS].update_one({"user_id": context["merchant_id"], "id": fresh["id"], "status": "open", "opened_by": context["actor_id"]},
        {"$inc": {"local_service_revision": 1}}, **kwargs)
    if changed.matched_count != 1:
        raise HTTPException(409, detail={"code": "supplier_receiving_session_closed"})
    for piece in pieces:
        key = hashlib.sha256(f"{context['merchant_id']}:{fresh['id']}:{piece['piece_id']}:{service_id}".encode()).hexdigest()
        existing = await db[LOCAL_SERVICES].find_one({"_id": key}, {"_id": 0}, **kwargs)
        if existing and existing.get("option_condition") != condition:
            raise HTTPException(409, detail={"code": "supplier_invoice_local_service_conflict", "message": "الخدمة مضافة لهذه القطعة بخيار مختلف."})
        await db[LOCAL_SERVICES].update_one({"_id": key}, {"$setOnInsert": {
            "id": key, "user_id": context["merchant_id"], "session_id": fresh["id"], "piece_id": piece["piece_id"],
            "product_id": piece.get("product_id"), "service_id": service_id, "category_id": product.get("cost_category_id"),
            "option_condition": condition, "service": service, "created_at": at, "created_by": context["actor_id"]}}, upsert=True, **kwargs)
    return {"ok": True, "scope": "invoice", "invoice_scope": True, "service": service,
        "piece_ids": list(piece_ids), "affected_piece_ids": list(piece_ids), "permanent_for_future_orders": False, "historical_invoices_changed": False}


async def merge_local_invoice_services(db, *, user_id, session_id, rows, service_catalog=None, mongo_session=None):
    if not rows:
        return rows
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    additions = await db[LOCAL_SERVICES].find({"user_id": user_id, "session_id": session_id,
        "piece_id": {"$in": [row.get("piece_id") for row in rows]}}, {"_id": 0}, **kwargs).to_list(100000)
    by_piece = {}
    for addition in additions:
        by_piece.setdefault(addition["piece_id"], []).append(addition)
    for row in rows:
        services = list(row.get("invoice_services") or [])
        present = {service.get("service_id") for service in services}
        for addition in by_piece.get(row.get("piece_id"), []):
            if addition["service_id"] in present:
                continue
            resource = (service_catalog or {}).get(addition["service_id"])
            service = invoice_service(resource, addition.get("option_condition")) if resource else dict(addition["service"])
            services.append(service)
            present.add(addition["service_id"])
        row["invoice_services"] = services
    return rows


def register_local_service_routes(router, db: Any, current_user: Callable):
    import supplier_receiving_routes as base
    indexes_ready = False
    index_lock = asyncio.Lock()

    async def ensure_indexes_once():
        nonlocal indexes_ready
        if indexes_ready:
            return
        async with index_lock:
            if not indexes_ready:
                await db[LOCAL_SERVICES].create_index([("user_id", 1), ("session_id", 1), ("piece_id", 1)])
                indexes_ready = True

    async def context_session(session_id, user):
        context = await base._actor_context(db, user)
        base._require_permission(context, base.RECEIVE_PERMISSION)
        base._require_permission(context, base.ADD_PRODUCT_SERVICE_PERMISSION)
        session = await base._session_for_actor(db, context=context, session_id=session_id)
        if session.get("status") != "open":
            raise HTTPException(409, detail={"code": "supplier_receiving_session_closed"})
        await ensure_indexes_once()
        return context, session

    @router.get("/sessions/{session_id}/products/{product_id}/invoice-service-candidates")
    async def candidates(session_id: str, product_id: str, piece_ids: str = Query("", max_length=100000), user: dict = Depends(current_user)):
        context, session = await context_session(session_id, user)
        return await service_candidates(db, context=context, session=session, product_id=product_id,
            piece_ids=[part for part in piece_ids.split(",") if part])

    @router.post("/sessions/{session_id}/products/{product_id}/invoice-services/{service_id}", status_code=201)
    async def add(session_id: str, product_id: str, service_id: str, payload: LocalServiceRequest, user: dict = Depends(current_user)):
        context, session = await context_session(session_id, user)
        condition = payload.option_condition.model_dump(exclude_none=True) if payload.option_condition else None
        async def work(mongo_session):
            return await add_local_service(db, context=context, actor=user, session=session, product_id=product_id,
                service_id=service_id, piece_ids=payload.piece_ids, option_condition=condition, mongo_session=mongo_session)
        client = getattr(db, "client", None)
        if client is None or not hasattr(client, "start_session"):
            raise HTTPException(503, detail={"code": "supplier_receiving_atomic_transaction_required"})
        async with await client.start_session() as mongo_session:
            return await mongo_session.with_transaction(work)
