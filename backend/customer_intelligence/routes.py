"""Tenant-scoped Customer Intelligence preview, inbox and draft-review routes."""
from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from ai_store_access_contract import (
    PERMISSIONS,
    effective_permissions,
    find_role_assignment,
)
from meta_reviewer_access import (
    META_REVIEWER_CI_PERMISSIONS,
    is_meta_reviewer,
    require_review_scope,
)

from .inbox import CustomerIntelligenceInboxService, LiveInboxResponse
from .instagram_provisioning import (
    InstagramProvisionIn,
    InstagramProvisionResult,
    InstagramProvisioningError,
    InstagramProvisioningService,
    InstagramSetupPublic,
)
from .learning_status import (
    CustomerLearningStatusPublic,
    CustomerLearningStatusService,
)
from .models import CustomerIntelligenceWorkspaceResponse
from .reply_suggestions import (
    ESCALATE_PERMISSION,
    INBOX_READ_PERMISSION,
    SUGGESTION_REVIEW_PERMISSION,
    ConversationNotFound,
    CustomerIntelligenceActor,
    ReplySuggestionConflict,
    ReplySuggestionNotFound,
    ReplySuggestionProviderError,
    ReplySuggestionPublic,
    ReplySuggestionReviewIn,
    ReplySuggestionService,
)
from .service import CustomerIntelligencePreviewService


FEATURE_FLAG_ENV = "MEZAN_CUSTOMER_INTELLIGENCE_PHASE1_ENABLED"
LIVE_INBOX_FEATURE_FLAG_ENV = "MEZAN_CUSTOMER_INTELLIGENCE_LIVE_INBOX_ENABLED"


def _feature_enabled() -> bool:
    return os.getenv(FEATURE_FLAG_ENV, "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _live_inbox_enabled() -> bool:
    return os.getenv(LIVE_INBOX_FEATURE_FLAG_ENV, "true").strip().lower() in {
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


async def _actor_context(db: Any, user: Any) -> CustomerIntelligenceActor:
    if not isinstance(user, dict) or not str(user.get("id") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authenticated_user_missing_id"},
        )
    actor_id = str(user["id"]).strip()
    role = str(user.get("role") or "").strip().casefold()
    if is_meta_reviewer(user):
        principal = require_review_scope(user, "customer_intelligence")
        return CustomerIntelligenceActor(
            actor_id=actor_id,
            owner_user_id=str(principal["id"]),
            permissions=META_REVIEWER_CI_PERMISSIONS,
            is_owner=False,
        )
    if role == "owner" or user.get("is_owner") is True:
        return CustomerIntelligenceActor(
            actor_id=actor_id,
            owner_user_id=actor_id,
            permissions=frozenset(PERMISSIONS),
            is_owner=True,
        )
    owner_user_id = str(user.get("created_by") or "").strip()
    if not owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "employee_store_not_linked"},
        )
    assignment = await find_role_assignment(
        db,
        owner_user_id=owner_user_id,
        user_id=actor_id,
    )
    return CustomerIntelligenceActor(
        actor_id=actor_id,
        owner_user_id=owner_user_id,
        permissions=frozenset(effective_permissions(assignment)),
        is_owner=False,
    )


def _require_actor_permission(
    actor: CustomerIntelligenceActor,
    permission: str,
) -> None:
    if permission not in actor.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "customer_intelligence_permission_required",
                "permission": permission,
            },
        )


def _reply_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ConversationNotFound, ReplySuggestionNotFound)):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code},
        )
    if isinstance(exc, ReplySuggestionProviderError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code},
        )
    if isinstance(exc, ReplySuggestionConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "reply_suggestion_failed"},
    )


def _instagram_error_detail(exc: InstagramProvisioningError) -> dict[str, Any]:
    """Return owner-safe provider diagnostics without credentials or raw IDs."""
    detail: dict[str, Any] = {"code": exc.code}
    for key in (
        "operation",
        "http_status",
        "meta_error_code",
        "error_subcode",
        "trace_id",
        "page_subscription_permission_ready",
    ):
        value = getattr(exc, key, None)
        if value is not None:
            detail[key] = value
    missing_permissions = list(
        getattr(exc, "missing_page_permissions", ()) or ()
    )
    if missing_permissions:
        detail["missing_page_permissions"] = missing_permissions
    return detail


