"""Explicit one-order source refresh; never approve, receive or complete work."""
from fastapi import HTTPException
from mobile_app_permissions import mobile_app_access_for_user
from order_engine.salla_refresh import refresh_order_from_salla


async def refresh_mobile_order(db, user, number, *, page):
    number = str(number or "").strip()
    if not number or len(number) > 64:
        raise HTTPException(400, detail={"code": "order_number_required"})
    owner = str(user.get("_mobile_owner_id") or user.get("id") or "")
    actor = str(user.get("_mobile_actor_id") or user.get("id") or "")

    async def authorize(scoped):
        # Reload the actual employee, never reuse a merchant-shaped page grant.
        live = await scoped.users.find_one({"id": actor})
        if not live or live.get("disabled") or live.get("is_active") is False or live.get("deleted_at"):
            raise HTTPException(403, detail={"code": "mobile_actor_unavailable"})
        if live.get("role") == "owner" or live.get("is_owner") is True:
            if str(live.get("id")) != owner:
                raise HTTPException(403, detail={"code": "employee_store_changed"})
            return
        from mobile_app_request_context import _linked_owner_id
        access = await mobile_app_access_for_user(scoped, live)
        if not access.get("enabled") or page not in (access.get("permissions") or []):
            raise HTTPException(403, detail={"code": "mobile_app_page_permission_required"})
        if await _linked_owner_id(scoped, live) != owner:
            raise HTTPException(403, detail={"code": "employee_store_changed"})

    await authorize(db)
    result = await refresh_order_from_salla(
        db, owner, number, force=True, allow_auto_fulfillment=False,
        allow_delivery_observation=False, allow_billing_eligibility=False,
        before_persist=authorize,
    )
    await authorize(db)
    if not (result.get("ok") and result.get("found") and result.get("updated") and result.get("order_number") == number):
        raise HTTPException(409 if result.get("ok") or result.get("needs_reauth") else 502,
            detail={"code": result.get("code") or result.get("reason") or "order_refresh_unconfirmed",
                    "message": "تعذّر تأكيد تحديث الطلب من سلة؛ أعد المحاولة.", "order_number": number})
    return result
