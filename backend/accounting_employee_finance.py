"""MZ2-native payroll, employee advances and custody.

Salary accruals are explicit MZ2 accounting events.  Cash movements are never
invented by this module: salary payments, advance grants/repayments and custody
grants/returns must consume one imported MZ2 daily bank movement exactly once.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounting_mz2_balances import read_mz2_write_balances
from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_mz2_reports import read_mz2_ledger
from ledger_core import post_txn_group


RIYADH = ZoneInfo("Asia/Riyadh")
MONEY = Decimal("0.01")
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PAYROLL_SOURCE = "accounting_payroll_p01"
MOVEMENT_ACTIONS = {
    "salary_payment",
    "advance_grant",
    "advance_repayment",
    "custody_grant",
    "custody_return",
}


def _money(value: Any) -> Decimal:
    try:
        number = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, "employee_amount_invalid") from None
    if not number.is_finite() or number <= 0:
        raise HTTPException(400, "employee_amount_must_be_positive")
    return number


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(400, "employee_accounting_timezone_required")
    return value.astimezone(timezone.utc)


def _movement_accounting_at(movement_date: str) -> datetime:
    try:
        day = datetime.strptime(movement_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(409, "daily_movement_date_invalid") from None
    # Bank statements often carry day precision only. Noon Riyadh is a stable
    # intra-day instant and preserves the economic date without pretending
    # that the bank supplied an exact timestamp.
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=RIYADH).astimezone(timezone.utc)


async def _actor_scope(db, user: dict[str, Any], permission: str) -> tuple[dict[str, Any], str]:
    actor = await fresh_accounting_user(db, user)
    require_accounting_permission(actor, permission)
    owner = accounting_owner_id(actor)
    if not owner:
        raise HTTPException(403, "accounting_owner_scope_missing")
    return actor, owner


async def _cutover(db, owner: str) -> datetime:
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
    )
    state = (row or {}).get("mezan2_financial_cutover") or {}
    if state.get("operation_id") != OPERATION_ID or state.get("status") != "active":
        raise HTTPException(409, "mz2_p01_not_active")
    try:
        cut = datetime.fromisoformat(str(state.get("cutover_at") or "").replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(409, "mz2_cutover_invalid") from None
    if cut.tzinfo is None or cut.utcoffset() is None:
        raise HTTPException(409, "mz2_cutover_invalid")
    return cut.astimezone(timezone.utc)


async def _require_post_cutover(db, owner: str, accounting_at: datetime) -> str:
    cut = await _cutover(db, owner)
    event = _aware_utc(accounting_at)
    if event <= cut:
        raise HTTPException(409, detail={
            "code": "employee_event_not_after_cutover",
            "cutover_at": cut.isoformat(),
            "accounting_at": event.isoformat(),
        })
    if event > datetime.now(timezone.utc):
        raise HTTPException(409, "employee_event_in_future")
    return event.isoformat()


async def _employee(db, owner: str, employee_id: str) -> dict[str, Any]:
    query = {
        "user_id": owner,
        "$or": [
            {"id": employee_id},
            {"employee_id": employee_id},
            {"external_id": employee_id},
            {"legacy_id": employee_id},
        ],
        "category": "employee",
        "status": {"$ne": "inactive"},
        "archived": {"$ne": True},
        "is_archived": {"$ne": True},
        "deleted": {"$ne": True},
        "is_deleted": {"$ne": True},
    }
    employee = await db.operating_salaries.find_one(
        query,
        {"_id": 0, "id": 1, "employee_id": 1, "name": 1, "monthly_amount": 1, "status": 1},
    )
    if not employee:
        raise HTTPException(404, "employee_not_found")
    employee["canonical_id"] = str(employee.get("id") or employee.get("employee_id") or employee_id)
    return employee


def _public_event(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_id"}


class PayrollAccrualIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str
    accrued_at: datetime
    employee_id: str | None = Field(default=None, max_length=200)
    amount: Decimal | None = None
    reason: str = Field(default="", max_length=500)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value: str) -> str:
        value = value.strip()
        if not PERIOD_RE.fullmatch(value):
            raise ValueError("period must be YYYY-MM")
        return value

    @field_validator("employee_id", "reason")
    @classmethod
    def strip_optional(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        return _money(value) if value is not None else None


class MovementClassifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str = Field(min_length=1, max_length=200)
    action: Literal[
        "salary_payment",
        "advance_grant",
        "advance_repayment",
        "custody_grant",
        "custody_return",
    ]
    apply_open_advances: bool = True
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("employee_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


async def _post_accrual(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    employee: dict[str, Any],
    period: str,
    accounting_at: str,
    amount: Decimal,
    reason: str,
) -> dict[str, Any]:
    employee_id = employee["canonical_id"]
    event_id = _hash([owner, "salary_accrual", employee_id, period])
    economic = {
        "employee_id": employee_id,
        "period": period,
        "accounting_at": accounting_at,
        "amount": format(amount, ".2f"),
    }
    economic_hash = _hash(economic)
    prior = await db.mz2_employee_financial_events.find_one(
        {"_id": event_id, "user_id": owner},
    )
    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise HTTPException(409, detail={
                "code": "salary_accrual_conflict",
                "employee_id": employee_id,
                "period": period,
            })
        return {**_public_event(prior), "state": "already_posted"}

    # Forces the clean-cutover/readiness contract inside the owner's active
    # transaction, and requires an explicitly approved zero/opening for this
    # employee payable account.
    await read_mz2_write_balances(
        db,
        owner=owner,
        required_accounts=[("employee", employee_id, "salary_payable")],
    )

    metadata = {
        "operation_id": OPERATION_ID,
        "source": PAYROLL_SOURCE,
        "payroll_event_id": event_id,
        "employee_id": employee_id,
        "employee_name": employee.get("name") or "",
        "period": period,
        "accounting_at": accounting_at,
        "reason": reason,
    }
    result = await post_txn_group(
        db,
        user_id=owner,
        actor_id=actor["id"],
        actor_name=actor.get("name") or actor.get("email") or actor["id"],
        txn_type="mz2_salary_accrual",
        notes=f"استحقاق راتب {period} — {employee.get('name') or employee_id}",
        metadata=metadata,
        entries=[
            {
                "entity_type": "expense",
                "entity_id": "salary",
                "sub_account": None,
                "side": "debit",
                "amount": float(amount),
                "entry_type": "salary_accrual",
            },
            {
                "entity_type": "employee",
                "entity_id": employee_id,
                "sub_account": "salary_payable",
                "side": "credit",
                "amount": float(amount),
                "entry_type": "salary_accrual",
            },
        ],
    )
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "_id": event_id,
        "id": event_id,
        "user_id": owner,
        "kind": "salary_accrual",
        "employee_id": employee_id,
        "employee_name": employee.get("name") or "",
        "period": period,
        "amount": format(amount, ".2f"),
        "accounting_at": accounting_at,
        "economic_hash": economic_hash,
        "txn_group_id": result["txn_group_id"],
        "status": "posted",
        "reason": reason,
        "posted_by": actor["id"],
        "posted_at": now,
    }
    await db.mz2_employee_financial_events.insert_one(event)
    return _public_event(event)


async def accrue_payroll_period(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    payload: PayrollAccrualIn,
) -> dict[str, Any]:
    accounting_dt = _aware_utc(payload.accrued_at)
    local_period = accounting_dt.astimezone(RIYADH).strftime("%Y-%m")
    if local_period != payload.period:
        raise HTTPException(409, detail={
            "code": "salary_period_date_mismatch",
            "period": payload.period,
            "accounting_period": local_period,
        })
    accounting_at = await _require_post_cutover(db, owner, accounting_dt)

    if payload.employee_id:
        employees = [await _employee(db, owner, payload.employee_id)]
    else:
        if payload.amount is not None:
            raise HTTPException(400, "salary_bulk_override_not_allowed")
        employees = await db.operating_salaries.find(
            {
                "user_id": owner,
                "category": "employee",
                "status": {"$ne": "inactive"},
                "archived": {"$ne": True},
                "is_archived": {"$ne": True},
                "deleted": {"$ne": True},
                "is_deleted": {"$ne": True},
            },
            {"_id": 0, "id": 1, "employee_id": 1, "name": 1, "monthly_amount": 1, "status": 1},
        ).sort("name", 1).to_list(500)
        if not employees:
            raise HTTPException(409, "no_active_employees")
        for employee in employees:
            employee["canonical_id"] = str(employee.get("id") or employee.get("employee_id") or "")
            if not employee["canonical_id"]:
                raise HTTPException(409, "employee_identity_missing")

    results = []
    for employee in employees:
        monthly = _money(employee.get("monthly_amount") or 0)
        amount = payload.amount if payload.employee_id and payload.amount is not None else monthly
        amount = _money(amount)
        if payload.employee_id and payload.amount is not None and amount != monthly and len(payload.reason) < 3:
            raise HTTPException(400, "salary_override_reason_required")
        results.append(await _post_accrual(
            db,
            owner=owner,
            actor=actor,
            employee=employee,
            period=payload.period,
            accounting_at=accounting_at,
            amount=amount,
            reason=payload.reason,
        ))

    return {
        "period": payload.period,
        "accounting_at": accounting_at,
        "employee_count": len(results),
        "posted": sum(1 for item in results if item.get("state") != "already_posted"),
        "already_posted": sum(1 for item in results if item.get("state") == "already_posted"),
        "items": results,
    }


async def _movement_for_employee(db, owner: str, movement_id: str) -> dict[str, Any]:
    movement = await db.mz2_daily_movements.find_one(
        {"user_id": owner, "id": movement_id},
    )
    if not movement:
        raise HTTPException(404, "daily_movement_not_found")
    if movement.get("receipt_id") or movement.get("confirmed_provider") or movement.get("explicit_provider"):
        raise HTTPException(409, "provider_movement_cannot_be_employee_cash")
    if movement.get("suggested_provider"):
        raise HTTPException(409, "provider_suggestion_must_be_resolved_first")
    return movement


async def classify_employee_movement(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    movement_id: str,
    payload: MovementClassifyIn,
) -> dict[str, Any]:
    if payload.action not in MOVEMENT_ACTIONS:
        raise HTTPException(400, "employee_action_invalid")
    employee = await _employee(db, owner, payload.employee_id)
    employee_id = employee["canonical_id"]
    movement = await _movement_for_employee(db, owner, movement_id)

    existing_event_id = str(movement.get("accounting_event_id") or "")
    if existing_event_id:
        prior = await db.mz2_employee_financial_events.find_one(
            {"_id": existing_event_id, "user_id": owner},
        )
        if prior and prior.get("kind") == payload.action and prior.get("employee_id") == employee_id:
            return {**_public_event(prior), "state": "already_posted"}
        raise HTTPException(409, "daily_movement_already_consumed")

    if movement.get("status") != "unclassified":
        raise HTTPException(409, detail={
            "code": "daily_movement_not_ready_for_employee",
            "status": movement.get("status"),
        })

    direction = movement.get("direction")
    expected_direction = "out" if payload.action in {"salary_payment", "advance_grant", "custody_grant"} else "in"
    if direction != expected_direction:
        raise HTTPException(409, detail={
            "code": "employee_movement_direction_mismatch",
            "expected": expected_direction,
            "actual": direction,
        })

    amount = _money(movement.get("amount"))
    accounting_dt = _movement_accounting_at(movement["movement_date"])
    accounting_at = await _require_post_cutover(db, owner, accounting_dt)
    bank_id = str(movement.get("bank_account_id") or "").strip()
    if not bank_id:
        raise HTTPException(409, "daily_movement_bank_missing")

    required = [("bank", bank_id, "main")]
    if payload.action == "salary_payment":
        required.extend([
            ("employee", employee_id, "salary_payable"),
            ("employee", employee_id, "advance"),
        ])
    elif payload.action in {"advance_grant", "advance_repayment"}:
        required.append(("employee", employee_id, "advance"))
    else:
        required.append(("employee", employee_id, "custody"))

    balances = await read_mz2_write_balances(db, owner=owner, required_accounts=required)
    bank_balance = balances.net_balance(entity_type="bank", entity_id=bank_id, sub_account="main")
    if expected_direction == "out" and amount > bank_balance:
        raise HTTPException(409, detail={
            "code": "insufficient_mz2_bank_balance",
            "available": format(max(bank_balance, Decimal(0)), ".2f"),
            "required": format(amount, ".2f"),
        })

    event_id = _hash([owner, "employee_movement", movement_id])
    entries: list[dict[str, Any]]
    detail: dict[str, str] = {}

    if payload.action == "salary_payment":
        payable = max(
            -balances.net_balance(
                entity_type="employee",
                entity_id=employee_id,
                sub_account="salary_payable",
            ),
            Decimal(0),
        )
        advance = max(
            balances.net_balance(
                entity_type="employee",
                entity_id=employee_id,
                sub_account="advance",
            ),
            Decimal(0),
        )
        if payable <= 0:
            raise HTTPException(409, "employee_salary_not_payable")
        if amount > payable:
            raise HTTPException(409, detail={
                "code": "salary_payment_exceeds_payable",
                "payable": format(payable, ".2f"),
                "bank_cash": format(amount, ".2f"),
            })
        offset = Decimal(0)
        if payload.apply_open_advances:
            offset = min(advance, max(payable - amount, Decimal(0)))
        total_settle = amount + offset
        entries = [
            {
                "entity_type": "employee",
                "entity_id": employee_id,
                "sub_account": "salary_payable",
                "side": "debit",
                "amount": float(total_settle),
                "entry_type": "salary_payment",
            },
            {
                "entity_type": "bank",
                "entity_id": bank_id,
                "sub_account": "main",
                "side": "credit",
                "amount": float(amount),
                "entry_type": "salary_payment",
            },
        ]
        if offset > 0:
            entries.append({
                "entity_type": "employee",
                "entity_id": employee_id,
                "sub_account": "advance",
                "side": "credit",
                "amount": float(offset),
                "entry_type": "advance_settle",
            })
        detail = {
            "cash_amount": format(amount, ".2f"),
            "advance_offset": format(offset, ".2f"),
            "salary_settled": format(total_settle, ".2f"),
        }
    elif payload.action == "advance_grant":
        entries = [
            {
                "entity_type": "employee", "entity_id": employee_id,
                "sub_account": "advance", "side": "debit",
                "amount": float(amount), "entry_type": "advance_grant",
            },
            {
                "entity_type": "bank", "entity_id": bank_id,
                "sub_account": "main", "side": "credit",
                "amount": float(amount), "entry_type": "advance_grant",
            },
        ]
    elif payload.action == "advance_repayment":
        advance = max(
            balances.net_balance(
                entity_type="employee", entity_id=employee_id, sub_account="advance"
            ),
            Decimal(0),
        )
        if amount > advance:
            raise HTTPException(409, detail={
                "code": "advance_repayment_exceeds_balance",
                "available": format(advance, ".2f"),
                "received": format(amount, ".2f"),
            })
        entries = [
            {
                "entity_type": "bank", "entity_id": bank_id,
                "sub_account": "main", "side": "debit",
                "amount": float(amount), "entry_type": "advance_repay_cash",
            },
            {
                "entity_type": "employee", "entity_id": employee_id,
                "sub_account": "advance", "side": "credit",
                "amount": float(amount), "entry_type": "advance_repay_cash",
            },
        ]
    elif payload.action == "custody_grant":
        entries = [
            {
                "entity_type": "employee", "entity_id": employee_id,
                "sub_account": "custody", "side": "debit",
                "amount": float(amount), "entry_type": "custody_grant",
            },
            {
                "entity_type": "bank", "entity_id": bank_id,
                "sub_account": "main", "side": "credit",
                "amount": float(amount), "entry_type": "custody_grant",
            },
        ]
    else:  # custody_return
        custody = max(
            balances.net_balance(
                entity_type="employee", entity_id=employee_id, sub_account="custody"
            ),
            Decimal(0),
        )
        if amount > custody:
            raise HTTPException(409, detail={
                "code": "custody_return_exceeds_balance",
                "available": format(custody, ".2f"),
                "received": format(amount, ".2f"),
            })
        entries = [
            {
                "entity_type": "bank", "entity_id": bank_id,
                "sub_account": "main", "side": "debit",
                "amount": float(amount), "entry_type": "custody_return",
            },
            {
                "entity_type": "employee", "entity_id": employee_id,
                "sub_account": "custody", "side": "credit",
                "amount": float(amount), "entry_type": "custody_return",
            },
        ]

    economic = {
        "movement_id": movement_id,
        "employee_id": employee_id,
        "kind": payload.action,
        "amount": format(amount, ".2f"),
        "accounting_at": accounting_at,
        "bank_account_id": bank_id,
        **detail,
    }
    economic_hash = _hash(economic)
    prior = await db.mz2_employee_financial_events.find_one(
        {"_id": event_id, "user_id": owner},
    )
    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise HTTPException(409, "employee_movement_accounting_conflict")
        return {**_public_event(prior), "state": "already_posted"}

    metadata = {
        "operation_id": OPERATION_ID,
        "source": PAYROLL_SOURCE,
        "payroll_event_id": event_id,
        "daily_movement_id": movement_id,
        "daily_movement_file_id": movement.get("file_id"),
        "bank_reference": movement.get("reference"),
        "employee_id": employee_id,
        "employee_name": employee.get("name") or "",
        "accounting_at": accounting_at,
        "reason": payload.reason,
        **detail,
    }
    result = await post_txn_group(
        db,
        user_id=owner,
        actor_id=actor["id"],
        actor_name=actor.get("name") or actor.get("email") or actor["id"],
        txn_type="mz2_employee_" + payload.action,
        notes=payload.reason,
        metadata=metadata,
        entries=entries,
    )
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "_id": event_id,
        "id": event_id,
        "user_id": owner,
        "kind": payload.action,
        "employee_id": employee_id,
        "employee_name": employee.get("name") or "",
        "movement_id": movement_id,
        "amount": format(amount, ".2f"),
        "accounting_at": accounting_at,
        "bank_account_id": bank_id,
        "bank_reference": movement.get("reference"),
        "economic_hash": economic_hash,
        "txn_group_id": result["txn_group_id"],
        "status": "posted",
        "reason": payload.reason,
        "posted_by": actor["id"],
        "posted_at": now,
        **detail,
    }
    await db.mz2_employee_financial_events.insert_one(event)
    changed = await db.mz2_daily_movements.update_one(
        {
            "_id": movement["_id"],
            "user_id": owner,
            "accounting_event_id": {"$in": [None, ""]},
        },
        {"$set": {
            "status": "accounting_posted",
            "accounting_event_id": event_id,
            "accounting_action": payload.action,
            "accounting_txn_group_id": result["txn_group_id"],
            "accounting_employee_id": employee_id,
            "accounting_reason": payload.reason,
            "consumed_at": now,
            "consumed_by": actor["id"],
        }},
    )
    if changed.modified_count != 1:
        raise HTTPException(409, "daily_movement_concurrent_consumption")
    await db.mz2_employee_financial_audit.insert_one({
        "user_id": owner,
        "action": "bank_movement_classified",
        "event_id": event_id,
        "movement_id": movement_id,
        "employee_id": employee_id,
        "kind": payload.action,
        "txn_group_id": result["txn_group_id"],
        "actor_id": actor["id"],
        "at": now,
        "reason": payload.reason,
    })
    return _public_event(event)


async def payroll_context(db, owner: str) -> dict[str, Any]:
    employees = await db.operating_salaries.find(
        {
            "user_id": owner,
            "category": "employee",
            "status": {"$ne": "inactive"},
            "archived": {"$ne": True},
            "is_archived": {"$ne": True},
            "deleted": {"$ne": True},
            "is_deleted": {"$ne": True},
        },
        {"_id": 0, "id": 1, "employee_id": 1, "name": 1, "monthly_amount": 1, "status": 1},
    ).sort("name", 1).to_list(500)
    scope = await read_mz2_ledger(db, owner=owner)
    nets: dict[tuple[str, str], Decimal] = {}
    if scope["status"] == "available":
        for row in scope["items"]:
            if row.get("entity_type") != "employee":
                continue
            key = (str(row.get("entity_id")), str(row.get("sub_account") or ""))
            amount = Decimal(str(row.get("amount") or 0))
            nets[key] = nets.get(key, Decimal(0)) + (amount if row["side"] == "debit" else -amount)
    result = []
    for employee in employees:
        employee_id = str(employee.get("id") or employee.get("employee_id") or "")
        result.append({
            "id": employee_id,
            "name": employee.get("name") or "",
            "monthly_amount": employee.get("monthly_amount") or 0,
            "salary_payable": float(max(-nets.get((employee_id, "salary_payable"), Decimal(0)), Decimal(0))),
            "advance": float(max(nets.get((employee_id, "advance"), Decimal(0)), Decimal(0))),
            "custody": float(max(nets.get((employee_id, "custody"), Decimal(0)), Decimal(0))),
        })
    pending_movements = await db.mz2_daily_movements.find(
        {
            "user_id": owner,
            "status": "unclassified",
            "explicit_provider": None,
            "suggested_provider": None,
        },
        {"_id": 0},
    ).sort([("movement_date", -1), ("created_at", -1)]).limit(300).to_list(300)
    return {
        "ledger_status": scope["status"],
        "ledger_reason": scope["reason"],
        "employees": result,
        "pending_movements": pending_movements,
    }


def install_employee_finance_routes(router, db, current_user) -> None:
    base = "/accounting-module/payroll"

    @router.get(base + "/context")
    async def context(user: dict = Depends(current_user)):
        _, owner = await _actor_scope(db, user, "accounting.payroll.view")
        return await payroll_context(db, owner)

    @router.post(base + "/accrue")
    async def accrue(payload: PayrollAccrualIn, user: dict = Depends(current_user)):
        actor, owner = await _actor_scope(db, user, "accounting.payroll.post")
        return await accrue_payroll_period(
            db,
            owner=owner,
            actor=actor,
            payload=payload,
        )

    @router.post(base + "/movements/{movement_id}/classify")
    async def classify(
        movement_id: str,
        payload: MovementClassifyIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await _actor_scope(db, user, "accounting.payroll.post")
        return await classify_employee_movement(
            db,
            owner=owner,
            actor=actor,
            movement_id=movement_id,
            payload=payload,
        )
