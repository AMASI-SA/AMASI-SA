"""Owner-only, GET-only routes for Customer Intelligence Phase 1."""
from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status

from .models import CustomerIntelligenceWorkspaceResponse
from .service import CustomerIntelligencePreviewService


FEATURE_FLAG_ENV = "MEZAN_CUSTOMER_INTELLIGENCE_PHASE1_ENABLED"


def _feature_enabled() -> bool:
    return os.getenv(FEATURE_FLAG_ENV, "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_owner(user: Any) -> dict:
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "owner_only",
                "message": "مركز ذكاء العملاء في مرحلته التجريبية متاح للمالك فقط.",
            },
        )
    role = str(user.get("role") or "").strip().lower()
    if role != "owner" and user.get("is_owner") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "owner_only",
                "message": "مركز ذكاء العملاء في مرحلته التجريبية متاح للمالك فقط.",
            },
        )
    if not user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "authenticated_owner_missing_id",
                "message": "تعذر تحديد هوية مالك المتجر.",
            },
        )
    return user


def make_customer_intelligence_router(
    current_user: Callable,
    *,
    service: CustomerIntelligencePreviewService | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/customer-intelligence/v1",
        tags=["customer-intelligence-phase1-preview"],
    )

    if not _feature_enabled():

        @router.get("/workspace", include_in_schema=False)
        async def workspace_disabled() -> None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "feature_disabled",
                    "message": "مركز ذكاء العملاء التجريبي غير مفعّل.",
                },
            )

        return router

    preview_service = service or CustomerIntelligencePreviewService()

    @router.get(
        "/workspace",
        response_model=CustomerIntelligenceWorkspaceResponse,
    )
    async def workspace(user: dict = Depends(current_user)) -> dict:
        _require_owner(user)
        return preview_service.workspace()

    return router
