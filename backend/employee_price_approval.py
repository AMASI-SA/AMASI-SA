"""Employee price proposals never change catalog costs before owner approval.

Supplier invoices keep their recorded prices and payable. Only the catalog
target is changed by a separate, original dashboard-owner decision.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from pymongo.errors import DuplicateKeyError

from component_edit_policy import component_cost_metadata
from mobile_app_permissions import MOBILE_APP_CLIENT
from product_cost_revision import bump_product_cost_revision

REQUESTS = "mezan_employee_price_requests_v1"
PRODUCTS = "mezan_products_v2"
COST_PROFILES = "mezan_product_cost_profiles_v2"
RESOURCES = "mezan_cost_resources_v2"
AUDIT = "mezan_cost_change_log_v2"
BINDINGS = "mezan_product_option_cost_bindings_v2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _money(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        halalas = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if halalas > 9_007_199_254_740_991:
            raise ValueError
        return halalas
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise HTTPException(422, detail={"code": "invalid_cost"}) from None


def requires_mobile_price_approval(user: dict) -> bool:
    return user.get("_session_client") == MOBILE_APP_CLIENT or bool(user.get("_mobile_actor_id"))


def _owner(user: dict) -> str:
    identity = _text(user.get("id"))
    if (not identity or requires_mobile_price_approval(user)
            or not (user.get("role") == "owner" or user.get("is_owner") is True)
            or _text(user.get("created_by")) not in {"", identity}):
        raise HTTPException(403, detail={"code": "price_change_owner_required", "message": "اعتماد السعر متاح لمالك المتجر من لوحة التحكم فقط."})
    return identity


def public_request(row: dict) -> dict:
    return {key: value for key, value in row.items()
            if key not in {"_id", "user_id", "catalog_guard", "target_amount", "target_present", "target_metadata"}}


async def atomic_price_change(db: Any, work: Callable) -> Any:
    client = getattr(db, "client", None)
    if client is None or not hasattr(client, "start_session"):
        raise HTTPException(503, detail={"code": "price_change_transaction_required"})
    async with await client.start_session() as session:
        return await session.with_transaction(work)


async def _target(db: Any, owner: str, change: dict, mongo_session: Any = None) -> dict:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    if change.get("change_type") == "product_price":
        product_id = _text(change.get("product_id"))
        product = await db[PRODUCTS].find_one({"user_id": owner,
            "$or": [{field: product_id} for field in ("id", "mezan_product_id", "salla_product_id")]}, {"_id": 0}, **kwargs)
        if not product:
            raise HTTPException(409, detail={"code": "price_change_product_missing"})
        salla_id = _text(product.get("salla_product_id") or product.get("mezan_product_id") or product.get("id"))
        if _text(change.get("option_id")):
            selector = {"user_id": owner, "salla_product_id": salla_id,
                "option_id": _text(change.get("option_id")), "value_id": _text(change.get("value_id"))}
            binding = await db[BINDINGS].find_one(selector, {"_id": 0}, **kwargs) or {}
            return {"collection": BINDINGS, "selector": selector, "field": "direct_amount",
                "guard": {"present": "direct_amount" in binding, "amount": binding.get("direct_amount"),
                    "mode": binding.get("mode"), "resource_id": binding.get("resource_id"), "quantity": binding.get("quantity")},
                "effective_amount": binding.get("direct_amount"), "product_id": _text(product.get("mezan_product_id") or product.get("id")),
                "product_name": _text(product.get("name")), "salla_product_id": salla_id,
                "option_id": selector["option_id"], "value_id": selector["value_id"]}
        profile = await db[COST_PROFILES].find_one({"user_id": owner, "salla_product_id": salla_id}, {"_id": 0}, **kwargs) or {}
        variant = _text(change.get("variant_id"))
        if variant and ("." in variant or variant.startswith("$")):
            raise HTTPException(422, detail={"code": "price_change_variant_invalid"})
        values = dict(profile.get("variant_costs") or {}) if variant else profile
        key = variant or "base_cost"
        guard = {"present": key in values, "amount": values.get(key)}
        # An absent variant uses the base cost. A later base edit invalidates
        # that proposal, while an unrelated variant edit does not.
        if variant and values.get(key) is None:
            guard["fallback_base_cost"] = profile.get("base_cost")
        return {"collection": COST_PROFILES, "selector": {"user_id": owner, "salla_product_id": salla_id},
            "field": f"variant_costs.{variant}" if variant else "base_cost", "guard": guard,
            "effective_amount": values.get(key) if values.get(key) is not None else profile.get("base_cost"),
            "product_id": _text(product.get("mezan_product_id") or product.get("id")),
            "product_name": _text(product.get("name")), "salla_product_id": salla_id, "variant_id": variant or None}
    if change.get("change_type") != "service_price":
        raise HTTPException(422, detail={"code": "price_change_target_invalid"})
    service_id = _text(change.get("service_id"))
    resource = await db[RESOURCES].find_one({"user_id": owner, "id": service_id, "kind": "service", "track_inventory": {"$ne": True}}, {"_id": 0}, **kwargs)
    if not resource or resource.get("status") == "inactive":
        raise HTTPException(409, detail={"code": "price_change_service_missing"})
    return {"collection": RESOURCES, "selector": {"user_id": owner, "id": service_id}, "field": "unit_cost",
        "guard": {"present": "unit_cost" in resource, "amount": resource.get("unit_cost")},
        "effective_amount": resource.get("unit_cost"), "service_id": service_id,
        "service_name": _text(resource.get("name"))}


async def _propose(db: Any, *, owner: str, actor: dict, source: str, changes: list[dict],
                   invoice_id: str | None = None, at: datetime | None = None, mongo_session: Any = None,
                   client_request_id: str | None = None) -> list[dict]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    at = at or _now()
    actor_id = _text(actor.get("_mobile_actor_id") or actor.get("id"))
    actor_name = _text(actor.get("_mobile_actor_name") or actor.get("name") or actor.get("_mobile_actor_email") or actor.get("email"))
    if client_request_id is not None and (not isinstance(client_request_id, str) or not 8 <= len(client_request_id) <= 160):
        raise HTTPException(422, detail={"code": "price_change_request_key_invalid"})
    requests = []
    for change in changes:
        target = await _target(db, owner, change, mongo_session)
        amount = change.get("target_amount")
        after = _money(amount)
        # The approved catalog value is exactly the amount shown in halalas.
        amount = after / 100 if after is not None else None
        present = change.get("target_present", True)
        identity = {"owner": owner, "source": source, "invoice": invoice_id, "actor": actor_id,
            "target": target["selector"], "field": target["field"], "before": target["guard"],
            "after": amount, "present": present, "metadata": change.get("target_metadata")}
        if invoice_id or client_request_id:
            identity.pop("before")
            if client_request_id:
                identity["client_request_id"] = client_request_id
                for key in ("after", "present", "metadata"):
                    identity.pop(key)
        request_id = "pcr_" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:40]
        before = _money(target.get("effective_amount"))
        row = {"_id": request_id, "id": request_id, "user_id": owner, "status": "pending", "source": source,
            "change_type": change["change_type"], "product_id": target.get("product_id") or change.get("product_id"),
            "product_name": target.get("product_name") or change.get("product_name"),
            "salla_product_id": target.get("salla_product_id"), "variant_id": target.get("variant_id"),
            "service_id": target.get("service_id"), "service_name": target.get("service_name"),
            "before_halalas": before, "after_halalas": after, "before_amount": target.get("effective_amount"),
            "after_amount": amount, "removes_override": not present or amount is None,
            "removes_binding": change.get("removes_binding") is True,
            "catalog_guard": target["guard"], "target_amount": amount, "target_present": present,
            "target_metadata": change.get("target_metadata"),
            "requested_by": actor_id, "requested_by_name": actor_name, "invoice_id": invoice_id,
            "client_request_id": client_request_id,
            "created_at": at, "decided_at": None,
            **{key: change[key] for key in ("invoice_before_halalas", "invoice_after_halalas", "reference_option_cost_halalas", "option_id", "option_name", "value_id", "value_name") if key in change}}
        if target["collection"] == BINDINGS:
            row["before_quantity"] = target["guard"].get("quantity") or 1.0
            row["after_quantity"] = None if change.get("removes_binding") else (change.get("target_metadata") or {}).get("quantity", 1.0)
        await db[REQUESTS].update_one({"_id": request_id}, {"$setOnInsert": row}, upsert=True, **kwargs)
        saved = await db[REQUESTS].find_one({"_id": request_id, "user_id": owner}, {"_id": 0}, **kwargs)
        if (saved.get("target_amount") != amount or saved.get("target_present", True) != present
                or saved.get("target_metadata") != change.get("target_metadata")
                or bool(saved.get("removes_binding")) != bool(change.get("removes_binding"))):
            raise HTTPException(409, detail={"code": "price_change_request_conflict", "message": "استخدم محاولة حفظ جديدة عند تغيير السعر المقترح."})
        name = row.get("service_name") or row.get("product_name") or "منتج"
        after_label = "إزالة السعر المخصص" if after is None else f"{after / 100:.2f} ريال"
        await db.settlement_alerts.update_one({"_id": request_id}, {"$setOnInsert": {
            "id": request_id, "user_id": owner, "type": "employee_price_change", "alert_type": "employee_price_change", "fingerprint": f"employee-price:{request_id}",
            "status": "new", "severity": "info", "title": "طلب اعتماد تغيير سعر",
            "message": f"{actor_name or 'الموظف'} يطلب تغيير سعر {name} إلى {after_label}",
            "related_entity_url": f"/price-change-requests/{request_id}",
            "metadata": {"price_change_request_id": request_id}, "created_at": at.isoformat(), "updated_at": at.isoformat(),
            "snoozed_until": None}}, upsert=True, **kwargs)
        requests.append(public_request(saved))
    return requests


async def propose_invoice_price_changes(db: Any, *, user_id: str, actor: dict, invoice_id: str,
                                        changes: list[dict], changed_at: datetime, mongo_session: Any) -> list[dict]:
    enriched = []
    for change in changes:
        base = change.get("after_base_halalas", change.get("after_halalas", 0))
        proposed = {**change, "target_amount": int(base or 0) / 100,
            "invoice_before_halalas": change.get("before_halalas"), "invoice_after_halalas": change.get("after_halalas")}
        request = (await _propose(db, owner=user_id, actor=actor, source="supplier_invoice", changes=[proposed],
            invoice_id=invoice_id, at=changed_at, mongo_session=mongo_session))[0]
        enriched.append({**change, "applied": False, "approval_status": request["status"], "approval_request_id": request["id"]})
    return enriched


def proposal_response(requests: list[dict]) -> dict:
    return {"ok": True, "approval_required": bool(requests), "catalog_updated": False,
        "status": "pending" if any(row["status"] == "pending" for row in requests) else requests[0]["status"] if requests else "unchanged",
        "requests": requests, "pending_request_id": requests[0]["id"] if requests else None}


async def invoice_approval_view(db: Any, *, owner: str, invoice: dict) -> dict:
    """Refresh proposal status for display without rewriting the invoice."""
    request_ids = set(invoice.get("price_approval_request_ids") or [])
    request_ids.update(change["approval_request_id"] for change in invoice.get("price_changes") or []
                       if change.get("approval_request_id"))
    if not request_ids:
        return invoice
    rows = await db[REQUESTS].find({"user_id": owner, "_id": {"$in": sorted(request_ids)}},
        {"_id": 0, "id": 1, "status": 1}).to_list(len(request_ids))
    statuses = {row["id"]: row["status"] for row in rows}
    public = {**invoice, "price_approval_pending": any(statuses.get(key, "pending") == "pending" for key in request_ids),
              "price_approval_request_count": len(request_ids)}
    if "price_changes" in invoice:
        public["price_changes"] = [{**change, "approval_status": statuses.get(change.get("approval_request_id"), change.get("approval_status"))}
                                   for change in invoice["price_changes"]]
    return public


async def propose_mobile_product_costs(db: Any, *, user: dict, product: dict, base_cost: Any,
                                       variant_costs: dict, client_request_id: str | None = None) -> dict:
    owner = _text(user.get("_mobile_owner_id") or user.get("id"))
    salla_id = _text(product.get("salla_product_id") or product.get("mezan_product_id") or product.get("id"))
    async def submit(session):
        kwargs = {"session": session} if session is not None else {}
        profile = await db[COST_PROFILES].find_one({"user_id": owner, "salla_product_id": salla_id}, {"_id": 0}, **kwargs) or {}
        changes = []
        common = {"change_type": "product_price", "product_id": _text(product.get("id") or salla_id)}
        if profile.get("base_cost") != base_cost:
            changes.append({**common, "target_amount": base_cost})
        previous = dict(profile.get("variant_costs") or {})
        for variant in sorted(set(previous) | set(variant_costs)):
            if previous.get(variant) != variant_costs.get(variant):
                changes.append({**common, "variant_id": variant, "target_amount": variant_costs.get(variant), "target_present": variant in variant_costs})
        requests = await _propose(db, owner=owner, actor=user, source="mobile_product", changes=changes, mongo_session=session, client_request_id=client_request_id)
        return {**proposal_response(requests), "salla_product_id": salla_id, "base_cost": profile.get("base_cost"),
            "variant_costs": previous, "updated_at": profile.get("updated_at")}
    return await atomic_price_change(db, submit)


async def propose_mobile_service_cost(db: Any, *, user: dict, resource: dict, amount: Any, client_request_id: str | None = None) -> dict:
    if resource.get("unit_cost") == amount:
        return proposal_response([])
    owner = _text(user.get("_mobile_owner_id") or user.get("id"))
    async def submit(session):
        requests = await _propose(db, owner=owner, actor=user, source="mobile_service",
            changes=[{"change_type": "service_price", "service_id": resource["id"], "target_amount": amount}], mongo_session=session, client_request_id=client_request_id)
        return proposal_response(requests)
    return await atomic_price_change(db, submit)


async def propose_mobile_option_cost(db: Any, *, user: dict, product: dict, patch: dict, client_request_id: str | None = None) -> dict:
    owner = _text(user.get("_mobile_owner_id") or user.get("id"))
    async def submit(session):
        kwargs = {"session": session} if session is not None else {}
        selector = {key: patch[key] for key in ("user_id", "salla_product_id", "option_id", "value_id")}
        old = await db[BINDINGS].find_one(selector, {"_id": 0}, **kwargs)
        metadata = {key: patch[key] for key in ("product_name", "option_name", "value_name", "mode", "resource_id", "quantity")}
        changed = not old or old.get("direct_amount") != patch["direct_amount"] or any(old.get(key) != metadata[key] for key in ("mode", "quantity"))
        requests = await _propose(db, owner=owner, actor=user, source="mobile_product_option", changes=[{
            "change_type": "product_price", "product_id": _text(product.get("id") or product.get("salla_product_id")),
            "option_id": patch["option_id"], "value_id": patch["value_id"], "option_name": patch["option_name"], "value_name": patch["value_name"],
            "target_amount": patch["direct_amount"], "target_metadata": metadata,
        }] if changed else [], mongo_session=session, client_request_id=client_request_id)
        return {**proposal_response(requests), "binding": old}
    return await atomic_price_change(db, submit)


async def propose_mobile_option_removal(db: Any, *, user: dict, product: dict, binding: dict,
                                        client_request_id: str | None = None) -> dict:
    owner = _text(user.get("_mobile_owner_id") or user.get("id"))
    async def submit(session):
        requests = await _propose(db, owner=owner, actor=user, source="mobile_product_option_removal", changes=[{
            "change_type": "product_price", "product_id": _text(product.get("id") or product.get("salla_product_id")),
            "option_id": binding["option_id"], "value_id": binding["value_id"],
            "option_name": binding.get("option_name"), "value_name": binding.get("value_name"),
            "target_amount": None, "target_present": False, "removes_binding": True,
        }], mongo_session=session, client_request_id=client_request_id)
        return {**proposal_response(requests), "binding": binding, "deleted": 0}
    return await atomic_price_change(db, submit)


async def decide_price_change(db: Any, *, user: dict, request_id: str, decision: str, mongo_session: Any) -> dict:
    owner = _owner(user)
    if decision not in {"approve", "reject"}:
        raise HTTPException(422, detail={"code": "price_change_decision_invalid"})
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    request = await db[REQUESTS].find_one({"id": request_id, "user_id": owner}, {"_id": 0}, **kwargs)
    if not request:
        raise HTTPException(404, detail={"code": "price_change_request_not_found"})
    status = "approved" if decision == "approve" else "rejected"
    if request.get("status") != "pending":
        if request.get("status") == status:
            return {"ok": True, "request": public_request(request), "idempotent": True}
        raise HTTPException(409, detail={"code": "price_change_already_decided", "message": "تم اتخاذ قرار على هذا الطلب سابقًا."})
    at = _now()
    if decision == "approve":
        target = await _target(db, owner, request, mongo_session)
        if target["guard"] != request["catalog_guard"]:
            raise HTTPException(409, detail={"code": "price_change_stale", "message": "تغيّر السعر المعتمد بعد تقديم الطلب. راجع السعر الحالي قبل طلب تعديل جديد."})
        amount = request.get("target_amount")
        _money(amount)
        selector = {**target["selector"], target["field"]: request["catalog_guard"]["amount"] if request["catalog_guard"]["present"] else {"$exists": False}}
        patch = {"$set": {"updated_at": at, "updated_by": owner, "last_price_approval_id": request_id}}
        if target["collection"] == RESOURCES:
            patch["$set"].update(component_cost_metadata(track_inventory=False, amount=amount))
        elif request.get("target_present", True):
            patch["$set"][target["field"]] = amount
        else:
            patch["$unset"] = {target["field"]: ""}
        if target["collection"] == BINDINGS:
            patch["$set"].update(request.get("target_metadata") or {})
        if target["collection"] in {COST_PROFILES, BINDINGS}:
            patch["$setOnInsert"] = {**target["selector"], "id": f"cost-approval:{request_id}", "created_at": at}
        try:
            if request.get("removes_binding"):
                if target["collection"] != BINDINGS or target["guard"].get("mode") != "direct":
                    raise HTTPException(409, detail={"code": "price_change_stale", "message": "تغيّر ربط السعر المباشر قبل الاعتماد."})
                result = await db[BINDINGS].delete_one(selector, **kwargs)
                changed_catalog = result.deleted_count == 1
            else:
                result = await db[target["collection"]].update_one(selector, patch, upsert=target["collection"] in {COST_PROFILES, BINDINGS}, **kwargs)
                changed_catalog = bool(result.matched_count or result.upserted_id)
        except DuplicateKeyError:
            raise HTTPException(409, detail={"code": "price_change_stale", "message": "تغيّر السعر المعتمد أثناء المراجعة. حدّث الطلب."}) from None
        if not changed_catalog:
            raise HTTPException(409, detail={"code": "price_change_stale", "message": "تغيّر السعر المعتمد أثناء المراجعة. حدّث الطلب."})
        await bump_product_cost_revision(db, owner, session=mongo_session)
    update = {"status": status, "decided_at": at, "decided_by": owner, "decided_by_name": _text(user.get("name"))}
    changed = await db[REQUESTS].update_one({"id": request_id, "user_id": owner, "status": "pending"}, {"$set": update}, **kwargs)
    if changed.matched_count != 1:
        raise HTTPException(409, detail={"code": "price_change_already_decided", "message": "تم اتخاذ قرار على هذا الطلب سابقًا."})
    await db.settlement_alerts.update_one({"id": request_id, "user_id": owner}, {"$set": {
        "status": "resolved", "updated_at": at.isoformat(), "metadata.decision": status}}, **kwargs)
    await db[AUDIT].update_one({"_id": f"price-decision:{request_id}"}, {"$setOnInsert": {
        "id": f"price-decision:{request_id}", "user_id": owner, "event_type": f"employee_price_change_{status}",
        "price_change_request_id": request_id, "actor_id": owner, "before_halalas": request.get("before_halalas"),
        "after_halalas": request.get("after_halalas"), "created_at": at}}, upsert=True, **kwargs)
    return {"ok": True, "request": public_request({**request, **update}), "idempotent": False}


class PriceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]


def make_price_change_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/price-change-requests-v1", tags=["Employee Price Approval"])
    @router.get("")
    async def list_requests(status: Literal["pending", "approved", "rejected", "all"] = "pending",
                            limit: int = Query(100, ge=1, le=100), user: dict = Depends(current_user)):
        owner = _owner(user)
        await db[REQUESTS].create_index([("user_id", 1), ("status", 1), ("created_at", -1)])
        query = {"user_id": owner, **({"status": status} if status != "all" else {})}
        rows = await db[REQUESTS].find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
        return {"ok": True, "items": [public_request(row) for row in rows],
            "pending_count": await db[REQUESTS].count_documents({"user_id": owner, "status": "pending"})}

    @router.get("/{request_id}")
    async def get_request(request_id: str, user: dict = Depends(current_user)):
        owner = _owner(user)
        row = await db[REQUESTS].find_one({"id": request_id, "user_id": owner}, {"_id": 0})
        if not row:
            raise HTTPException(404, detail={"code": "price_change_request_not_found"})
        return {"ok": True, "request": public_request(row)}

    @router.post("/{request_id}/decision")
    async def decision(request_id: str, payload: PriceDecision, user: dict = Depends(current_user)):
        _owner(user)
        async def apply(session):
            return await decide_price_change(db, user=user, request_id=request_id, decision=payload.decision, mongo_session=session)
        return await atomic_price_change(db, apply)
    return router
