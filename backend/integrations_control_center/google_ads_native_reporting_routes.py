"""Owner-only Google Ads reporting routes for Integrations V2."""
from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from .google_ads_account_selection import (
    GOOGLE_ADS_PROVIDER_ID,
    get_google_ads_account_selection,
)
from .google_ads_native_reporting import (
    GOOGLE_ADS_REPORTING_SOURCE_MODE,
    GoogleAdsReportingError,
    GoogleAdsReportingSyncInput,
    google_ads_reporting_enabled,
    google_ads_reporting_missing_configuration,
    run_google_ads_reporting_sync,
)
from .google_oauth_security import google_oauth_configured


def _detail(exc: GoogleAdsReportingError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
        "result": exc.result,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def google_ads_reporting_readiness(db: Any, user_id: str) -> dict[str, Any]:
    selection = await get_google_ads_account_selection(db, user_id)
    missing = google_ads_reporting_missing_configuration()
    enabled = google_ads_reporting_enabled()
    ready = bool(
        not missing
        and enabled
        and selection.get("selected_count", 0) > 0
        and google_oauth_configured()
    )
    return {
        "provider": GOOGLE_ADS_PROVIDER_ID,
        "configured": not missing,
        "enabled": enabled,
        "ready": ready,
        "missing": missing,
        "selection": selection,
        "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_google_ads_native_reporting_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(f"/{GOOGLE_ADS_PROVIDER_ID}/reporting-readiness")
    async def reporting_readiness(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await google_ads_reporting_readiness(db, str(owner["id"]))

    @router.post(f"/{GOOGLE_ADS_PROVIDER_ID}/reporting-sync")
    async def reporting_sync(
        payload: GoogleAdsReportingSyncInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        run_id = str(uuid.uuid4())
        readiness = await google_ads_reporting_readiness(db, user_id)
        if not readiness["ready"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "google_ads_reporting_not_ready",
                    "message": (
                        "أكمل Google OAuth، اختر الحسابات، وفعّل راية التقارير أولًا."
                    ),
                    "readiness": readiness,
                },
            )
        try:
            result = await run_google_ads_reporting_sync(db, user_id, payload)
        except GoogleAdsReportingError as exc:
            await db.mezan_integration_sync_runs_v2.insert_one(
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "provider": GOOGLE_ADS_PROVIDER_ID,
                    "run_type": "google_ads_reporting_manual",
                    "status": "failed",
                    "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
                    "summary": exc.result,
                    "error": _detail(exc),
                }
            )
            raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
        await db.mezan_integration_sync_runs_v2.insert_one(
            {
                "run_id": run_id,
                "user_id": user_id,
                "provider": GOOGLE_ADS_PROVIDER_ID,
                "run_type": "google_ads_reporting_manual",
                "status": result["status"],
                "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
                "summary": {
                    "rows_saved": result["rows_saved"],
                    "accounts_attempted": result["accounts_attempted"],
                    "accounts_complete": result["accounts_complete"],
                    "errors_count": result["errors_count"],
                },
                "error": None,
            }
        )
        return {"run_id": run_id, **result}


__all__ = [
    "attach_google_ads_native_reporting_routes",
    "google_ads_reporting_readiness",
]
