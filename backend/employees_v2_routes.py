"""Mezan Employees V2 foundation and guarded shadow migration.

The legacy salary engine continues to own payroll until a later, separately
validated cutover.  This module creates the employee identity that Mezan V2
needs and links it to the existing salary, login, operational-role and ledger
records without rewriting any of them.

Migration modes
---------------
preview
    Read-only projection.  No collection is modified.
shadow_read_only
    Creates idempotent employee and salary-contract snapshots in V2.  Legacy
    payroll and the general ledger remain authoritative and writable; the V2
    records are deliberately read-only until cutover acceptance criteria pass.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

EMPLOYEES = "mezan_employees_v2"
SALARY_CONTRACTS = "mezan_employee_salary_contracts_v2"
EMPLOYEE_EVENTS = "mezan_employee_events_v2"
MIGRATION_RUNS = "mezan_employee_migration_runs_v2"
ROLE_ASSIGNMENTS = "mezan_role_assignments_v2"
APPLY_CONFIRMATION = "MIGRATE_EMPLOYEES_V2_SHADOW"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_name(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(_text(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _source_fingerprint(legacy_rows: list[dict[str, Any]]) -> str:
    material = [
        {
            "id": _text(row.get("id")),
            "name": _text(row.get("name")),
            "country": _text(row.get("country") or "saudi"),
            "monthly_amount": _money(row.get("monthly_amount")),
            "start_date": _text(row.get("start_date")),
            "stopped_at": _text(row.get("stopped_at")),
            "status": _text(row.get("status") or "active"),
            "account_user_id": _text(
                row.get("account_user_id")
                or row.get("linked_user_id")
                or row.get("user_account_id")
            ),
        }
        for row in legacy_rows
    ]
    material.sort(key=lambda row: (row["id"], row["name"]))
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _financial_balances(
    ledger_rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Compute the three employee sub-accounts using Ledger SSOT rules."""
    output: dict[str, dict[str, float]] = {}
    for row in ledger_rows:
        employee_id = _text(row.get("employee_id") or row.get("entity_id"))
        sub_account = _text(row.get("sub_account"))
        side = _text(row.get("side"))
        if not employee_id or sub_account not in {"salary_payable", "advance", "custody"}:
            continue
        amount = _money(row.get("total") if "total" in row else row.get("amount"))
        bucket = output.setdefault(
            employee_id,
            {"salary_payable": 0.0, "advance": 0.0, "custody": 0.0},
        )
        if sub_account == "salary_payable":
            bucket[sub_account] += amount if side == "credit" else -amount
        else:
            bucket[sub_account] += amount if side == "debit" else -amount
    for bucket in output.values():
        for key in bucket:
            bucket[key] = _money(bucket[key])
    return output


def _account_resolution(
    legacy: dict[str, Any],
    team_users: list[dict[str, Any]],
) -> dict[str, Any]:
    legacy_id = _text(legacy.get("id"))
    users_by_id = {
        _text(row.get("id")): row for row in team_users if _text(row.get("id"))
    }
    explicit_id = _text(
        legacy.get("account_user_id")
        or legacy.get("linked_user_id")
        or legacy.get("user_account_id")
    )
    reverse_links = [
        row for row in team_users
        if _text(row.get("linked_employee_id")) == legacy_id
    ]

    exact_candidates: list[tuple[str, dict[str, Any]]] = []
    if explicit_id and explicit_id in users_by_id:
        exact_candidates.append(("legacy_explicit_link", users_by_id[explicit_id]))
    exact_candidates.extend(("account_reverse_link", row) for row in reverse_links)
    unique_exact = {
        _text(row.get("id")): (method, row)
        for method, row in exact_candidates
        if _text(row.get("id"))
    }
    if len(unique_exact) == 1:
        method, account = next(iter(unique_exact.values()))
        return {
            "status": "linked",
            "method": method,
            "account_user_id": _text(account.get("id")),
            "account_name": _text(account.get("name")),
            "account_email": _text(account.get("email")),
            "suggested_account": None,
        }
    if len(unique_exact) > 1:
        return {
            "status": "conflict",
            "method": "multiple_exact_links",
            "account_user_id": None,
            "account_name": None,
            "account_email": None,
            "suggested_account": None,
        }
    if explicit_id and explicit_id not in users_by_id:
        return {
            "status": "review_required",
            "method": "stale_explicit_link",
            "account_user_id": None,
            "account_name": None,
            "account_email": None,
            "suggested_account": None,
        }

    normalized_name = _normalized_name(legacy.get("name"))
    name_matches = [
        row for row in team_users
        if normalized_name and _normalized_name(row.get("name")) == normalized_name
    ]
    if len(name_matches) == 1:
        suggestion = name_matches[0]
        return {
            "status": "review_required",
            "method": "unique_name_suggestion",
            "account_user_id": None,
            "account_name": None,
            "account_email": None,
            "suggested_account": {
                "id": _text(suggestion.get("id")),
                "name": _text(suggestion.get("name")),
                "email": _text(suggestion.get("email")),
            },
        }
    if len(name_matches) > 1:
        return {
            "status": "review_required",
            "method": "ambiguous_name",
            "account_user_id": None,
            "account_name": None,
            "account_email": None,
            "suggested_account": None,
        }
    return {
        "status": "not_required",
        "method": "no_login_account",
        "account_user_id": None,
        "account_name": None,
        "account_email": None,
        "suggested_account": None,
    }