def make_customer_intelligence_router(
    current_user: Callable,
    *,
    db: Any | None = None,
    service: CustomerIntelligencePreviewService | None = None,
    inbox_service: CustomerIntelligenceInboxService | None = None,
    reply_suggestion_service: ReplySuggestionService | None = None,
    instagram_provisioning_service: InstagramProvisioningService | None = None,
    learning_status_service: CustomerLearningStatusService | None = None,
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

    else:
        preview_service = service or CustomerIntelligencePreviewService()

        @router.get(
            "/workspace",
            response_model=CustomerIntelligenceWorkspaceResponse,
        )
        async def workspace(user: dict = Depends(current_user)) -> dict:
            require_review_scope(user, "customer_intelligence")
            return preview_service.workspace()

    live_service = inbox_service or (
        CustomerIntelligenceInboxService(db) if db is not None else None
    )

    learning_status = learning_status_service or (
        CustomerLearningStatusService(db) if db is not None else None
    )
    if learning_status is not None and _live_inbox_enabled():

        @router.get(
            "/learning/status",
            response_model=CustomerLearningStatusPublic,
        )
        async def get_customer_learning_status(
            response: Response,
            user: dict = Depends(current_user),
        ) -> CustomerLearningStatusPublic:
            actor = await _actor_context(db, user)
            _require_actor_permission(actor, INBOX_READ_PERMISSION)
            response.headers["Cache-Control"] = "no-store, private"
            return await learning_status.status(owner_user_id=actor.owner_user_id)

    if live_service is not None and _live_inbox_enabled():

        @router.get(
            "/inbox",
            response_model=LiveInboxResponse,
        )
        async def inbox(
            response: Response,
            limit: int = Query(default=20, ge=1, le=20),
            messages_limit: int = Query(default=30, ge=1, le=50),
            offset: int = Query(default=0, ge=0, le=10_000),
            user: dict = Depends(current_user),
        ) -> LiveInboxResponse:
            actor = await _actor_context(db, user)
            _require_actor_permission(actor, INBOX_READ_PERMISSION)
            response.headers["Cache-Control"] = "no-store, private"
            return await live_service.inbox(
                owner_user_id=actor.owner_user_id,
                actor_id=actor.actor_id,
                is_owner=actor.is_owner,
                limit=limit,
                messages_limit=messages_limit,
                offset=offset,
            )

    suggestions = reply_suggestion_service or (
        ReplySuggestionService(db) if db is not None else None
    )
    if suggestions is not None and _live_inbox_enabled():

        @router.post(
            "/conversations/{conversation_id}/reply-suggestion",
            response_model=ReplySuggestionPublic,
            status_code=status.HTTP_201_CREATED,
        )
        async def create_reply_suggestion(
            conversation_id: str,
            response: Response,
            user: dict = Depends(current_user),
        ) -> ReplySuggestionPublic:
            actor = await _actor_context(db, user)
            _require_actor_permission(actor, INBOX_READ_PERMISSION)
            _require_actor_permission(actor, SUGGESTION_REVIEW_PERMISSION)
            response.headers["Cache-Control"] = "no-store, private"
            try:
                return await suggestions.create(
                    actor=actor,
                    conversation_id=conversation_id,
                )
            except (
                ConversationNotFound,
                ReplySuggestionConflict,
                ReplySuggestionProviderError,
            ) as exc:
                raise _reply_error(exc) from exc

        @router.get(
            "/conversations/{conversation_id}/reply-suggestion",
            response_model=ReplySuggestionPublic | None,
        )
        async def get_reply_suggestion(
            conversation_id: str,
            response: Response,
            user: dict = Depends(current_user),
        ) -> ReplySuggestionPublic | None:
            actor = await _actor_context(db, user)
            _require_actor_permission(actor, INBOX_READ_PERMISSION)
            response.headers["Cache-Control"] = "no-store, private"
            try:
                return await suggestions.latest(
                    actor=actor,
                    conversation_id=conversation_id,
                )
            except (ConversationNotFound, ReplySuggestionConflict) as exc:
                raise _reply_error(exc) from exc

        @router.post(
            "/conversations/{conversation_id}/reply-suggestion/{suggestion_id}/review",
            response_model=ReplySuggestionPublic,
        )
        async def review_reply_suggestion(
            conversation_id: str,
            suggestion_id: str,
            response: Response,
            review: ReplySuggestionReviewIn = Body(...),
            user: dict = Depends(current_user),
        ) -> ReplySuggestionPublic:
            actor = await _actor_context(db, user)
            _require_actor_permission(actor, INBOX_READ_PERMISSION)
            permission = (
                ESCALATE_PERMISSION
                if review.decision == "escalate"
                else SUGGESTION_REVIEW_PERMISSION
            )
            _require_actor_permission(actor, permission)
            response.headers["Cache-Control"] = "no-store, private"
            try:
                return await suggestions.review(
                    actor=actor,
                    conversation_id=conversation_id,
                    suggestion_id=suggestion_id,
                    review=review,
                )
            except (
                ConversationNotFound,
                ReplySuggestionNotFound,
                ReplySuggestionConflict,
            ) as exc:
                raise _reply_error(exc) from exc

    instagram_setup = instagram_provisioning_service or (
        InstagramProvisioningService(db) if db is not None else None
    )
    if instagram_setup is not None and _live_inbox_enabled():

        @router.get(
            "/channels/instagram/setup",
            response_model=InstagramSetupPublic,
        )
        async def get_instagram_setup(
            response: Response,
            user: dict = Depends(current_user),
        ) -> InstagramSetupPublic:
            owner = require_review_scope(user, "integrations.meta")
            response.headers["Cache-Control"] = "no-store, private"
            try:
                return await instagram_setup.setup(owner_user_id=str(owner["id"]))
            except InstagramProvisioningError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_instagram_error_detail(exc),
                ) from exc

        @router.post(
            "/channels/instagram/setup",
            response_model=InstagramProvisionResult,
            status_code=status.HTTP_201_CREATED,
        )
        async def provision_instagram(
            request: InstagramProvisionIn,
            response: Response,
            user: dict = Depends(current_user),
        ) -> InstagramProvisionResult:
            owner = require_review_scope(user, "integrations.meta")
            response.headers["Cache-Control"] = "no-store, private"
            try:
                return await instagram_setup.provision(
                    owner_user_id=str(owner["id"]),
                    request=request,
                )
            except InstagramProvisioningError as exc:
                status_code = (
                    status.HTTP_404_NOT_FOUND
                    if exc.code == "instagram_candidate_not_found"
                    else status.HTTP_409_CONFLICT
                )
                raise HTTPException(
                    status_code=status_code,
                    detail=_instagram_error_detail(exc),
                ) from exc

    return router
