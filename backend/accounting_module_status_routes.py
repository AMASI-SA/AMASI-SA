"""Read-only access and readiness endpoints for the accounting module."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, Query

from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    accounting_permissions_for_user,
    is_owner,
    require_accounting_permission,
)
from accounting_module_ledger import (
    ledger_only_home_balances,
    opening_posted_is_verified,
)
from accounting_module_readiness import build_accounting_module_status
from financial_provider_apps_legacy import (
    _invoice_summaries,
    build_provider_catalog,
    ensure_user_settings,
)

_USER_PROJECTION = {
    "_id": 0,
    "id": 1,
    "name": 1,
    "email": 1,
    "role": 1,
    "is_owner": 1,
    "created_by": 1,
    "accounting_permissions": 1,
}


async def fresh_accounting_user(db, user: dict[str, Any]) -> dict[str, Any]:
    fresh = await db.users.find_one({"id": user["id"]}, _USER_PROJECTION)
    if not fresh:
        return user
    # Some auth dependencies may carry a computed owner marker. Preserve it
    # without trusting client input or persisting it to the user document.
    if user.get("is_owner") is True:
        fresh["is_owner"] = True
    return fresh


async def _provider_summary(db, owner_id: str) -> dict[str, int]:
    invoice_summaries = await _invoice_summaries(db, owner_id)
    merchant_settings = await ensure_user_settings(db, owner_id)
    apps = build_provider_catalog(
        merchant_settings,
        invoice_summary=invoice_summaries,
    )
    verified = await db.financial_provider_tax_invoices_v2.count_documents({
        "user_id": owner_id,
        "verification_status": "verified",
        "status": {"$ne": "void"},
    })
    tax_invoices = sum(int(app.get("tax_invoice_count") or 0) for app in apps)
    return {
        "providers": len(apps),
        "tax_invoices": tax_invoices,
        "verified_tax_invoices": int(verified or 0),
        "unverified_tax_invoices": max(tax_invoices - int(verified or 0), 0),
    }


def install_accounting_status_routes(router, db, current_user: Callable):
    @router.get("/accounting-module/access")
    async def accounting_module_access(user: dict = Depends(current_user)):
        fresh = await fresh_accounting_user(db, user)
        return {
            "operation_id": OPERATION_ID,
            "user_id": fresh.get("id"),
            "accounting_owner_id": accounting_owner_id(fresh),
            "default_policy": "deny_all_non_owner",
            "permissions": accounting_permissions_for_user(fresh),
            "is_owner": is_owner(fresh),
        }

    @router.get("/accounting-module/status")
    async def accounting_module_status(
        page: str = Query(default="home", pattern="^(home|opening-balances)$"),
        user: dict = Depends(current_user),
    ):
        fresh = await fresh_accounting_user(db, user)
        required = (
            "accounting.opening_balances.view"
            if page == "opening-balances"
            else "accounting.home.view"
        )
        require_accounting_permission(fresh, required)
        owner_id = accounting_owner_id(fresh)
        settings = await db.settings.find_one(
            {"user_id": owner_id},
            {"_id": 0, "mezan2_financial_cutover": 1},
        )
        cutover = (settings or {}).get("mezan2_financial_cutover") or {}
        provider = await _provider_summary(db, owner_id)
        opening_verified = await opening_posted_is_verified(
            db,
            user_id=owner_id,
            cutover=cutover,
        )
        preliminary = build_accounting_module_status(
            cutover,
            provider_summary=provider,
            opening_posted_verified=opening_verified,
        )
        ledger = None
        cutover_at = preliminary["cutover"].get("cutover_at")
        if preliminary["cutover"].get("safe_active") and cutover_at:
            ledger = await ledger_only_home_balances(
                db,
                user_id=owner_id,
                cutover_at=cutover_at,
            )
        return build_accounting_module_status(
            cutover,
            provider_summary=provider,
            opening_posted_verified=opening_verified,
            ledger_balances=ledger,
        )

    return router
