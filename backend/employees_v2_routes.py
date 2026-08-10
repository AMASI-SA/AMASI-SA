"""Mezan Employees V2 identity management and guarded shadow migration.

The legacy salary engine continues to own payroll until a later, separately
validated cutover.  This module creates the employee identity that Mezan V2
needs and links it to the existing salary, login, operational-role and ledger
records without rewriting legacy payroll or financial history.

Migration modes
---------------
preview
    Read-only projection.  No collection is modified.
shadow_read_only
    Creates idempotent employee and salary-contract snapshots in V2. Legacy
    payroll and the general ledger remain authoritative. After pilot acceptance,
    Employee OS may edit operational identity and access fields, but salary and
    financial snapshots stay read-only until payroll cutover.
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

from auth import hash_password
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
NATIVE_SOURCE_SYSTEM = "mezan_employees_v2_native"
EMPLOYEE_CREATE_CONFIRMATION = "CREATE_EMPLOYEE_V2"
EMPLOYEE_ACCOUNT_LINK_CONFIRMATION = "LINK_EMPLOYEE_V2_ACCOUNT"
EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION = "UNLINK_EMPLOYEE_V2_ACCOUNT"
EMPLOYEE_ROLE_ASSIGNMENT_CONFIRMATION = "ASSIGN_EMPLOYEE_V2_ROLE"
EMPLOYEE_PASSWORD_CONFIRMATION = "RESET_EMPLOYEE_V2_ACCOUNT_PASSWORD"
EMPLOYEE_STATUSES = {"active", "inactive"}


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


def normalize_employee_payload(
    payload: dict[str, Any],
    *,
    partial: bool = False,
) -> dict[str, Any]:
    """Validate operational employee identity fields without financial writes."""
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
        status = _text(payload.get("status") or "active").casefold()
        if status not in EMPLOYEE_STATUSES:
            raise ValueError("employee_status_invalid")
        normalized["status"] = status

    if partial and not normalized:
        raise ValueError("employee_update_empty")
    return normalized


def _require_managed_employee(employee: dict[str, Any] | None) -> dict[str, Any]:
    if not employee:
        raise HTTPException(
            status_code=404,
            detail={"code": "employee_v2_not_found"},
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


def build_employee_management_snapshot(
    *,
    owner_id: str,
    employees: list[dict[str, Any]],
    salary_contracts: list[dict[str, Any]],
    team_users: list[dict[str, Any]],
    role_assignments: list[dict[str, Any]],
    preview_employees: list[dict[str, Any]],
    latest_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the owner-managed Employee OS workspace for all V2 identities."""
    owner_id = _text(owner_id)
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
    users_by_id = {
        _text(row.get("id")): row
        for row in team_users
        if _text(row.get("id"))
    }
    preview_by_employee: dict[str, dict[str, Any]] = {}
    for row in preview_employees:
        for key in (row.get("employee_id"), row.get("legacy_employee_id")):
            normalized = _text(key)
            if normalized:
                preview_by_employee[normalized] = row
    events_by_employee: dict[str, dict[str, Any]] = {}
    for row in latest_events:
        employee_id = _text(row.get("employee_id"))
        if employee_id and employee_id not in events_by_employee:
            events_by_employee[employee_id] = row

    rows: list[dict[str, Any]] = []
    for employee in employees:
        employee_id = _text(employee.get("id"))
        legacy_employee_id = _text(employee.get("legacy_employee_id"))
        preview = (
            preview_by_employee.get(employee_id)
            or preview_by_employee.get(legacy_employee_id)
            or {}
        )
        preview_account = preview.get("account") or {}
        account_user_id = _text(
            employee.get("account_user_id")
            or preview_account.get("account_user_id")
        )
        account = users_by_id.get(account_user_id)
        assignment = assignments_by_user.get(account_user_id)
        raw_status = _text(employee.get("status") or preview.get("status"))
        status = "active" if raw_status == "active" else "inactive"
        contract = contracts_by_employee.get(employee_id) or preview.get("salary_contract")
        account_status = "linked" if account_user_id else _text(
            preview_account.get("status") or "not_linked"
        )
        if account_user_id and not account:
            account_status = "missing"
        source_system = _text((employee.get("source") or {}).get("system"))
        rows.append({
            "id": employee_id,
            "name": _text(employee.get("display_name")),
            "phone": employee.get("phone"),
            "contact_email": employee.get("contact_email"),
            "job_title": employee.get("job_title"),
            "department": employee.get("department"),
            "hire_date": employee.get("hire_date"),
            "status": status,
            "legacy_status": raw_status or None,
            "notes": employee.get("notes"),
            "version": int(employee.get("version") or 1),
            "management_mode": "full_management",
            "source_system": source_system,
            "migrated": source_system == "mezan_legacy",
            "payroll_writes_enabled": False,
            "salary_contract": contract,
            "financial_snapshot": preview.get("financial_snapshot") or {
                "salary_payable": 0.0,
                "advance": 0.0,
                "custody": 0.0,
            },
            "financial_history_preserved": True,
            "account": {
                "status": account_status,
                "user_id": account_user_id or None,
                "name": (account or {}).get("name")
                or (employee.get("account_link") or {}).get("account_name"),
                "email": (account or {}).get("email")
                or (employee.get("account_link") or {}).get("account_email"),
                "account_role": (account or {}).get("role"),
                "access_enabled": bool(
                    account
                    and account.get("disabled") is not True
                    and account.get("is_active") is not False
                    and not account.get("deleted_at")
                ),
                "suggested_account": preview_account.get("suggested_account"),
                "effect_scope": "employee_account_access",
            },
            "operational_role": {
                "role_key": (assignment or {}).get("role_key"),
                "enabled": bool((assignment or {}).get("enabled", False)),
                "effective_permissions": effective_permissions(assignment),
                "extra_permissions": list(
                    (assignment or {}).get("extra_permissions") or []
                ),
                "denied_permissions": list(
                    (assignment or {}).get("denied_permissions") or []
                ),
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
        for row in employees
        if _text(row.get("account_user_id"))
    }
    candidates = []
    for account in team_users:
        account_id = _text(account.get("id"))
        assignment = assignments_by_user.get(account_id)
        if (
            not account_id
            or account_id == owner_id
            or _text(account.get("role")).casefold() == "owner"
            or account.get("is_owner") is True
            or account.get("deleted_at")
            or account_id in used_account_ids
            or _text((assignment or {}).get("employee_v2_id"))
        ):
            continue
        candidates.append({
            "id": account_id,
            "name": _text(account.get("name")),
            "email": _text(account.get("email")),
            "account_role": _text(account.get("role") or "viewer"),
            "disabled": account.get("disabled") is True
            or account.get("is_active") is False,
            "has_existing_role": bool((assignment or {}).get("role_key")),
            "role_key": (assignment or {}).get("role_key"),
        })

    rows.sort(key=lambda row: (row["status"] != "active", row["name"].casefold(), row["id"]))
    candidates.sort(key=lambda row: (row["name"].casefold(), row["email"].casefold()))
    return {
        "rollout_mode": "full_management",
        "managed_count": len(rows),
        "active_count": sum(row["status"] == "active" for row in rows),
        "inactive_count": sum(row["status"] != "active" for row in rows),
        "linked_account_count": sum(bool(row["account"]["user_id"]) for row in rows),
        "employees_with_roles": sum(bool(row["operational_role"]["role_key"]) for row in rows),
        "can_create_employee": True,
        "migrated_employee_writes_enabled": True,
        "legacy_payroll_writes_enabled": False,
        "general_ledger_writes_enabled": False,
        "account_link_effect_scope": "employee_account_access",
        "role_assignment_scope": "linked_employee_account_only",
        "financial_writes": 0,
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
    *,
    owner_id: str,
) -> dict[str, Any]:
    legacy_id = _text(legacy.get("id"))
    all_users_by_id = {
        _text(row.get("id")): row for row in team_users if _text(row.get("id"))
    }
    users_by_id = {
        account_id: row
        for account_id, row in all_users_by_id.items()
        if account_id != _text(owner_id)
        and _text(row.get("role")).casefold() != "owner"
        and row.get("is_owner") is not True
    }
    explicit_id = _text(
        legacy.get("account_user_id")
        or legacy.get("linked_user_id")
        or legacy.get("user_account_id")
    )
    if explicit_id and explicit_id in all_users_by_id and explicit_id not in users_by_id:
        return {
            "status": "review_required",
            "method": "owner_account_forbidden",
            "account_user_id": None,
            "account_name": None,
            "account_email": None,
            "suggested_account": None,
        }
    reverse_links = [
        row for row in users_by_id.values()
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
        row for row in users_by_id.values()
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
        account = _account_resolution(legacy, team_users, owner_id=owner_id)
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
    employees = await db[EMPLOYEES].find(
        {"user_id": owner_id},
        {"_id": 0},
    ).sort("created_at", -1).to_list(5000)
    employee_ids = [_text(row.get("id")) for row in employees if _text(row.get("id"))]
    linked_account_ids = [
        _text(row.get("account_user_id"))
        for row in employees
        if _text(row.get("account_user_id"))
    ]
    salary_contracts = await db[SALARY_CONTRACTS].find(
        {
            "user_id": owner_id,
            "employee_id": {"$in": employee_ids},
        },
        {"_id": 0},
    ).to_list(max(len(employee_ids) * 2, 1))
    team_users = await db.users.find(
        {"$or": [
            {"id": owner_id},
            {"created_by": owner_id},
            {"id": {"$in": linked_account_ids}},
        ]},
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
        {"user_id": owner_id, "employee_id": {"$in": employee_ids}},
        {"_id": 0},
    ).sort("occurred_at", -1).to_list(5000)
    return build_employee_management_snapshot(
        owner_id=owner_id,
        employees=employees,
        salary_contracts=salary_contracts,
        team_users=team_users,
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
        "mode": "full_management",
        "management": management,
    }


def _account_access_view(account: dict[str, Any] | None) -> dict[str, Any] | None:
    if not account:
        return None
    return {
        "id": account.get("id"),
        "name": account.get("name"),
        "email": account.get("email"),
        "disabled": account.get("disabled") is True,
        "is_active": account.get("is_active") is not False,
        "password_updated_at": account.get("password_updated_at"),
    }


def _role_audit_view(assignment: dict[str, Any] | None) -> dict[str, Any] | None:
    if not assignment:
        return None
    return {
        key: assignment.get(key)
        for key in (
            "role_key",
            "enabled",
            "effective_permissions",
            "warehouse_ids",
            "workplace_warehouse_id",
            "fulfillment_responsibilities",
            "employee_v2_id",
            "assignment_scope",
        )
    }


async def _set_employee_account_access(
    db: Any,
    *,
    account_id: str,
    active: bool,
    owner_id: str,
    reason: str,
) -> None:
    """Enable or revoke login and operational permissions as one policy step."""
    account_id = _text(account_id)
    if not account_id:
        return
    now = _now()
    account_update = await db.users.update_one(
        {"id": account_id, "created_by": owner_id, "role": {"$ne": "owner"}},
        {"$set": {
            "disabled": not active,
            "is_active": active,
            "employee_access_state": "active" if active else "disabled",
            "employee_access_reason": reason,
            "employee_access_updated_at": now,
            "employee_access_updated_by": owner_id,
        }},
    )
    if not account_update.matched_count:
        return
    assignment = await db[ROLE_ASSIGNMENTS].find_one(
        {"user_id": account_id},
        {"_id": 0},
    )
    if not assignment:
        return
    if active:
        restored_enabled = bool(
            assignment.get("enabled_before_employee_suspension", False)
        ) if assignment.get("suspended_by_employee_v2") else bool(
            assignment.get("enabled", False)
        )
        restored = {**assignment, "enabled": restored_enabled}
        role_updates = {
            "enabled": restored_enabled,
            "effective_permissions": effective_permissions(restored),
            "suspended_by_employee_v2": False,
            "updated_at": now,
            "updated_by": owner_id,
        }
    else:
        role_updates = {
            "enabled_before_employee_suspension": bool(
                assignment.get("enabled_before_employee_suspension")
                if assignment.get("suspended_by_employee_v2")
                else assignment.get("enabled", False)
            ),
            "enabled": False,
            "effective_permissions": [],
            "suspended_by_employee_v2": True,
            "updated_at": now,
            "updated_by": owner_id,
        }
    await db[ROLE_ASSIGNMENTS].update_one(
        {"user_id": account_id},
        {"$set": role_updates},
    )


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

    @router.post("/management/employees")
    async def create_employee(
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != EMPLOYEE_CREATE_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_create_confirmation_required"},
            )
        try:
            values = normalize_employee_payload(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc)},
            ) from exc

        owner_id = _text(user.get("id"))
        await ensure_employee_v2_indexes(db)
        employee_id = f"empv2_{uuid.uuid4().hex}"
        now = _now()
        employee = {
            "id": employee_id,
            "user_id": owner_id,
            # Preserve the existing unique legacy-key index without claiming a
            # salary identity in the legacy payroll source.
            "legacy_employee_id": f"native:{employee_id}",
            "financial_entity_id": None,
            **values,
            "account_user_id": None,
            "account_link_status": "not_linked",
            "account_link": None,
            "source": {
                "system": NATIVE_SOURCE_SYSTEM,
                "collection": EMPLOYEES,
                "record_id": employee_id,
            },
            "management": {
                "mode": "full_management",
                "payroll_enabled": False,
                "legacy_writes_enabled": False,
                "general_ledger_writes_enabled": False,
                "migrated_employee_writes_enabled": True,
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
                detail={"code": "employee_v2_identity_conflict"},
            ) from exc
        employee.pop("_id", None)
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_created",
            actor=user,
            after=_employee_audit_view(employee),
            metadata={
                "rollout_mode": "full_management",
                "legacy_writes_made": False,
                "general_ledger_writes_made": False,
                "salary_contract_created": False,
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "employee_id": employee_id, **response}

    @router.put("/management/employees/{employee_id}")
    async def update_employee(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        owner_id = _text(user.get("id"))
        employee = _require_managed_employee(await db[EMPLOYEES].find_one(
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
                "hire_date", "status", "notes",
            }
        }
        try:
            values = normalize_employee_payload(editable, partial=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc)},
            ) from exc

        account_id = _text(employee.get("account_user_id"))
        before_account = await db.users.find_one(
            {"id": account_id}, {"_id": 0}
        ) if account_id else None
        before_assignment = await db[ROLE_ASSIGNMENTS].find_one(
            {"user_id": account_id}, {"_id": 0}
        ) if account_id else None
        now = _now()
        update_fields = {
            **values,
            "version": expected_version + 1,
            "updated_at": now,
            "updated_by": owner_id,
        }
        version_query: dict[str, Any] = {
            "user_id": owner_id,
            "id": employee_id,
        }
        if expected_version == 1 and "version" not in employee:
            version_query["version"] = {"$exists": False}
        else:
            version_query["version"] = expected_version
        result = await db[EMPLOYEES].update_one(
            version_query,
            {"$set": update_fields},
        )
        if not result.matched_count:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_version_conflict"},
            )
        if "status" in values and account_id:
            await _set_employee_account_access(
                db,
                account_id=account_id,
                active=values["status"] == "active",
                owner_id=owner_id,
                reason="employee_status_changed",
            )
        updated = await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_updated",
            actor=user,
            before={
                "employee": _employee_audit_view(employee),
                "account_access": _account_access_view(before_account),
                "role_assignment": _role_audit_view(before_assignment),
            },
            after={
                "employee": _employee_audit_view(updated),
                "account_access": _account_access_view(
                    await db.users.find_one({"id": account_id}, {"_id": 0})
                    if account_id else None
                ),
                "role_assignment": _role_audit_view(
                    await db[ROLE_ASSIGNMENTS].find_one(
                        {"user_id": account_id}, {"_id": 0}
                    ) if account_id else None
                ),
            },
            metadata={
                "changed_fields": sorted(values.keys()),
                "legacy_payroll_writes_made": False,
                "general_ledger_writes_made": False,
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "employee_id": employee_id, **response}

    @router.put("/management/employees/{employee_id}/account")
    async def link_employee_account(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != EMPLOYEE_ACCOUNT_LINK_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_account_link_confirmation_required"},
            )
        owner_id = _text(user.get("id"))
        employee = _require_managed_employee(await db[EMPLOYEES].find_one(
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
        account = await db.users.find_one(
            {
                "id": account_id,
                "created_by": owner_id,
                "role": {"$ne": "owner"},
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
        assigned_employee_id = _text((existing_assignment or {}).get("employee_v2_id"))
        if assigned_employee_id and assigned_employee_id != employee_id:
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
                "effect_scope": "employee_account_access",
                "legacy_user_reverse_link_written": False,
            },
            "updated_at": now,
            "updated_by": owner_id,
        }
        try:
            await db[EMPLOYEES].update_one(
                {"user_id": owner_id, "id": employee_id},
                {"$set": {
                    **link,
                    "version": int(employee.get("version") or 1) + 1,
                }},
            )
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_linked_elsewhere"},
            ) from exc
        if existing_assignment:
            await db[ROLE_ASSIGNMENTS].update_one(
                {"user_id": account_id},
                {"$set": {
                    "employee_v2_id": employee_id,
                    "assignment_scope": "employee_v2",
                    "updated_at": now,
                    "updated_by": owner_id,
                }},
            )
        await _set_employee_account_access(
            db,
            account_id=account_id,
            active=_text(employee.get("status")) == "active",
            owner_id=owner_id,
            reason="employee_account_linked",
        )
        updated_account = await db.users.find_one({"id": account_id}, {"_id": 0})
        updated_assignment = await db[ROLE_ASSIGNMENTS].find_one(
            {"user_id": account_id}, {"_id": 0}
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_account_linked",
            actor=user,
            before={
                "account_user_id": None,
                "account_access": _account_access_view(account),
                "role_assignment": _role_audit_view(existing_assignment),
            },
            after={
                "account_user_id": account_id,
                "account_access": _account_access_view(updated_account),
                "role_assignment": _role_audit_view(updated_assignment),
            },
            metadata={
                "effect_scope": "employee_account_access",
                "legacy_user_reverse_link_written": False,
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "idempotent_replay": False, **response}

    @router.delete("/management/employees/{employee_id}/account")
    async def unlink_employee_account(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_account_unlink_confirmation_required"},
            )
        owner_id = _text(user.get("id"))
        employee = _require_managed_employee(await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        ))
        account_id = _text(employee.get("account_user_id"))
        if not account_id:
            response = await _employee_management_response(db, owner_id=owner_id)
            return {"ok": True, "idempotent_replay": True, **response}
        before_account = await db.users.find_one(
            {"id": account_id}, {"_id": 0}
        )
        before_assignment = await db[ROLE_ASSIGNMENTS].find_one(
            {"user_id": account_id},
            {"_id": 0},
        )
        await _set_employee_account_access(
            db,
            account_id=account_id,
            active=False,
            owner_id=owner_id,
            reason="employee_account_unlinked",
        )
        if before_assignment:
            await db[ROLE_ASSIGNMENTS].update_one(
                {"user_id": account_id},
                {"$set": {
                    "enabled": False,
                    "effective_permissions": [],
                    "employee_v2_id": None,
                    "assignment_scope": "employee_v2_unlinked",
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
                    "version": int(employee.get("version") or 1) + 1,
                },
            },
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_account_unlinked",
            actor=user,
            before={
                "account_user_id": account_id,
                "account_access": _account_access_view(before_account),
                "role_assignment": _role_audit_view(before_assignment),
            },
            after={
                "account_user_id": None,
                "account_access": _account_access_view(
                    await db.users.find_one({"id": account_id}, {"_id": 0})
                ),
                "role_assignment": _role_audit_view(
                    await db[ROLE_ASSIGNMENTS].find_one(
                        {"user_id": account_id}, {"_id": 0}
                    )
                ),
            },
            metadata={"effect_scope": "employee_account_access"},
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "idempotent_replay": False, **response}

    @router.put("/management/employees/{employee_id}/role")
    async def assign_employee_role(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != EMPLOYEE_ROLE_ASSIGNMENT_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_role_assignment_confirmation_required"},
            )
        owner_id = _text(user.get("id"))
        employee = _require_managed_employee(await db[EMPLOYEES].find_one(
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
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "email": 1,
                "role": 1,
                "disabled": 1,
                "is_active": 1,
            },
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
        bound_employee_id = _text((before or {}).get("employee_v2_id"))
        if bound_employee_id and bound_employee_id != employee_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_has_existing_role"},
            )
        now = _now()
        requested_enabled = bool(assignment["enabled"])
        employee_active = _text(employee.get("status")) == "active"
        persisted_assignment = {
            **assignment,
            "enabled": requested_enabled if employee_active else False,
        }
        document = {
            "id": (before or {}).get("id") or uuid.uuid4().hex,
            "user_id": account_id,
            "user_name": account.get("name"),
            "user_email": account.get("email"),
            "employee_v2_id": employee_id,
            "assignment_scope": "employee_v2",
            **persisted_assignment,
            "effective_permissions": effective_permissions(persisted_assignment),
            "enabled_before_employee_suspension": requested_enabled,
            "suspended_by_employee_v2": not employee_active,
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
        await _set_employee_account_access(
            db,
            account_id=account_id,
            active=employee_active,
            owner_id=owner_id,
            reason="employee_role_assigned",
        )
        saved = await db[ROLE_ASSIGNMENTS].find_one(
            {"user_id": account_id}, {"_id": 0}
        ) or document
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_role_assigned",
            actor=user,
            before=_role_audit_view(before),
            after=_role_audit_view(saved),
            metadata={
                "permission_count": len(saved.get("effective_permissions") or []),
                "assignment_scope": "employee_v2",
                "employee_active": employee_active,
            },
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, "assignment": saved, **response}

    @router.put("/management/employees/{employee_id}/account/password")
    async def reset_employee_account_password(
        employee_id: str,
        payload: dict[str, Any] = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        if _text(payload.get("confirmation")) != EMPLOYEE_PASSWORD_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_password_confirmation_required"},
            )
        password = str(payload.get("new_password") or "")
        if len(password) < 6 or len(password) > 128:
            raise HTTPException(
                status_code=422,
                detail={"code": "employee_password_invalid"},
            )
        owner_id = _text(user.get("id"))
        employee = _require_managed_employee(await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0},
        ))
        account_id = _text(employee.get("account_user_id"))
        if not account_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "employee_account_link_required_before_password"},
            )
        account = await db.users.find_one(
            {"id": account_id, "created_by": owner_id, "role": {"$ne": "owner"}},
        )
        if not account:
            raise HTTPException(
                status_code=404,
                detail={"code": "employee_login_account_not_available"},
            )
        now = _now()
        await db.users.update_one(
            {"id": account_id},
            {"$set": {
                "password_hash": hash_password(password),
                "password_updated_at": now,
                "password_updated_by": owner_id,
            }},
        )
        updated_account = await db.users.find_one(
            {"id": account_id}, {"_id": 0}
        )
        await _record_employee_event(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
            event_type="employee_account_password_reset",
            actor=user,
            before=_account_access_view(account),
            after=_account_access_view(updated_account),
            metadata={"password_secret_recorded": False},
        )
        response = await _employee_management_response(db, owner_id=owner_id)
        return {"ok": True, **response}

    @router.get("/management/employees/{employee_id}/events")
    async def employee_events(
        employee_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        _require_owner(user)
        owner_id = _text(user.get("id"))
        _require_managed_employee(await db[EMPLOYEES].find_one(
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
    "EMPLOYEE_ACCOUNT_LINK_CONFIRMATION",
    "EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION",
    "EMPLOYEE_CREATE_CONFIRMATION",
    "EMPLOYEE_PASSWORD_CONFIRMATION",
    "EMPLOYEE_ROLE_ASSIGNMENT_CONFIRMATION",
    "NATIVE_SOURCE_SYSTEM",
    "SALARY_CONTRACTS",
    "build_employee_management_snapshot",
    "build_employee_migration_preview",
    "ensure_employee_v2_indexes",
    "make_employees_v2_router",
    "normalize_employee_payload",
]
