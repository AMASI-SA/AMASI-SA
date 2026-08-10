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
from datetime import date, datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from ai_store_access_contract import (
    PERMISSIONS,
    ROLE_CATALOG,
    ROLE_LABELS,
    effective_permissions,
    validate_assignment,
)
from warehouse_location_routes import WAREHOUSES

EMPLOYEES = "mezan_employees_v2"
SALARY_CONTRACTS = "mezan_employee_salary_contracts_v2"
EMPLOYEE_EVENTS = "mezan_employee_events_v2"
MIGRATION_RUNS = "mezan_employee_migration_runs_v2"
ROLE_ASSIGNMENTS = "mezan_role_assignments_v2"
APPLY_CONFIRMATION = "MIGRATE_EMPLOYEES_V2_SHADOW"
PILOT_SOURCE_SYSTEM = "mezan_employees_v2_pilot"
PILOT_CREATE_CONFIRMATION = "CREATE_EMPLOYEE_V2_PILOT"
PILOT_ACCOUNT_LINK_CONFIRMATION = "LINK_EMPLOYEE_V2_PILOT_ACCOUNT"
PILOT_ACCOUNT_UNLINK_CONFIRMATION = "UNLINK_EMPLOYEE_V2_PILOT_ACCOUNT"
PILOT_ROLE_ASSIGNMENT_CONFIRMATION = "ASSIGN_EMPLOYEE_V2_PILOT_ROLE"
PILOT_EMPLOYEE_LIMIT = 1
PILOT_STATUSES = {"draft", "inactive"}


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


def _optional_text(value: Any, *, maximum: int, field: str) -> str | None:
    normalized = _text(value)
    if len(normalized) > maximum:
        raise ValueError(f"{field}_too_long")
    return normalized or None


def _iso_date(value: Any, *, field: str) -> str | None:
    normalized = _text(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field}_invalid") from exc


def normalize_pilot_employee_payload(
    payload: dict[str, Any],
    *,
    partial: bool = False,
) -> dict[str, Any]:
    """Validate the deliberately narrow employee-management pilot contract.

    Pilot employee records are never payroll-active.  Salary is stored only in
    the V2 pilot contract and is not written to ``operating_salaries`` or the
    general ledger.
    """
    normalized: dict[str, Any] = {}
    if not partial or "name" in payload:
        name = _text(payload.get("name"))
        if not name:
            raise ValueError("employee_name_required")
        if len(name) > 80:
            raise ValueError("employee_name_too_long")
        normalized["display_name"] = name

    text_fields = {
        "phone": (40, "employee_phone"),
        "contact_email": (254, "employee_contact_email"),
        "job_title": (120, "employee_job_title"),
        "department": (120, "employee_department"),
        "notes": (1000, "employee_notes"),
    }
    for key, (maximum, error_field) in text_fields.items():
        if not partial or key in payload:
            value = _optional_text(
                payload.get(key),
                maximum=maximum,
                field=error_field,
            )
            if key == "contact_email" and value:
                value = value.casefold()
                if "@" not in value or value.startswith("@") or value.endswith("@"):
                    raise ValueError("employee_contact_email_invalid")
            normalized[key] = value

    if not partial or "hire_date" in payload:
        normalized["hire_date"] = _iso_date(
            payload.get("hire_date"),
            field="employee_hire_date",
        )

    if not partial or "status" in payload:
        status = _text(payload.get("status") or "draft").casefold()
        if status not in PILOT_STATUSES:
            raise ValueError("employee_pilot_status_invalid")
        normalized["status"] = status

    if not partial or "monthly_salary" in payload:
        raw_amount = payload.get("monthly_salary")
        try:
            amount = float(raw_amount or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("employee_monthly_salary_invalid") from exc
        if amount < 0 or amount > 10_000_000:
            raise ValueError("employee_monthly_salary_invalid")
        normalized["monthly_salary"] = _money(amount)

    if partial and not normalized:
        raise ValueError("employee_update_empty")
    return normalized


def _is_pilot_employee(employee: dict[str, Any] | None) -> bool:
    return bool(
        employee
        and (employee.get("source") or {}).get("system") == PILOT_SOURCE_SYSTEM
        and (employee.get("management") or {}).get("mode") == "pilot_only"
    )


def _require_pilot_employee(employee: dict[str, Any] | None) -> dict[str, Any]:
    if not employee:
        raise HTTPException(
            status_code=404,
            detail={"code": "employee_v2_not_found"},
        )
    if not _is_pilot_employee(employee):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "employee_management_pilot_only",
                "migrated_employee_writes_enabled": False,
            },
        )
    return employee


