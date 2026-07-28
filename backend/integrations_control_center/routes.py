"""Owner-only HTTP routes for Apps & Integrations Control Center V2."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .catalog import PROVIDER_BY_ID, provider_or_none
from .models import (
    ActivityListResponse,
    CapabilityResponse,
    ConnectionTestResponse,
    OverviewResponse,
)
from .service import IntegrationsControlCenterService


def _is_owner(user: Any) -> bool:
    if not isinstance(user, dict):
        return False
    role = str(user.get("role") or "").strip().lower()
    return role == "owner" or user.get("is_owner") is True


def _require_owner(user: Any) -> dict:
    if not _is_owner(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "owner_only",
                "message": "صفحة التطبيقات والتكاملات متاحة للمالك فقط.",
            },
        )
    if not user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "authenticated_user_missing_id",
                "message": "تعذر تحديد هوية مالك المتجر.",
            },
        )
    return user


def _validated_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    definition = provider_or_none(provider)
    if not definition:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "unknown_integration_provider",
                "message": "منصة التكامل غير معروفة.",
            },
        )
    return definition.provider


def make_integrations_control_center_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    """Create the isolated router mounted below the application's `/api`."""
    # OpenAI is already used by Mezan's analyzer but predates this catalogue.
    # Install its secret-safe runtime card before constructing the service.
    from openai_integration_status_support import install_openai_integration_status_support

    install_openai_integration_status_support()
    router = APIRouter(
        prefix="/integrations-v2",
        tags=["apps-integrations-control-center-v2"],
    )
    service = IntegrationsControlCenterService(db)

    @router.get("/overview", response_model=OverviewResponse)
    async def overview(user: dict = Depends(current_user)) -> dict:
        owner = _require_owner(user)
        return await service.overview(str(owner["id"]))

    @router.get("/capabilities", response_model=CapabilityResponse)
    async def capabilities(user: dict = Depends(current_user)) -> dict:
        owner = _require_owner(user)
        return await service.capabilities(str(owner["id"]))

    @router.get("/sync-runs", response_model=ActivityListResponse)
    async def sync_runs(
        provider: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict:
        owner = _require_owner(user)
        return await service.list_sync_runs(
            str(owner["id"]),
            provider=_validated_provider(provider),
            limit=limit,
        )

    @router.get("/errors", response_model=ActivityListResponse)
    async def errors(
        provider: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict:
        owner = _require_owner(user)
        return await service.list_errors(
            str(owner["id"]),
            provider=_validated_provider(provider),
            limit=limit,
        )

    @router.post(
        "/{provider}/test-connection",
        response_model=ConnectionTestResponse,
    )
    async def test_connection(
        provider: str,
        user: dict = Depends(current_user),
    ) -> dict:
        owner = _require_owner(user)
        provider_id = _validated_provider(provider)
        definition = PROVIDER_BY_ID[provider_id]
        if not definition.legacy_sources or definition.planned:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "local_probe_not_available",
                    "message": (
                        "لا يوجد اختبار محلي آمن لهذه المنصة في المرحلة الأولى."
                    ),
                },
            )
        return await service.test_connection(str(owner["id"]), provider_id)

    return router


def attach_integrations_control_center_routes(
    parent_router: APIRouter,
    db: Any,
) -> None:
    """Compatibility helper for route modules that receive a parent router."""
    from auth import get_current_user_from_db

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    parent_router.include_router(
        make_integrations_control_center_router(db, current_user)
    )


# Short aliases make the route factory discoverable without changing the
# canonical names used by server.py.
make_integrations_v2_router = make_integrations_control_center_router
attach_integrations_v2_routes = attach_integrations_control_center_routes
