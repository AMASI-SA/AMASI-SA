"""Owner-only routes for the unified advertising manager."""
from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status

from integrations_control_center.routes import _require_owner

from .account_cost_settings import attach_account_cost_settings_routes
from .models import AdsManagerOverview
from .service import AdsManagerService


_ERROR_MESSAGES = {
    "invalid_date": "استخدم تاريخًا صحيحًا بصيغة YYYY-MM-DD.",
    "date_to_before_date_from": "تاريخ النهاية يجب ألا يسبق تاريخ البداية.",
    "future_date_not_allowed": "لا يمكن طلب فترة مستقبلية.",
    "range_too_wide": "الحد الأقصى للفترة هو 90 يومًا.",
    "invalid_provider": "منصة الإعلانات غير معروفة.",
}
FEATURE_FLAG_ENV = "MEZAN_ADS_MANAGER_READ_ONLY_ENABLED"


def _feature_enabled() -> bool:
    return os.getenv(FEATURE_FLAG_ENV, "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def make_ads_manager_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(
        prefix="/ads-manager",
        tags=["unified-ads-manager-read-only"],
    )

    # Account cost settings are a native Mezan 2 control surface and remain
    # available independently from the read-only overview feature flag.
    attach_account_cost_settings_routes(
        router,
        db,
        current_user,
        _require_owner,
    )

    if not _feature_enabled():
        @router.get("/overview", include_in_schema=False)
        async def overview_disabled() -> None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "feature_disabled",
                    "message": "مدير الإعلانات للقراءة غير مفعّل.",
                },
            )

        return router

    service = AdsManagerService(db)

    @router.get("/overview", response_model=AdsManagerOverview)
    async def overview(
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        provider: str = Query(default="all"),
        campaign_query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=50, ge=10, le=100),
        user: dict = Depends(current_user),
    ) -> dict:
        owner = _require_owner(user)
        try:
            return await service.overview(
                str(owner["id"]),
                date_from=date_from,
                date_to=date_to,
                provider=provider,
                campaign_query=campaign_query,
                page=page,
                limit=limit,
            )
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": code,
                    "message": _ERROR_MESSAGES.get(code, "تعذر قراءة نطاق التقرير."),
                },
            ) from exc

    return router