def _employee_audit_view(employee: dict[str, Any] | None) -> dict[str, Any] | None:
    if not employee:
        return None
    return {
        "id": employee.get("id"),
        "display_name": employee.get("display_name"),
        "phone": employee.get("phone"),
        "contact_email": employee.get("contact_email"),
        "job_title": employee.get("job_title"),
        "department": employee.get("department"),
        "hire_date": employee.get("hire_date"),
        "status": employee.get("status"),
        "notes": employee.get("notes"),
        "account_user_id": employee.get("account_user_id"),
        "account_link_status": employee.get("account_link_status"),
        "version": employee.get("version"),
    }


def _reserved_review_account_ids(
    preview_employees: list[dict[str, Any]],
) -> set[str]:
    """Protect suggested migrated-employee links (including Arafat) in pilot."""
    return {
        _text((row.get("account") or {}).get("suggested_account", {}).get("id"))
        for row in preview_employees
        if (row.get("account") or {}).get("status") == "review_required"
        and _text((row.get("account") or {}).get("suggested_account", {}).get("id"))
    }


def build_employee_management_snapshot(
    *,
    pilot_employees: list[dict[str, Any]],
    salary_contracts: list[dict[str, Any]],
    team_users: list[dict[str, Any]],
    all_employee_links: list[dict[str, Any]],
    role_assignments: list[dict[str, Any]],
    preview_employees: list[dict[str, Any]],
    latest_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the pilot workspace without granting writes to migrated rows."""
    contracts_by_employee = {
        _text(row.get("employee_id")): row
        for row in salary_contracts
        if _text(row.get("employee_id"))
    }
    assignments_by_user = {
        _text(row.get("user_id")): row
        for row in role_assignments
        if _text(row.get("user_id"))
    }
    events_by_employee: dict[str, dict[str, Any]] = {}
    for row in latest_events:
        employee_id = _text(row.get("employee_id"))
        if employee_id and employee_id not in events_by_employee:
            events_by_employee[employee_id] = row

    rows: list[dict[str, Any]] = []
    for employee in pilot_employees:
        employee_id = _text(employee.get("id"))
        account_user_id = _text(employee.get("account_user_id"))
        assignment = assignments_by_user.get(account_user_id)
        rows.append({
            "id": employee_id,
            "name": _text(employee.get("display_name")),
            "phone": employee.get("phone"),
            "contact_email": employee.get("contact_email"),
            "job_title": employee.get("job_title"),
            "department": employee.get("department"),
            "hire_date": employee.get("hire_date"),
            "status": employee.get("status") or "draft",
            "notes": employee.get("notes"),
            "version": int(employee.get("version") or 1),
            "management_mode": "pilot_only",
            "payroll_enabled": False,
            "salary_contract": contracts_by_employee.get(employee_id),
            "account": {
                "status": employee.get("account_link_status") or "not_linked",
                "user_id": account_user_id or None,
                "name": (employee.get("account_link") or {}).get("account_name"),
                "email": (employee.get("account_link") or {}).get("account_email"),
                "effect_scope": "employees_v2_pilot_only",
            },
            "operational_role": {
                "role_key": (assignment or {}).get("role_key"),
                "enabled": bool((assignment or {}).get("enabled", False)),
                "effective_permissions": effective_permissions(assignment),
                "warehouse_ids": list((assignment or {}).get("warehouse_ids") or []),
                "workplace_warehouse_id": (assignment or {}).get("workplace_warehouse_id"),
                "fulfillment_responsibilities": list(
                    (assignment or {}).get("fulfillment_responsibilities") or []
                ),
            },
            "latest_event": events_by_employee.get(employee_id),
            "created_at": employee.get("created_at"),
            "updated_at": employee.get("updated_at"),
        })

    used_account_ids = {
        _text(row.get("account_user_id"))
        for row in all_employee_links
        if _text(row.get("account_user_id"))
    }
    reserved_account_ids = _reserved_review_account_ids(preview_employees)
    pilot_employee_ids = {_text(row.get("id")) for row in pilot_employees}
    assigned_account_ids = {
        account_id
        for account_id, assignment in assignments_by_user.items()
        if not (
            assignment.get("assignment_scope") == "employee_pilot"
            and assignment.get("enabled") is False
            and _text(assignment.get("employee_v2_id"))
            in pilot_employee_ids
        )
    }
    candidates = []
    for account in team_users:
        account_id = _text(account.get("id"))
        if (
            not account_id
            or _text(account.get("role")).casefold() == "owner"
            or account.get("is_owner") is True
            or account.get("disabled") is True
            or account.get("is_active") is False
            or account.get("deleted_at")
            or account_id in used_account_ids
            or account_id in reserved_account_ids
            or account_id in assigned_account_ids
        ):
            continue
        candidates.append({
            "id": account_id,
            "name": _text(account.get("name")),
            "email": _text(account.get("email")),
            "account_role": _text(account.get("role") or "viewer"),
        })

    rows.sort(key=lambda row: (row.get("created_at") or "", row["id"]), reverse=True)
    candidates.sort(key=lambda row: (row["name"].casefold(), row["email"].casefold()))
    return {
        "rollout_mode": "pilot_only",
        "pilot_limit": PILOT_EMPLOYEE_LIMIT,
        "pilot_count": len(rows),
        "can_create_pilot": len(rows) < PILOT_EMPLOYEE_LIMIT,
        "migrated_employee_writes_enabled": False,
        "legacy_payroll_writes_enabled": False,
        "general_ledger_writes_enabled": False,
        "account_link_effect_scope": "employees_v2_pilot_only",
        "role_assignment_scope": "linked_pilot_account_only",
        "reserved_review_accounts": len(reserved_account_ids),
        "accounts_with_existing_roles_excluded": len(assigned_account_ids),
        "employees": rows,
        "login_account_candidates": candidates,
        "role_catalog": ROLE_CATALOG,
        "role_labels": ROLE_LABELS,
        "permissions": sorted(PERMISSIONS),
    }


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
    await db[EMPLOYEES].create_index(
        [("user_id", ASCENDING), ("source.system", ASCENDING), ("created_at", DESCENDING)],
        name="ix_mezan_employee_v2_management_source",
    )
    await db[EMPLOYEES].create_index(
        [("user_id", ASCENDING), ("account_user_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"account_user_id": {"$type": "string"}},
        name="uq_mezan_employee_v2_account_link",
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


async def _record_employee_event(
    db: Any,
    *,
    owner_id: str,
    employee_id: str,
    event_type: str,
    actor: dict[str, Any],
    before: Any = None,
    after: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_id = f"empevt_{uuid.uuid4().hex}"
    event = {
        "id": event_id,
        "event_key": event_id,
        "user_id": owner_id,
        "employee_id": employee_id,
        "event_type": event_type,
        "actor_type": "human",
        "actor_id": _text(actor.get("id")),
        "actor_name": _text(actor.get("name") or actor.get("email")),
        "before": before,
        "after": after,
        "metadata": metadata or {},
        "occurred_at": _now(),
    }
    await db[EMPLOYEE_EVENTS].insert_one(event)
    event.pop("_id", None)
    return event


async def _management_from_db(
    db: Any,
    *,
    owner_id: str,
    preview: dict[str, Any],
) -> dict[str, Any]:
    pilot_employees = await db[EMPLOYEES].find(
        {
            "user_id": owner_id,
            "source.system": PILOT_SOURCE_SYSTEM,
            "management.mode": "pilot_only",
        },
        {"_id": 0},
    ).sort("created_at", -1).to_list(PILOT_EMPLOYEE_LIMIT + 10)
    pilot_ids = [_text(row.get("id")) for row in pilot_employees if _text(row.get("id"))]
    salary_contracts = await db[SALARY_CONTRACTS].find(
        {
            "user_id": owner_id,
            "employee_id": {"$in": pilot_ids},
            "source_authority": "employees_v2_pilot_only",
        },
        {"_id": 0},
    ).to_list(max(len(pilot_ids), 1))
    all_employee_links = await db[EMPLOYEES].find(
        {"user_id": owner_id, "account_user_id": {"$type": "string"}},
        {"_id": 0, "id": 1, "account_user_id": 1},
    ).to_list(5000)
    team_users = await db.users.find(
        {"created_by": owner_id},
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "email": 1,
            "role": 1,
            "disabled": 1,
            "is_active": 1,
            "deleted_at": 1,
        },
    ).to_list(5000)
    account_ids = [_text(row.get("id")) for row in team_users if _text(row.get("id"))]
    role_assignments = await db[ROLE_ASSIGNMENTS].find(
        {"user_id": {"$in": account_ids}},
        {"_id": 0},
    ).to_list(5000)
    latest_events = await db[EMPLOYEE_EVENTS].find(
        {"user_id": owner_id, "employee_id": {"$in": pilot_ids}},
        {"_id": 0},
    ).sort("occurred_at", -1).to_list(500)
    return build_employee_management_snapshot(
        pilot_employees=pilot_employees,
        salary_contracts=salary_contracts,
        team_users=team_users,
        all_employee_links=all_employee_links,
        role_assignments=role_assignments,
        preview_employees=preview.get("employees") or [],
        latest_events=latest_events,
    )


async def _employee_management_response(
    db: Any,
    *,
    owner_id: str,
) -> dict[str, Any]:
    preview = await _preview_from_db(db, owner_id)
    management = await _management_from_db(
        db,
        owner_id=owner_id,
        preview=preview,
    )
    return {
        **preview,
        "mode": "pilot_management",
        "management": management,
    }


def make_employees_v2_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/employees-v2", tags=["Employees V2"])

    @router.get("")
    async def list_employees(user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_owner(user)
        owner_id = _text(user.get("id"))
        return await _employee_management_response(db, owner_id=owner_id)

    @router.get("/management")
    async def management_workspace(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        return await _employee_management_response(
            db,
            owner_id=_text(user.get("id")),
        )

    @router.post("/management/pilot")
    async def create_pilot_employee(
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != PILOT_CREATE_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_pilot_confirmation_required"},
            )
        try:
            values = normalize_pilot_employee_payload(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc)},
            ) from exc

        owner_id = _text(user.get("id"))
        await ensure_employee_v2_indexes(db)
        existing_count = await db[EMPLOYEES].count_documents({
            "user_id": owner_id,
            "source.system": PILOT_SOURCE_SYSTEM,
            "management.mode": "pilot_only",
        })
        if existing_count >= PILOT_EMPLOYEE_LIMIT:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "employee_pilot_limit_reached",
                    "pilot_limit": PILOT_EMPLOYEE_LIMIT,
                },
            )

        employee_id = _stable_id("emppilot", owner_id, "single")
        now = _now()
        monthly_salary = values.pop("monthly_salary")
        employee = {
            "id": employee_id,
            "user_id": owner_id,
            # The existing production index requires a non-null unique legacy
            # key.  This sentinel is never treated as a payroll identity.
            "legacy_employee_id": f"pilot:{employee_id}",
            "financial_entity_id": None,
            **values,
            "account_user_id": None,
            "account_link_status": "not_linked",
            "account_link": None,
            "source": {
                "system": PILOT_SOURCE_SYSTEM,
                "collection": EMPLOYEES,
                "record_id": employee_id,
            },
            "management": {
                "mode": "pilot_only",
                "payroll_enabled": False,
                "legacy_writes_enabled": False,
                "general_ledger_writes_enabled": False,
                "migrated_employee_writes_enabled": False,
            },
            "version": 1,
            "created_at": now,
            "created_by": owner_id,
            "updated_at": now,
            "updated_by": owner_id,
        }
        try:
            await db[EMPLOYEES].insert_one(employee)
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "employee_pilot_limit_reached",
                    "pilot_limit": PILOT_EMPLOYEE_LIMIT,
                },
            ) from exc
        employee.pop("_id", None)

        contract = {
            "id": _stable_id("empctpilot", owner_id, employee_id),
            "user_id": owner_id,
            "employee_id": employee_id,
            "legacy_salary_id": f"pilot:{employee_id}",
            "contract_type": "monthly",
            "monthly_amount": monthly_salary,
            "currency": "SAR",
            "effective_from": values.get("hire_date"),
            "effective_to": None,
            "status": "pilot_only",
            "payroll_enabled": False,
            "accrual_policy": "disabled_during_pilot",
            "source_authority": "employees_v2_pilot_only",
            "created_at": now,
            "created_by": owner_id,
            "updated_at": now,
            "updated_by": owner_id,
        }
        await db[SALARY_CONTRACTS].update_one(
            {"user_id": owner_id, "employee_id": employee_id},
            {"$setOnInsert": contract},
            upsert=True,
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_pilot_created",
            actor=user,
            after={
                **(_employee_audit_view(employee) or {}),
                "monthly_salary": monthly_salary,
                "payroll_enabled": False,
            },
            metadata={
                "rollout_mode": "pilot_only",
                "legacy_writes_made": False,
                "general_ledger_writes_made": False,
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "employee_id": employee_id, **response}

    @router.put("/management/pilot/{employee_id}")
    async def update_pilot_employee(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        owner_id = _text(user.get("id"))
        employee = _require_pilot_employee(await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        ))
        try:
            expected_version = int(payload.get("expected_version"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_expected_version_required"},
            ) from exc
        if expected_version != int(employee.get("version") or 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_version_conflict"},
            )
        editable = {
            key: value for key, value in payload.items()
            if key in {
                "name", "phone", "contact_email", "job_title", "department",
                "hire_date", "status", "notes", "monthly_salary",
            }
        }
        try:
            values = normalize_pilot_employee_payload(editable, partial=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc)},
            ) from exc

        salary_supplied = "monthly_salary" in values
        monthly_salary = values.pop("monthly_salary", None)
        previous_contract = await db[SALARY_CONTRACTS].find_one(
            {
                "user_id": owner_id,
                "employee_id": employee_id,
                "source_authority": "employees_v2_pilot_only",
            },
            {"_id": 0},
        )
        now = _now()
        update_fields = {
            **values,
            "updated_at": now,
            "updated_by": owner_id,
        }
        result = await db[EMPLOYEES].update_one(
            {
                "user_id": owner_id,
                "id": employee_id,
                "version": expected_version,
                "source.system": PILOT_SOURCE_SYSTEM,
            },
            {"$set": update_fields, "$inc": {"version": 1}},
        )
        if not result.matched_count:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_version_conflict"},
            )
        contract_updates: dict[str, Any] = {}
        if salary_supplied:
            contract_updates["monthly_amount"] = monthly_salary
        if "hire_date" in values:
            contract_updates["effective_from"] = values.get("hire_date")
        if contract_updates:
            await db[SALARY_CONTRACTS].update_one(
                {
                    "user_id": owner_id,
                    "employee_id": employee_id,
                    "source_authority": "employees_v2_pilot_only",
                },
                {"$set": {
                    **contract_updates,
                    "payroll_enabled": False,
                    "updated_at": now,
                    "updated_by": owner_id,
                }},
            )
        updated = await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_pilot_updated",
            actor=user,
            before={
                **(_employee_audit_view(employee) or {}),
                "monthly_salary": (previous_contract or {}).get("monthly_amount"),
            },
            after={
                **(_employee_audit_view(updated) or {}),
                "monthly_salary": monthly_salary if salary_supplied else (previous_contract or {}).get("monthly_amount"),
            },
            metadata={
                "changed_fields": sorted(values.keys())
                + (["monthly_salary"] if salary_supplied else []),
                "payroll_enabled": False,
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "employee_id": employee_id, **response}

    @router.put("/management/pilot/{employee_id}/account")
    async def link_pilot_employee_account(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != PILOT_ACCOUNT_LINK_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_account_link_confirmation_required"},
            )
        owner_id = _text(user.get("id"))
        employee = _require_pilot_employee(await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        ))
        account_id = _text(payload.get("account_user_id"))
        if not account_id:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_account_required"},
            )
        current_account_id = _text(employee.get("account_user_id"))
        if current_account_id and current_account_id != account_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_already_linked"},
            )
        if current_account_id == account_id:
            response = await _employee_management_response(db, owner_id=owner_id)
            return {"ok": True, "idempotent_replay": True, **response}

        preview = await _preview_from_db(db, owner_id)
        if account_id in _reserved_review_account_ids(preview.get("employees") or []):
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_reserved_for_migrated_review"},
            )
        account = await db.users.find_one(
            {
                "id": account_id,
                "created_by": owner_id,
                "role": {"$ne": "owner"},
                "disabled": {"$ne": True},
                "is_active": {"$ne": False},
                "$or": [
                    {"deleted_at": {"$exists": False}},
                    {"deleted_at": None},
                ],
            },
            {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1},
        )
        if not account:
            raise HTTPException(
                status_code=404,
                detail={"code": "employee_login_account_not_available"},
            )
        existing_assignment = await db[ROLE_ASSIGNMENTS].find_one(
            {"user_id": account_id},
            {"_id": 0},
        )
        reusable_pilot_assignment = bool(
            existing_assignment
            and existing_assignment.get("assignment_scope") == "employee_pilot"
            and existing_assignment.get("enabled") is False
            and _text(existing_assignment.get("employee_v2_id")) == employee_id
        )
        if existing_assignment and not reusable_pilot_assignment:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_has_existing_role"},
            )
        linked_elsewhere = await db[EMPLOYEES].find_one(
            {
                "user_id": owner_id,
                "account_user_id": account_id,
                "id": {"$ne": employee_id},
            },
            {"_id": 0, "id": 1},
        )
        if linked_elsewhere:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_linked_elsewhere"},
            )
        now = _now()
        link = {
            "account_user_id": account_id,
            "account_link_status": "linked",
            "account_link": {
                "account_name": _text(account.get("name")),
                "account_email": _text(account.get("email")),
                "linked_at": now,
                "linked_by": owner_id,
                "effect_scope": "employees_v2_pilot_only",
                "legacy_user_reverse_link_written": False,
            },
            "updated_at": now,
            "updated_by": owner_id,
        }
        try:
            await db[EMPLOYEES].update_one(
                {"user_id": owner_id, "id": employee_id},
                {"$set": link, "$inc": {"version": 1}},
            )
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_linked_elsewhere"},
            ) from exc
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_pilot_account_linked",
            actor=user,
            before={"account_user_id": None},
            after={
                "account_user_id": account_id,
                "account_name": account.get("name"),
                "account_email": account.get("email"),
            },
            metadata={
                "effect_scope": "employees_v2_pilot_only",
                "legacy_user_reverse_link_written": False,
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "idempotent_replay": False, **response}

    @router.delete("/management/pilot/{employee_id}/account")
    async def unlink_pilot_employee_account(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != PILOT_ACCOUNT_UNLINK_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_account_unlink_confirmation_required"},
            )
        owner_id = _text(user.get("id"))
        employee = _require_pilot_employee(await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        ))
        account_id = _text(employee.get("account_user_id"))
        if not account_id:
            response = await _employee_management_response(db, owner_id=owner_id)
            return {"ok": True, "idempotent_replay": True, **response}
        before_assignment = await db[ROLE_ASSIGNMENTS].find_one(
            {
                "user_id": account_id,
                "employee_v2_id": employee_id,
                "assignment_scope": "employee_pilot",
            },
            {"_id": 0},
        )
        if before_assignment:
            await db[ROLE_ASSIGNMENTS].update_one(
                {"user_id": account_id, "employee_v2_id": employee_id},
                {"$set": {
                    "enabled": False,
                    "effective_permissions": [],
                    "updated_at": _now(),
                    "updated_by": owner_id,
                }},
            )
        await db[EMPLOYEES].update_one(
            {"user_id": owner_id, "id": employee_id},
            {
                "$set": {
                    "account_user_id": None,
                    "account_link_status": "not_linked",
                    "account_link": None,
                    "updated_at": _now(),
                    "updated_by": owner_id,
                },
                "$inc": {"version": 1},
            },
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_pilot_account_unlinked",
            actor=user,
            before={
                "account_user_id": account_id,
                "role_assignment_enabled": bool(before_assignment),
            },
            after={"account_user_id": None, "role_assignment_enabled": False},
            metadata={"effect_scope": "employees_v2_pilot_only"},
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "idempotent_replay": False, **response}

    @router.put("/management/pilot/{employee_id}/role")
    async def assign_pilot_employee_role(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != PILOT_ROLE_ASSIGNMENT_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_role_assignment_confirmation_required"},
            )
        owner_id = _text(user.get("id"))
        employee = _require_pilot_employee(await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        ))
        account_id = _text(employee.get("account_user_id"))
        if not account_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_link_required_before_role"},
            )
        account = await db.users.find_one(
            {"id": account_id, "created_by": owner_id, "role": {"$ne": "owner"}},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1},
        )
        if not account:
            raise HTTPException(
                status_code=404,
                detail={"code": "employee_login_account_not_available"},
            )
        try:
            assignment = validate_assignment(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc)},
            ) from exc
        if assignment["role_key"] == "owner":
            raise HTTPException(
                status_code=422,
                detail={"code": "operational_owner_requires_account_owner"},
            )
        if assignment["warehouse_ids"]:
            found = await db[WAREHOUSES].count_documents({
                "user_id": owner_id,
                "id": {"$in": assignment["warehouse_ids"]},
                "status": {"$ne": "disabled"},
            })
            if found != len(assignment["warehouse_ids"]):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "warehouse_assignment_invalid"},
                )
        before = await db[ROLE_ASSIGNMENTS].find_one(
            {"user_id": account_id},
            {"_id": 0},
        )
        if before and (
            before.get("assignment_scope") != "employee_pilot"
            or _text(before.get("employee_v2_id")) != employee_id
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_has_existing_role"},
            )
        now = _now()
        document = {
            "id": (before or {}).get("id") or uuid.uuid4().hex,
            "user_id": account_id,
            "user_name": account.get("name"),
            "user_email": account.get("email"),
            "employee_v2_id": employee_id,
            "assignment_scope": "employee_pilot",
            **assignment,
            "effective_permissions": effective_permissions(assignment),
            "updated_at": now,
            "updated_by": owner_id,
        }
        if not before:
            document["created_at"] = now
            document["created_by"] = owner_id
        await db[ROLE_ASSIGNMENTS].update_one(
            {"user_id": account_id},
            {"$set": document},
            upsert=True,
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_pilot_role_assigned",
            actor=user,
            before=before,
            after=document,
            metadata={
                "permission_count": len(document["effective_permissions"]),
                "assignment_scope": "employee_pilot",
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "assignment": document, **response}

    @router.get("/management/pilot/{employee_id}/events")
    async def pilot_employee_events(
        employee_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        owner_id = _text(user.get("id"))
        _require_pilot_employee(await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0, "id": 1, "source": 1, "management": 1},
        ))
        items = await db[EMPLOYEE_EVENTS].find(
            {"user_id": owner_id, "employee_id": employee_id},
            {"_id": 0},
        ).sort("occurred_at", -1).limit(200).to_list(200)
        return {"ok": True, "items": items, "total": len(items)}

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
    "PILOT_ACCOUNT_LINK_CONFIRMATION",
    "PILOT_ACCOUNT_UNLINK_CONFIRMATION",
    "PILOT_CREATE_CONFIRMATION",
    "PILOT_ROLE_ASSIGNMENT_CONFIRMATION",
    "PILOT_SOURCE_SYSTEM",
    "SALARY_CONTRACTS",
    "build_employee_management_snapshot",
    "build_employee_migration_preview",
    "ensure_employee_v2_indexes",
    "make_employees_v2_router",
    "normalize_pilot_employee_payload",
]
