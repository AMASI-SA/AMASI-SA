"""Merchant-wide native assembly presentation setting; no fulfillment writes."""
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from mobile_app_permissions import mobile_app_access_for_user
from mobile_app_request_context import _linked_owner_id


class AssemblySettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    show_salla_order_link: StrictBool


async def _scope(db, user):
    from auth import account_is_disabled
    actor_id = str(user.get("_mobile_actor_id") or user.get("id") or "")
    actor = await db.users.find_one({"id": actor_id}, {"_id": 0})
    if not actor or account_is_disabled(actor):
        raise HTTPException(403, detail={"code": "assembly_app_access_required"})
    access = await mobile_app_access_for_user(db, actor)
    if not access.get("enabled") or "app.page.assembly_shipping" not in access.get("permissions", []):
        raise HTTPException(403, detail={"code": "assembly_app_access_required"})
    owner = str(actor.get("id")) if actor.get("role") == "owner" or actor.get("is_owner") is True else await _linked_owner_id(db, actor)
    if not owner or (user.get("_mobile_owner_id") and user["_mobile_owner_id"] != owner):
        raise HTTPException(403, detail={"code": "assembly_owner_scope_changed"})
    return owner, access.get("manager") is True


async def _enabled(db, owner):
    row = await db.settings.find_one({"user_id": owner}, {"assembly_shipping.show_salla_order_link": 1}) or {}
    settings = row.get("assembly_shipping")
    return isinstance(settings, dict) and settings.get("show_salla_order_link") is True


def trusted_admin_url(raw):
    """Use the provider's saved admin URL, never a customer URL or reference."""
    urls = raw.get("urls") if isinstance(raw.get("urls"), dict) else {}
    value = urls.get("admin")
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        identifier = str(raw.get("id") or "")
        if (parsed.scheme != "https" or parsed.netloc != "s.salla.sa"
                or parsed.query or parsed.fragment or not re.fullmatch(r"[0-9]+", identifier)
                or parsed.path.rstrip("/") != f"/orders/{identifier}"):
            return None
    except ValueError:
        return None
    return value


def make_assembly_mobile_settings_router(db, current_user, *, router=None):
    router = router if router is not None else APIRouter()

    @router.get("/assembly/settings")
    async def read_settings(user=Depends(current_user)):
        owner, manager = await _scope(db, user)
        return {"show_salla_order_link": await _enabled(db, owner), "can_manage": manager}

    @router.patch("/assembly/settings")
    async def change_settings(payload: AssemblySettingsPatch, user=Depends(current_user)):
        owner, manager = await _scope(db, user)
        if not manager:
            raise HTTPException(403, detail={"code": "mobile_app_manager_required"})
        await db.settings.update_one({"user_id": owner}, {"$set": {
            "assembly_shipping.show_salla_order_link": payload.show_salla_order_link}}, upsert=True)
        return {"show_salla_order_link": payload.show_salla_order_link, "can_manage": True}

    @router.get("/assembly/orders/{order_number}/salla-link")
    async def read_link(order_number: str, user=Depends(current_user)):
        owner, _ = await _scope(db, user)
        if not await _enabled(db, owner):
            return {"show_salla_order_link": False, "salla_admin_url": None}
        source = await db.unified_orders.find_one({"user_id": owner, "order_number": order_number},
            {"raw_by_source.salla_direct.id": 1, "raw_by_source.salla_direct.reference_id": 1,
             "raw_by_source.salla_direct.urls.admin": 1}) or {}
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        url = trusted_admin_url(raw) if str(raw.get("reference_id") or "") == order_number else None
        return {"show_salla_order_link": True, "salla_admin_url": url}

    return router
