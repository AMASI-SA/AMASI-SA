"""HTTP routes for the read-only Qoyod invoice-review screen."""
from __future__ import annotations

from io import BytesIO
from typing import Any, Callable, Optional

from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from integrations.qoyod.invoice_review import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    build_invoice_review,
    build_invoice_review_workbook,
    safe_export_filename,
    sync_invoice_review,
)


def attach_invoice_review_routes(
    router,
    *,
    db,
    current_user,
    tenant_id: Callable[[dict], str],
    orders_owner_id: Callable[[dict], str],
    get_api_key: Callable[..., Any],
    build_api_client: Callable[..., Any],
) -> None:
    """Attach list, GET-only sync, and export endpoints to Qoyod router."""

    def _params(
        from_date: Optional[str],
        to_date: Optional[str],
        search: Optional[str],
        page: int,
        page_size: int,
    ) -> dict:
        return {
            "from_date": from_date,
            "to_date": to_date,
            "search": search,
            "page": page,
            "page_size": page_size,
        }

    async def _review_or_422(**kwargs) -> dict:
        try:
            return await build_invoice_review(db, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/invoice-review")
    async def invoice_review(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        search: Optional[str] = Query(None, max_length=160),
        page: int = Query(1, ge=1),
        page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        user: dict = Depends(current_user),
    ):
        return await _review_or_422(
            orders_user_id=orders_owner_id(user),
            markers_user_id=tenant_id(user),
            **_params(from_date, to_date, search, page, page_size),
        )

    @router.post("/invoice-review/sync")
    async def invoice_review_sync(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        search: Optional[str] = Query(None, max_length=160),
        page: int = Query(1, ge=1),
        page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        user: dict = Depends(current_user),
    ):
        tenant = tenant_id(user)
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(
                status_code=409,
                detail="qoyod_api_key_not_configured",
            )
        try:
            result = await sync_invoice_review(
                db,
                orders_user_id=orders_owner_id(user),
                markers_user_id=tenant,
                api_client=await build_api_client(db, tenant, key),
                **_params(from_date, to_date, search, page, page_size),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result

    @router.get("/invoice-review/export.xlsx")
    async def invoice_review_export(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        search: Optional[str] = Query(None, max_length=160),
        user: dict = Depends(current_user),
    ):
        report = await _review_or_422(
            orders_user_id=orders_owner_id(user),
            markers_user_id=tenant_id(user),
            from_date=from_date,
            to_date=to_date,
            search=search,
            page=1,
            page_size=MAX_PAGE_SIZE,
            include_all=True,
        )
        content = build_invoice_review_workbook(report)
        filename = safe_export_filename(
            report["from_date"], report["to_date"]
        )
        return StreamingResponse(
            BytesIO(content),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Exported-Rows": str(len(report["items"])),
            },
        )