def _assignment_permissions(assignment: dict[str, Any] | None) -> list[str]:
    """Read the access-control service's persisted permission snapshot.

    The access-control owner writes ``effective_permissions`` on every role
    assignment.  Employees V2 deliberately does not duplicate that role
    catalogue or import product infrastructure merely to render a preview.
    """
    if not assignment or assignment.get("enabled", True) is False:
        return []
    return sorted({
        _text(value)
        for value in assignment.get("effective_permissions") or []
        if _text(value)
    })


def build_employee_migration_preview(
    *,
    owner_id: str,
    legacy_rows: list[dict[str, Any]],
    team_users: list[dict[str, Any]],
    role_assignments: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    existing_employees: list[dict[str, Any]],
    existing_contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic, side-effect-free employee migration report."""
    owner_id = _text(owner_id)
    assignments_by_user = {
        _text(row.get("user_id")): row
        for row in role_assignments
        if _text(row.get("user_id"))
    }
    existing_by_legacy = {
        _text(row.get("legacy_employee_id")): row
        for row in existing_employees
        if _text(row.get("legacy_employee_id"))
    }
    contracts_by_legacy = {
        _text(row.get("legacy_salary_id")): row
        for row in existing_contracts
        if _text(row.get("legacy_salary_id"))
    }
    balances = _financial_balances(ledger_rows)
    source_id_counts: dict[str, int] = {}
    for row in legacy_rows:
        legacy_id = _text(row.get("id"))
        source_id_counts[legacy_id] = source_id_counts.get(legacy_id, 0) + 1

    employees: list[dict[str, Any]] = []
    warnings = 0
    blocking_issues = 0
    for legacy in sorted(
        legacy_rows,
        key=lambda row: (
            _text(row.get("status") or "active") != "active",
            _normalized_name(row.get("name")),
            _text(row.get("id")),
        ),
    ):
        legacy_id = _text(legacy.get("id"))
        employee_id = _stable_id("empv2", owner_id, legacy_id)
        contract_id = _stable_id(
            "empct",
            owner_id,
            legacy_id,
            _text(legacy.get("start_date")),
            _money(legacy.get("monthly_amount")),
        )
        account = _account_resolution(legacy, team_users)
        existing = existing_by_legacy.get(legacy_id)
        existing_contract = contracts_by_legacy.get(legacy_id)
        duplicate_source_id = not legacy_id or source_id_counts.get(legacy_id, 0) > 1
        salary_drift = bool(
            existing_contract
            and _money(existing_contract.get("monthly_amount"))
            != _money(legacy.get("monthly_amount"))
        )
        row_blockers: list[str] = []
        row_warnings: list[str] = []
        if duplicate_source_id:
            row_blockers.append("duplicate_or_missing_legacy_employee_id")
        if account["status"] == "conflict":
            row_blockers.append("conflicting_account_links")
        elif account["status"] == "review_required":
            row_warnings.append("account_link_review_required")
        elif account["status"] == "not_required":
            row_warnings.append("login_account_not_created")
        if salary_drift:
            row_warnings.append("legacy_salary_changed_after_shadow_migration")

        assignment = assignments_by_user.get(_text(account.get("account_user_id")))
        permissions = _assignment_permissions(assignment)
        financial = balances.get(
            legacy_id,
            {"salary_payable": 0.0, "advance": 0.0, "custody": 0.0},
        )
        row_status = (
            "blocked" if row_blockers
            else "already_migrated" if existing
            else "ready"
        )
        employees.append({
            "employee_id": _text((existing or {}).get("id")) or employee_id,
            "legacy_employee_id": legacy_id,
            "financial_entity_id": legacy_id,
            "name": _text(legacy.get("name")),
            "status": _text(legacy.get("status") or "active"),
            "country": _text(legacy.get("country") or "saudi"),
            "salary_contract": {
                "id": _text((existing_contract or {}).get("id")) or contract_id,
                "monthly_amount": _money(legacy.get("monthly_amount")),
                "currency": "SAR",
                "effective_from": _text(legacy.get("start_date")) or None,
                "effective_to": _text(legacy.get("stopped_at")) or None,
                "status": "active" if _text(legacy.get("status") or "active") == "active" else "ended",
                "shadow_exists": bool(existing_contract),
            },
            "account": account,
            "operational_role": {
                "role_key": _text((assignment or {}).get("role_key")) or None,
                "enabled": bool((assignment or {}).get("enabled", True)) if assignment else False,
                "effective_permissions": permissions,
                "warehouse_ids": list((assignment or {}).get("warehouse_ids") or []),
                "fulfillment_responsibilities": list(
                    (assignment or {}).get("fulfillment_responsibilities") or []
                ),
            },
            "financial_snapshot": financial,
            "migration_status": row_status,
            "blockers": row_blockers,
            "warnings": row_warnings,
            "shadow_exists": bool(existing),
        })
        blocking_issues += len(row_blockers)
        warnings += len(row_warnings)

    active = sum(row["status"] == "active" for row in employees)
    stopped = len(employees) - active
    linked_accounts = sum(row["account"]["status"] == "linked" for row in employees)
    already_migrated = sum(row["shadow_exists"] for row in employees)
    monthly_total = _money(sum(row["salary_contract"]["monthly_amount"] for row in employees if row["status"] == "active"))
    return {
        "ok": True,
        "mode": "preview",
        "writes_made": False,
        "legacy_payroll_authoritative": True,
        "ledger_authoritative": True,
        "source_fingerprint": _source_fingerprint(legacy_rows),
        "generated_at": _now(),
        "summary": {
            "legacy_employees": len(employees),
            "active_employees": active,
            "stopped_employees": stopped,
            "active_monthly_salary_total": monthly_total,
            "linked_login_accounts": linked_accounts,
            "accounts_needing_review": sum(
                row["account"]["status"] == "review_required" for row in employees
            ),
            "employees_without_login": sum(
                row["account"]["status"] == "not_required" for row in employees
            ),
            "already_migrated": already_migrated,
            "ready_to_create": sum(
                row["migration_status"] == "ready" for row in employees
            ),
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "salary_payable_total": _money(sum(row["financial_snapshot"]["salary_payable"] for row in employees)),
            "advance_total": _money(sum(row["financial_snapshot"]["advance"] for row in employees)),
            "custody_total": _money(sum(row["financial_snapshot"]["custody"] for row in employees)),
        },
        "safety": {
            "operating_salaries_writes": False,
            "general_ledger_writes": False,
            "liability_writes": False,
            "user_account_writes": False,
            "role_assignment_writes": False,
            "historical_recompute": False,
            "apply_mode": "shadow_read_only",
        },
        "employees": employees,
    }


async def ensure_employee_v2_indexes(db: Any) -> None:
    await db[EMPLOYEES].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)],
        unique=True,
        name="uq_mezan_employee_v2_id",
    )
    await db[EMPLOYEES].create_index(
        [("user_id", ASCENDING), ("legacy_employee_id", ASCENDING)],
        unique=True,
        name="uq_mezan_employee_v2_legacy",
    )
    await db[EMPLOYEES].create_index(
        [("user_id", ASCENDING), ("status", ASCENDING), ("display_name", ASCENDING)],
        name="ix_mezan_employee_v2_status",
    )
    await db[SALARY_CONTRACTS].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)],
        unique=True,
        name="uq_mezan_employee_contract_v2_id",
    )
    await db[SALARY_CONTRACTS].create_index(
        [("user_id", ASCENDING), ("legacy_salary_id", ASCENDING)],
        unique=True,
        name="uq_mezan_employee_contract_v2_legacy",
    )
    await db[EMPLOYEE_EVENTS].create_index(
        [("user_id", ASCENDING), ("event_key", ASCENDING)],
        unique=True,
        name="uq_mezan_employee_event_v2_key",
    )
    await db[EMPLOYEE_EVENTS].create_index(
        [("user_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_mezan_employee_event_v2_time",
    )
    await db[MIGRATION_RUNS].create_index(
        [("user_id", ASCENDING), ("source_fingerprint", ASCENDING)],
        unique=True,
        name="uq_mezan_employee_migration_v2_source",
    )


def _require_owner(user: dict[str, Any]) -> None:
    if _text(user.get("role")).casefold() != "owner" and user.get("is_owner") is not True:
        raise HTTPException(status_code=403, detail={"code": "owner_required"})


async def _preview_from_db(db: Any, owner_id: str) -> dict[str, Any]:
    legacy_rows = await db.operating_salaries.find(
        {"user_id": owner_id, "category": "employee"},
        {"_id": 0},
    ).sort([("status", 1), ("name", 1)]).to_list(5000)
    team_users = await db.users.find(
        {"$or": [{"id": owner_id}, {"created_by": owner_id}]},
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "email": 1,
            "role": 1,
            "created_by": 1,
            "linked_employee_id": 1,
            "disabled": 1,
            "is_active": 1,
            "deleted_at": 1,
        },
    ).to_list(5000)
    active_team_users = [
        row for row in team_users
        if row.get("disabled") is not True
        and row.get("is_active") is not False
        and not row.get("deleted_at")
    ]
    team_ids = [_text(row.get("id")) for row in active_team_users if _text(row.get("id"))]
    role_assignments = await db[ROLE_ASSIGNMENTS].find(
        {"user_id": {"$in": team_ids}},
        {"_id": 0},
    ).to_list(5000)
    legacy_ids = [_text(row.get("id")) for row in legacy_rows if _text(row.get("id"))]
    ledger_pipeline = [
        {"$match": {
            "user_id": owner_id,
            "entity_type": "employee",
            "entity_id": {"$in": legacy_ids},
            "sub_account": {"$in": ["salary_payable", "advance", "custody"]},
            "status": "posted",
            "entry_type": {"$ne": "reversal"},
            "metadata.legacy_orphan": {"$ne": True},
        }},
        {"$group": {
            "_id": {
                "employee_id": "$entity_id",
                "sub_account": "$sub_account",
                "side": "$side",
            },
            "total": {"$sum": "$amount"},
        }},
    ]
    grouped_ledger = await db.general_ledger.aggregate(ledger_pipeline).to_list(50000)
    ledger_rows = [
        {
            "employee_id": _text((row.get("_id") or {}).get("employee_id")),
            "sub_account": _text((row.get("_id") or {}).get("sub_account")),
            "side": _text((row.get("_id") or {}).get("side")),
            "total": _money(row.get("total")),
        }
        for row in grouped_ledger
    ]
    existing_employees = await db[EMPLOYEES].find(
        {"user_id": owner_id}, {"_id": 0}
    ).to_list(5000)
    existing_contracts = await db[SALARY_CONTRACTS].find(
        {"user_id": owner_id}, {"_id": 0}
    ).to_list(10000)
    return build_employee_migration_preview(
        owner_id=owner_id,
        legacy_rows=legacy_rows,
        team_users=active_team_users,
        role_assignments=role_assignments,
        ledger_rows=ledger_rows,
        existing_employees=existing_employees,
        existing_contracts=existing_contracts,
    )


def make_employees_v2_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/employees-v2", tags=["Employees V2"])

    @router.get("")
    async def list_employees(user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_owner(user)
        owner_id = _text(user.get("id"))
        preview = await _preview_from_db(db, owner_id)
        return {
            **preview,
            "mode": "shadow_read_only" if preview["summary"]["already_migrated"] else "preview",
        }

    @router.get("/migration/preview")
    async def migration_preview(user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_owner(user)
        return await _preview_from_db(db, _text(user.get("id")))

    @router.post("/migration/apply-shadow")
    async def apply_shadow_migration(
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != APPLY_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_shadow_migration_confirmation_required"},
            )
        owner_id = _text(user.get("id"))
        await ensure_employee_v2_indexes(db)
        preview = await _preview_from_db(db, owner_id)
        if preview["summary"]["blocking_issues"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "employee_shadow_migration_blocked",
                    "blocking_issues": preview["summary"]["blocking_issues"],
                },
            )
        existing_run = await db[MIGRATION_RUNS].find_one(
            {
                "user_id": owner_id,
                "source_fingerprint": preview["source_fingerprint"],
            },
            {"_id": 0},
        )
        if existing_run:
            refreshed = await _preview_from_db(db, owner_id)
            return {
                "ok": True,
                "idempotent_replay": True,
                "run": existing_run,
                "preview": refreshed,
            }

        run_id = f"empmig_{uuid.uuid4().hex}"
        now = _now()
        inserted_employees = 0
        inserted_contracts = 0
        for row in preview["employees"]:
            account = row["account"]
            employee_doc = {
                "id": row["employee_id"],
                "user_id": owner_id,
                "legacy_employee_id": row["legacy_employee_id"],
                "financial_entity_id": row["financial_entity_id"],
                "display_name": row["name"],
                "status": row["status"],
                "country": row["country"],
                "department": None,
                "job_title": None,
                "manager_employee_id": None,
                "account_user_id": account.get("account_user_id"),
                "account_link_status": account.get("status"),
                "source": {
                    "system": "mezan_legacy",
                    "collection": "operating_salaries",
                    "record_id": row["legacy_employee_id"],
                },
                "migration": {
                    "mode": "shadow_read_only",
                    "run_id": run_id,
                    "source_fingerprint": preview["source_fingerprint"],
                    "migrated_at": now,
                },
                "created_at": now,
                "updated_at": now,
            }
            result = await db[EMPLOYEES].update_one(
                {
                    "user_id": owner_id,
                    "legacy_employee_id": row["legacy_employee_id"],
                },
                {"$setOnInsert": employee_doc},
                upsert=True,
            )
            inserted_employees += int(result.upserted_id is not None)

            salary = row["salary_contract"]
            contract_doc = {
                "id": salary["id"],
                "user_id": owner_id,
                "employee_id": row["employee_id"],
                "legacy_salary_id": row["legacy_employee_id"],
                "contract_type": "monthly",
                "monthly_amount": salary["monthly_amount"],
                "currency": "SAR",
                "effective_from": salary["effective_from"],
                "effective_to": salary["effective_to"],
                "status": salary["status"],
                "accrual_policy": "legacy_calendar_daily_compatible",
                "source_authority": "operating_salaries_until_cutover",
                "migration": {
                    "mode": "shadow_read_only",
                    "run_id": run_id,
                    "source_fingerprint": preview["source_fingerprint"],
                    "migrated_at": now,
                },
                "created_at": now,
                "updated_at": now,
            }
            contract_result = await db[SALARY_CONTRACTS].update_one(
                {
                    "user_id": owner_id,
                    "legacy_salary_id": row["legacy_employee_id"],
                },
                {"$setOnInsert": contract_doc},
                upsert=True,
            )
            inserted_contracts += int(contract_result.upserted_id is not None)

            event_key = f"employee_shadow_migrated:{preview['source_fingerprint']}:{row['legacy_employee_id']}"
            await db[EMPLOYEE_EVENTS].update_one(
                {"user_id": owner_id, "event_key": event_key},
                {"$setOnInsert": {
                    "id": f"empevt_{uuid.uuid4().hex}",
                    "event_key": event_key,
                    "user_id": owner_id,
                    "employee_id": row["employee_id"],
                    "event_type": "employee_shadow_migrated",
                    "actor_type": "human",
                    "actor_id": _text(user.get("id")),
                    "actor_name": _text(user.get("name") or user.get("email")),
                    "migration_run_id": run_id,
                    "occurred_at": now,
                }},
                upsert=True,
            )

        run = {
            "id": run_id,
            "user_id": owner_id,
            "mode": "shadow_read_only",
            "status": "completed",
            "source_fingerprint": preview["source_fingerprint"],
            "source_counts": {
                "employees": preview["summary"]["legacy_employees"],
                "active": preview["summary"]["active_employees"],
                "stopped": preview["summary"]["stopped_employees"],
            },
            "result_counts": {
                "employees_inserted": inserted_employees,
                "contracts_inserted": inserted_contracts,
            },
            "financial_snapshot": {
                "active_monthly_salary_total": preview["summary"]["active_monthly_salary_total"],
                "salary_payable_total": preview["summary"]["salary_payable_total"],
                "advance_total": preview["summary"]["advance_total"],
                "custody_total": preview["summary"]["custody_total"],
            },
            "safety": preview["safety"],
            "created_at": now,
            "created_by": _text(user.get("id")),
        }
        try:
            await db[MIGRATION_RUNS].insert_one(run)
            run.pop("_id", None)
        except DuplicateKeyError:
            # A second owner request may finish the same fingerprint while
            # this one is upserting its idempotent employee rows.  Return the
            # completed run instead of surfacing a false migration failure.
            run = await db[MIGRATION_RUNS].find_one(
                {
                    "user_id": owner_id,
                    "source_fingerprint": preview["source_fingerprint"],
                },
                {"_id": 0},
            ) or run
        refreshed = await _preview_from_db(db, owner_id)
        return {
            "ok": True,
            "idempotent_replay": False,
            "run": run,
            "preview": refreshed,
        }

    return router


__all__ = [
    "APPLY_CONFIRMATION",
    "EMPLOYEES",
    "SALARY_CONTRACTS",
    "build_employee_migration_preview",
    "ensure_employee_v2_indexes",
    "make_employees_v2_router",
]
