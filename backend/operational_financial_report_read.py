"""Canonical owner financial-position read, shared by HTTP and the demand child.

The calculation and source-validation order are unchanged. Importing this module
creates no application, scheduler, database client, or external synchronization.
"""
from fastapi import HTTPException
from operational_balance_store import read, digest, fail, now
from operational_financial_activity_runtime import prepare_in_thread
from operational_financial_position_views import FORECAST_VIEWS, LABEL_VIEWS


async def read_financial_position(db, user, *, scope, view="all", expected_revision=None,
                                  expected_as_of=None, expected_started_at=None):
    actor, owner, _ = await scope(db, user, "reports")
    from operational_balance_service import accounting_inactive
    from operational_financial_position import build_financial_position
    from operational_financial_order_labels import attach_order_numbers
    from operational_financial_position_state import (
        read_financial_position_state, require_current_snapshot, require_snapshot_binding,
    )

    # Preserve the legacy/full-export audit-validation contract. Only
    # the new scoped views use the dedicated report-only snapshot.
    state = await read(db, owner) if view == "all" else await read_financial_position_state(db, owner)
    bound = view == "forecast" or any(value is not None for value in (
        expected_revision, expected_as_of, expected_started_at))
    if bound:
        require_snapshot_binding(state, revision=expected_revision,
            as_of=expected_as_of, started_at=expected_started_at)
    source_state = state
    if state["status"] == "active":
        try:
            await accounting_inactive(db, owner)
        except HTTPException as exc:
            if not isinstance(exc.detail, dict) or exc.detail.get("code") != "operational_accounting_active":
                raise
            state = {**state, "status": "accounting_active"}
    cost_exposure = None
    if view in FORECAST_VIEWS and state["status"] == "active" and state.get("engine", {}).get("as_of"):
        # The separately released cost-stage reader is an explicit optional
        # dependency. Missing coverage is visible in the report; never
        # substitute another balance authority or refresh/write on this GET.
        try:
            from operational_open_cost_exposure import project as cost_project
        except ModuleNotFoundError as exc:
            if exc.name != "operational_open_cost_exposure":
                raise
        else:
            try:
                cost_exposure = await cost_project(db, owner, state, as_of=state["engine"]["as_of"])
            except ValueError as exc:
                # The optional bounded reader can reject its source scope.
                # Preserve the verified balance snapshot while explicitly
                # withholding estimates; unexpected errors still propagate.
                code = str(exc)
                if code not in {"operational_source_rejected", "operational_source_scope_too_large",
                                "operational_source_owner_mismatch", "operational_source_read_timeout",
                                "operational_source_read_invalid"}:
                    raise
                cost_exposure = {
                    "status": "preview", "as_of": state["engine"]["as_of"],
                    "engine_as_of": state["engine"]["as_of"], "started_at": state["started_at"],
                    "currencies": {}, "groups": [], "complete": False,
                    "issues": [{"code": code, "count": 1, "source_collection": getattr(exc, "collection", None), "message":
                        "غير متاح: تعذر التحقق من مصادر تكاليف الطلبات المفتوحة؛ الأرصدة الحالية من آخر احتساب محفوظ."}],
                }
    import asyncio
    result = await prepare_in_thread(build_financial_position, state,
        cost_exposure=cost_exposure, generated_at=now(), view=view)
    if view in LABEL_VIEWS:
        result = await attach_order_numbers(db, owner, result)
    latest_actor, latest_owner, _ = await scope(db, user, "reports")
    if latest_owner != owner or latest_actor["id"] != actor["id"]:
        fail("operational_actor_scope_changed", "تغير نطاق الجلسة؛ أعد فتح الصفحة", 409)
    if state["status"] == "active":
        await accounting_inactive(db, owner)
    if bound:
        await require_current_snapshot(db, owner, source_state)
    result.update(session_scope=digest([owner, actor["id"]]), permissions={"reports": True,
        "confirm_delivery_after_start": state['status'] == 'active' and latest_actor.get('role') == 'owner'
                                        and latest_actor['id'] == owner})
    return result
