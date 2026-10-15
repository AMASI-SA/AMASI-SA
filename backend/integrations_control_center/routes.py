"""Owner-only HTTP routes for Apps & Integrations Control Center V2."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from meta_reviewer_access import (
    META_INTEGRATION_PROVIDERS,
    is_meta_reviewer,
    require_review_scope,
)

from .catalog import PROVIDER_BY_ID, provider_or_none
from .models import (
    ActivityListResponse,
    CapabilityResponse,
    ConnectionTestResponse,
    OverviewResponse,
    SnapchatAnalyticsSyncResponse,
)
from .service import IntegrationsControlCenterService
from .snapchat_analytics_backfill import (
    SnapchatAnalyticsSyncError,
    SnapchatAnalyticsSyncInput,
)


def _is_owner(user: Any) -> bool:
    if not isinstance(user, dict):
        return False
    role = str(user.get("role") or "").strip().lower()
    return role == "owner" or user.get("is_owner") is True


def _require_owner(user: Any) -> dict:
    if not _is_owner(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "owner_only", "message": "صفحة التطبيقات والتكاملات متاحة للمالك فقط."})
    if not user.get("id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "authenticated_user_missing_id", "message": "تعذر تحديد هوية مالك المتجر."})
    return user


def _require_meta_integration_access(user: Any) -> dict:
    return require_review_scope(user, "integrations.meta")


def _filter_reviewer_overview(payload: dict, user: Any) -> dict:
    if not is_meta_reviewer(user):
        return payload
    result = dict(payload)
    providers = [
        row for row in payload.get("providers", [])
        if row.get("provider") in META_INTEGRATION_PROVIDERS
    ]
    result["providers"] = providers
    result["summary"] = {
        "total": len(providers),
        "connected": sum(row.get("connection_status") == "connected" for row in providers),
        "api_connections": sum(row.get("connection_provenance") == "api_connection" for row in providers),
        "legacy_integrations": sum(row.get("connection_provenance") == "legacy_integration" for row in providers),
        "data_feeds": sum(row.get("connection_provenance") == "data_feed" for row in providers),
        "disconnected": sum(row.get("connection_provenance") == "disconnected" for row in providers),
        "planned": sum(row.get("connection_provenance") == "planned" for row in providers),
        "unknown": sum(row.get("connection_provenance") == "unknown" for row in providers),
        "healthy": sum((row.get("health") or {}).get("status") == "healthy" for row in providers),
        "missing_permissions": sum(bool((row.get("permissions") or {}).get("missing")) for row in providers),
        "attention_required": sum(
            (row.get("health") or {}).get("status") in {"degraded", "unhealthy"}
            or row.get("connection_status") in {"needs_reauth", "expired", "error"}
            for row in providers
        ),
    }
    return result


def _validated_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    definition = provider_or_none(provider)
    if not definition:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "unknown_integration_provider", "message": "منصة التكامل غير معروفة."})
    return definition.provider


def make_integrations_control_center_router(db: Any, current_user: Callable) -> APIRouter:
    from openai_integration_status_support import install_openai_integration_status_support

    install_openai_integration_status_support()
    router = APIRouter(prefix="/integrations-v2", tags=["apps-integrations-control-center-v2"])
    service = IntegrationsControlCenterService(db)

    @router.get("/overview", response_model=OverviewResponse)
    async def overview(user: dict = Depends(current_user)) -> dict:
        principal = _require_meta_integration_access(user)
        payload = await service.overview(str(principal["id"]))
        return _filter_reviewer_overview(payload, user)

    @router.get("/capabilities", response_model=CapabilityResponse)
    async def capabilities(user: dict = Depends(current_user)) -> dict:
        principal = _require_meta_integration_access(user)
        payload = await service.capabilities(str(principal["id"]))
        if is_meta_reviewer(user):
            payload = dict(payload)
            payload["providers"] = [
                row for row in payload.get("providers", [])
                if row.get("provider") in META_INTEGRATION_PROVIDERS
            ]
        return payload

    @router.get("/sync-runs", response_model=ActivityListResponse)
    async def sync_runs(provider: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=100), user: dict = Depends(current_user)) -> dict:
        principal = _require_meta_integration_access(user)
        provider_id = _validated_provider(provider)
        if is_meta_reviewer(user):
            if provider_id and provider_id not in META_INTEGRATION_PROVIDERS:
                raise HTTPException(status_code=403, detail={"code": "meta_review_provider_denied"})
            provider_id = provider_id or "meta_ads"
        return await service.list_sync_runs(str(principal["id"]), provider=provider_id, limit=limit)

    @router.get("/errors", response_model=ActivityListResponse)
    async def errors(provider: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=100), user: dict = Depends(current_user)) -> dict:
        principal = _require_meta_integration_access(user)
        provider_id = _validated_provider(provider)
        if is_meta_reviewer(user):
            if provider_id and provider_id not in META_INTEGRATION_PROVIDERS:
                raise HTTPException(status_code=403, detail={"code": "meta_review_provider_denied"})
            provider_id = provider_id or "meta_ads"
        return await service.list_errors(str(principal["id"]), provider=provider_id, limit=limit)

    # Kept only for the focused legacy harness. The production package
    # composer removes this route unconditionally and installs the native
    # Mezan 2 implementation at the same URL.
    @router.post("/snapchat_ads/sync", response_model=SnapchatAnalyticsSyncResponse)
    async def sync_snapchat_ads(
        payload: SnapchatAnalyticsSyncInput,
        user: dict = Depends(current_user),
    ) -> dict:
        owner = _require_owner(user)
        try:
            return await service.sync_snapchat_analytics(str(owner["id"]), payload)
        except SnapchatAnalyticsSyncError as exc:
            result = exc.result or {}
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "run_id": getattr(exc, "run_id", None),
                    "provider": "snapchat_ads",
                    "status": "failed",
                    "date_from": result.get("date_from") or payload.from_date,
                    "date_to": result.get("date_to") or payload.to_date,
                    "accounts_attempted": int(result.get("accounts_synced") or 0),
                    "accounts_complete": int(result.get("accounts_complete") or 0),
                    "rows_saved": int(result.get("rows_saved") or 0),
                    "errors_count": int(result.get("errors_count") or 1),
                    "source_only": True,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                },
            ) from exc

    @router.post("/{provider}/test-connection", response_model=ConnectionTestResponse)
    async def test_connection(provider: str, user: dict = Depends(current_user)) -> dict:
        principal = _require_meta_integration_access(user)
        provider_id = _validated_provider(provider)
        if is_meta_reviewer(user) and provider_id not in META_INTEGRATION_PROVIDERS:
            raise HTTPException(status_code=403, detail={"code": "meta_review_provider_denied"})
        definition = PROVIDER_BY_ID[provider_id]
        if not definition.legacy_sources or definition.planned:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "local_probe_not_available", "message": "لا يوجد اختبار محلي آمن لهذه المنصة في المرحلة الأولى."})
        return await service.test_connection(str(principal["id"]), provider_id)

    return router


def attach_integrations_control_center_routes(parent_router: APIRouter, db: Any) -> None:
    from auth import get_current_user_from_db

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    parent_router.include_router(make_integrations_control_center_router(db, current_user))


make_integrations_v2_router = make_integrations_control_center_router
attach_integrations_v2_routes = attach_integrations_control_center_routes
