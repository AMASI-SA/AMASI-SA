"""Read the signed-in employee's canonical balance without report/write grants.

This is a self-service view of the saved operational ledger. It never derives a
balance from notification history, refreshes a provider, or creates a second
financial writer. The ordinary Mezan report and statement projection remain
the authority for current and running balances.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
import re

from fastapi import Depends, Query, Request, Response

from employee_finance_notifications import financial_actor_context
from mobile_app_permissions import EMPLOYEE_BALANCE_PERMISSION
from operational_balance_archive import ArchiveError, SNAPSHOT, unpack_financial_snapshot
from operational_balance_service import fmt, report
from operational_balance_store import STATES, digest, fail
from operational_balance_workspace import _balance, ledger_items
from operational_financial_activity import cash_positions
from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread
from operational_financial_position_core import position_balance
from operational_party_projection_state import party_projection_state
from operational_visible_report_read import run_visible_report_read
from operational_workspace_projection import balance_note, projected_balance_status, row_order, signed_current_balance
from typing import Literal


ENTRY_ID = re.compile(r"^em_[a-f0-9]{64}$")
READ_TIMEOUT_SECONDS = 8
PAGE_SIZE = 10
# Current amounts and their provenance only: no order snapshots, notification
# inbox, audit pages, idempotency commands, bank directory or supplier directory.
READ_FIELDS = {key: 1 for key in (
    "_id", "owner_id", "revision", "status", "started_at", "openings", "movements",
    "engine.obligations", "engine.salary_days", "engine.as_of", SNAPSHOT,
)}


def _side(value):
    if value is None:
        return None
    amount = Decimal(value)
    return "credit" if amount > 0 else "debit" if amount < 0 else "zero"


def _day(value):
    from operational_workspace_projection import strict_day
    try:
        return strict_day(value).isoformat()
    except (ValueError, TypeError):
        return None


def _stamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


class _SelfLedgerPolicy:
    """Only a row already proven to belong to this employee is materialized.

    Its recorded source label, registrar and note belong to the employee's own
    statement. No permission object is persisted or handed to another route.
    The response allowlist never includes a source balance or another ledger.
    """
    def __init__(self, bound, state):
        self.actor = {"id": bound["recipient_id"]}
        identity = bound["employee_id"]
        self.references = {("employee", identity)}
        for item in state.get("movements", []):
            if (item.get("party_type"), item.get("party_id")) != ("employee", identity):
                continue
            if item.get("bank_kind") and item.get("bank_id"):
                self.references.add((item["bank_kind"], item["bank_id"]))
            for related in item.get("workspace_resources") or []:
                if related.get("kind") and related.get("id"):
                    self.references.add((related["kind"], related["id"]))

    def can_reference(self, kind, identity):
        return (kind, identity) in self.references


def _restore(stored, owner):
    if (not isinstance(stored, dict) or stored.get("_id") != owner or stored.get("owner_id") != owner
            or type(stored.get("revision")) is not int or stored["revision"] < 0):
        fail("employee_balance_snapshot_invalid", "تعذر التحقق من سجل الموظف المالي", 503)
    try:
        state = unpack_financial_snapshot(stored, selected={"obligations"})
    except ArchiveError:
        fail("employee_balance_snapshot_invalid", "تعذر التحقق من سجل الموظف المالي", 503)
    if (not isinstance(state.get("openings"), dict) or not isinstance(state.get("movements"), list)
            or not isinstance(state.get("engine", {}).get("obligations", {}), dict)):
        fail("employee_balance_snapshot_invalid", "تعذر التحقق من سجل الموظف المالي", 503)
    return state


def _project(state, bound):
    identity = bound["employee_id"]
    selected = party_projection_state(state, "employee", identity)
    computed = report(selected)
    employee = next((row for row in computed["parties"] if
        (row["party_type"], row["party_id"], row["currency"]) == ("employee", identity, "SAR")), None)
    resource = {"kind": "employee", "id": identity, "name": bound["employee_name"], "currency": "SAR",
        "access": {"balance": True, "movements": "all", "report": True, "print": False},
        "balance": _balance(employee or {}, "employee"),
        "position_balance": position_balance(employee, "employee") if employee is not None else "0.00"}
    current = signed_current_balance(resource)
    if current is None or not current.is_finite():
        fail("employee_balance_unavailable", "رصيد الموظف غير متاح؛ أعد المحاولة", 503)
    rows = ledger_items(state, computed, resource, _SelfLedgerPolicy(bound, state))
    status = projected_balance_status(resource, rows)
    custody_key = ("employee_custody", identity, "SAR")
    custody = cash_positions(state, {custody_key}).get(custody_key, {}).get("actual", Decimal(0))
    if not custody.is_finite():
        fail("employee_balance_unavailable", "رصيد العهدة غير متاح؛ أعد المحاولة", 503)
    snapshot = digest(["employee-balance-v1", bound["merchant_id"], bound["recipient_id"], identity,
        state["revision"], state.get("started_at"), state.get("status"), state.get("engine", {}).get("as_of")])
    return {"employee": {"id": identity, "name": bound["employee_name"]},
        "summary": {"current_balance": fmt(current or Decimal(0)), "balance_side": _side(current),
            "custody_amount": fmt(custody or Decimal(0)), "currency": "SAR"},
        "snapshot": snapshot, "balance_status": status, "balance_note": balance_note(status), "_rows": rows}


def _public(row, bound, *, detail=False):
    credit = row["side"] == "for_party"
    amount = row["amount"]
    value = {"id": "em_" + digest([bound["merchant_id"], bound["employee_id"], row["id"]]),
        "kind_label": row["description"], "description": row.get("note") or row["description"],
        "business_date": _day(row.get("business_date")), "recorded_at": _stamp(row.get("recorded_at")),
        "credit_amount": amount if credit else "0.00", "debit_amount": "0.00" if credit else amount,
        "balance_after": row.get("balance_after"), "balance_side_after": _side(row.get("balance_after"))}
    if detail:
        value.update(amount=amount, side="credit" if credit else "debit", currency="SAR",
            source_label=row.get("source_label"), recorded_by=row.get("recorded_by"), reference=row.get("reference"))
    return value


def _salary_view(rows, view):
    """Compact only proven automatic salary entries; retain original daily values and IDs."""
    salaries = sorted(
        (row for row in rows if row.get("automatic_history") == "salary"),
        key=row_order, reverse=True)
    if view == "salary_history":
        return salaries
    if view == "compact_salary" and salaries:
        latest = {**salaries[0], "salary_history_count": len(salaries)}
        return sorted(
            [row for row in rows if row.get("automatic_history") != "salary"] + [latest],
            key=row_order, reverse=True)
    return rows


async def employee_balance_read(db, user, *, page=1, snapshot=None, identity=None, view="all"):
    if (page > 1 or view == "salary_history") and snapshot is None:
        fail("employee_balance_snapshot_required", "أعد فتح الصفحة الأولى لعرض الحركات", 422)
    if identity is not None and not ENTRY_ID.fullmatch(identity):
        fail("employee_balance_movement_not_found", "القيد غير متاح", 404)
    bound = await financial_actor_context(db, user, required_permission=EMPLOYEE_BALANCE_PERMISSION)
    stored = await db[STATES].find_one({"_id": bound["merchant_id"], "owner_id": bound["merchant_id"]},
        READ_FIELDS, max_time_ms=4000)
    if stored is None or stored.get("status") not in {"active", "frozen"}:
        fail("employee_balance_ledger_unavailable", "لم يُفعّل السجل المالي للموظف بعد", 409)
    state = await prepare_in_thread(_restore, stored, bound["merchant_id"])
    result = await prepare_in_thread(_project, state, bound)
    if snapshot is not None and result["snapshot"] != snapshot:
        fail("employee_balance_snapshot_changed", "تغيرت الحركات المالية؛ أعد تحميل الصفحة الأولى", 409)
    rows = result.pop("_rows")
    if identity is not None:
        matches = [_public(row, bound, detail=True) for row in rows
            if "em_" + digest([bound["merchant_id"], bound["employee_id"], row["id"]]) == identity]
        if len(matches) != 1:
            fail("employee_balance_movement_not_found", "القيد غير متاح", 404)
        result = {"employee": result["employee"], "snapshot": result["snapshot"], "item": matches[0]}
    else:
        rows = _salary_view(rows, view)
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        items = []
        for row in rows[(page-1)*PAGE_SIZE:page*PAGE_SIZE]:
            item = _public(row, bound)
            if view == "compact_salary" and row.get("salary_history_count"):
                item.update(kind_label="استحقاق راتب", salary_history_count=row["salary_history_count"])
            items.append(item)
        result.update(items=items,
            pagination={"page": page, "page_size": PAGE_SIZE, "total": len(rows), "total_pages": total_pages})
        if view != "all":
            result["view"] = view
    # A logout, disabled account, changed employee link or tenant switch during
    # preparation cannot deliver financial rows under the old binding.
    current = await financial_actor_context(db, user, required_permission=EMPLOYEE_BALANCE_PERMISSION)
    if current != bound:
        fail("employee_financial_scope_changed", "تغير حساب الموظف؛ أعد فتح الصفحة", 403)
    preparation_checkpoint()
    return result


def install_employee_balance_routes(router, db, current_user):
    async def read(request, user, **kwargs):
        async def bounded():
            try:
                async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                    return await employee_balance_read(db, user, **kwargs)
            except TimeoutError:
                fail("employee_balance_read_timeout", "تعذر تحميل رصيدك الآن؛ أعد المحاولة", 503)
        return await run_visible_report_read(request, bounded)

    @router.get("/me/statement")
    async def statement(request: Request, response: Response, page: int = Query(default=1, ge=1, le=100000),
                        page_size: int = Query(default=10, ge=10, le=10),
                        snapshot: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
                        view: Literal["all", "compact_salary", "salary_history"] = Query(default="all"),
                        user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await read(request, user, page=page, snapshot=snapshot, view=view)

    @router.get("/me/movements/{identity}")
    async def detail(identity: str, request: Request, response: Response,
                     snapshot: str = Query(pattern=r"^[a-f0-9]{64}$"), user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await read(request, user, snapshot=snapshot, identity=identity)
