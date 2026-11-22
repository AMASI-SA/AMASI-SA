"""Small HTTP boundary for request-driven saved display results."""
import asyncio
from math import ceil
from typing import Literal

from fastapi import Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from operational_balance_store import fail
from operational_workspace_read_builders import make_spec, authorize_spec


async def serve_projection(db, user, request, family, *, filters=None, resource=None,
                           page=1, page_size=24, focus_id=None):
    from operational_visible_report_read import run_visible_report_read
    # The visible HTTP reader owns cancellation even for a ready multi-chunk
    # statement. Durable child work owns its separate, shared demand leases.
    ownership = {}
    try:
        return await run_visible_report_read(request, lambda: _serve_projection(
            db, user, request, family, filters=filters, resource=resource,
            page=page, page_size=page_size, focus_id=focus_id, ownership=ownership))
    except BaseException:
        if ownership:
            from demand_read_store import release
            try:
                await release(db, ownership["spec"], ownership["consumer"])
            except (asyncio.CancelledError, Exception):
                # Preserve the original failure/cancellation; a failed cleanup
                # expires through the existing short consumer lease.
                pass
        raise


async def _serve_projection(db, user, request, family, *, filters=None, resource=None,
                            page=1, page_size=24, focus_id=None, ownership):
    from demand_read_store import (InvalidReadSpec, ProjectionUnavailable, SnapshotChanged,
        demand, find_row_ordinal, projection_key, read_ready)
    consumer = request.headers.get("X-Mezan-Read-Consumer")
    spec = await make_spec(db, user, request, family, filters=filters, resource=resource)
    key = projection_key(spec)
    try:
        # The first bounded page supplies the manifest's count; an out-of-range
        # or focused page is then clamped without scanning the result rows.
        result = await read_ready(db, spec, offset=(page - 1) * page_size, limit=page_size)
        if result is not None:
            total = result["total"]
            pages = max(1, ceil(total / page_size))
            selected_page = min(page, pages)
            if focus_id:
                ordinal = await find_row_ordinal(db, spec, focus_id, snapshot=result["snapshot"])
                if ordinal is not None:
                    selected_page = ordinal // page_size + 1
            if selected_page != page:
                result = await read_ready(db, spec, offset=(selected_page - 1) * page_size,
                                          limit=page_size, snapshot=result["snapshot"])
                if result is None:
                    fail("operational_read_source_changed", "تغيرت البيانات؛ أعد تحميل الصفحة", 409)
            await authorize_spec(db, spec)
            response = {**result["header"], "snapshot": {"generated_at": result["generated_at"],
                **spec["source_revision"], "display_only": True}}
            if result["items"] is not None:
                if family == "operational_statement":
                    items = list(result["items"])
                    while len(items) < total:
                        part = await read_ready(db, spec, offset=len(items), limit=300, snapshot=result["snapshot"])
                        if part is None or not part["items"]:
                            fail("operational_read_snapshot_unavailable", "تعذر إكمال الكشف المحفوظ؛ أعد المحاولة", 503)
                        items.extend(part["items"])
                    await authorize_spec(db, spec)
                    response["items"] = items
                else:
                    response.update(items=result["items"], total=total, page=selected_page,
                        page_size=page_size, total_pages=pages, pages=pages)
            return JSONResponse(content=jsonable_encoder(response), headers={"Cache-Control": "no-store"})
        ownership.update(spec=spec, consumer=consumer)
        status = await demand(db, spec, consumer,
                              retry=request.headers.get("X-Mezan-Read-Poll") != "1")
        await authorize_spec(db, spec)
        if status["status"] == "failed":
            fail(status.get("error_code") or "read_preparation_failed", "تعذر تجهيز البيانات؛ أعد المحاولة", 503)
        # Waking this dedicated queue must not trigger financial reconciliation,
        # invoice sending or any other background scheduler.
        from background_worker_process import wake_read_projections
        await wake_read_projections()
        response = JSONResponse(status_code=202, content=jsonable_encoder({"status": "preparing", "key": key,
            "retry_after_ms": status.get("retry_after_ms") or 1500,
            "release_url": "/operational-balances/workspace/read-demand/" + key}),
            headers={"Cache-Control": "no-store"})
        return response
    except InvalidReadSpec:
        fail("operational_read_invalid", "تعذر بدء قراءة الصفحة", 422)
    except (ProjectionUnavailable, SnapshotChanged):
        fail("operational_read_snapshot_unavailable", "تعذر التحقق من النتيجة المحفوظة؛ أعد المحاولة", 503)


