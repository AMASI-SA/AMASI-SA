"""HTTP API for the gated Mezan return decision workflow."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status

from return_decision_engine import (
    ReturnCaseApproval,
    ReturnCaseCreate,
    ReturnInspection,
    approve_return_case,
    create_return_case,
    get_return_workspace,
    inspect_return_case,
)


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
                "message": "هذه الصفحة متاحة للمالك فقط.",
            },
        )
    return user


def _raise_engine_error(exc: Exception) -> None:
    code = str(exc)
    if isinstance(exc, LookupError):
        http_status = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, RuntimeError):
        http_status = status.HTTP_409_CONFLICT
    else:
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    raise HTTPException(
        status_code=http_status,
        detail={"code": code, "message": code},
    ) from exc


def make_return_decision_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/returns-v2",
        tags=["return-decision-engine"],
    )

    @router.get("/orders/{order_number}")
    async def return_workspace(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        try:
            return await get_return_workspace(
                db,
                user_id=str(owner["id"]),
                order_number=order_number,
            )
        except Exception as exc:
            _raise_engine_error(exc)

    @router.post(
        "/orders/{order_number}/cases",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_case(
        order_number: str,
        request: ReturnCaseCreate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        try:
            return await create_return_case(
                db,
                user_id=str(owner["id"]),
                user=owner,
                order_number=order_number,
                request=request,
            )
        except Exception as exc:
            _raise_engine_error(exc)

    @router.post("/cases/{case_id}/approve")
    async def approve_case(
        case_id: str,
        request: ReturnCaseApproval,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        try:
            return await approve_return_case(
                db,
                user_id=str(owner["id"]),
                user=owner,
                case_id=case_id,
                request=request,
            )
        except Exception as exc:
            _raise_engine_error(exc)

    @router.post("/cases/{case_id}/inspect")
    async def inspect_case(
        case_id: str,
        request: ReturnInspection,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        try:
            return await inspect_return_case(
                db,
                user_id=str(owner["id"]),
                user=owner,
                case_id=case_id,
                request=request,
            )
        except Exception as exc:
            _raise_engine_error(exc)

    return router