def install_demand_workspace_routes(router, db, current_user):
    from operational_workspace_read_builders import bootstrap, load_context, selected_kinds
    from operational_financial_position_views import FinancialPositionView

    @router.get("/workspace/bootstrap")
    async def workspace_bootstrap(request: Request, user=Depends(current_user)):
        return await bootstrap(db, user)

    @router.get("/workspace/home")
    async def home(request: Request, user=Depends(current_user)):
        return await serve_projection(db, user, request, "operational_home")

    @router.get("/workspace/directory")
    async def directory(request: Request, group: Literal["assets", "parties"],
                        kind: str | None = Query(default=None, max_length=200),
                        q: str = Query(default="", max_length=160), page: int = Query(default=1, ge=1),
                        page_size: int = Query(default=24, ge=1, le=24), user=Depends(current_user)):
        filters = {"group": group, "kind": kind, "q": q}
        selected_kinds(filters, groups=True)
        return await serve_projection(db, user, request, "operational_directory", filters=filters,
                                      page=page, page_size=page_size)

    @router.get("/workspace/options")
    async def options(request: Request, operation: str = Query(max_length=50),
                      role: Literal["recipient", "source", "filter"] = "recipient",
                      kind: str | None = Query(default=None, max_length=200),
                      selected_kind: str | None = Query(default=None, max_length=50),
                      selected_id: str | None = Query(default=None, max_length=200),
                      q: str = Query(default="", max_length=160), page: int = Query(default=1, ge=1),
                      page_size: int = Query(default=24, ge=1, le=24), user=Depends(current_user)):
        from operational_balance_access import OPERATION_LABELS
        if operation not in {"in", "out", "move", "ads", "wallet", "movement", "filter", *OPERATION_LABELS}:
            fail("operational_workspace_operation", "اختر العملية المطلوبة", 422)
        if (selected_kind is None) != (selected_id is None):
            fail("operational_account_filter_invalid", "اختر نوع الحساب والحساب معًا", 422)
        filters = {"operation": operation, "role": role, "kind": kind, "selected_kind": selected_kind,
                   "selected_id": selected_id, "q": q}
        selected_kinds(filters)
        return await serve_projection(db, user, request, "operational_options", filters=filters,
                                      page=page, page_size=page_size)

    @router.get("/workspace/selection/{kind}/{identity}")
    async def selection(request: Request, kind: str, identity: str, user=Depends(current_user)):
        return await serve_projection(db, user, request, "operational_selection", resource=(kind, identity))

    @router.get("/workspace/position")
    async def position(request: Request, page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=24, ge=1, le=24), user=Depends(current_user)):
        return await serve_projection(db, user, request, "operational_position", page=page, page_size=page_size)

    @router.get("/workspace/report")
    async def report(request: Request, view: FinancialPositionView = "all",
                     expected_revision: int | None = Query(default=None, ge=0),
                     expected_as_of: str | None = Query(default=None, min_length=1, max_length=64),
                     expected_started_at: str | None = Query(default=None, min_length=1, max_length=64),
                     user=Depends(current_user)):
        return await serve_projection(db, user, request, "operational_report", filters={"view": view,
            "expected_revision": expected_revision, "expected_as_of": expected_as_of,
            "expected_started_at": expected_started_at})

    @router.delete("/workspace/read-demand/{key}")
    async def release_demand(request: Request, key: str,
                             consumer_id: str = Query(min_length=8, max_length=128), user=Depends(current_user)):
        from demand_read_store import InvalidReadSpec, release_by_key
        actor, owner, _ = await load_context(db, user)
        try:
            await release_by_key(db, key, actor["id"], consumer_id, owner_id=owner)
        except InvalidReadSpec:
            fail("operational_read_invalid", "تعذر إنهاء قراءة الصفحة", 422)
        return {"released": True}
