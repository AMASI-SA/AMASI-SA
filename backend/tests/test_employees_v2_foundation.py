from __future__ import annotations

from pathlib import Path

from employees_v2_routes import build_employee_migration_preview


ROOT = Path(__file__).resolve().parents[2]


def _preview(**overrides):
    data = {
        "owner_id": "owner-1",
        "legacy_rows": [
            {
                "id": "legacy-a",
                "name": "موظف أ",
                "category": "employee",
                "country": "saudi",
                "monthly_amount": 3000,
                "start_date": "2026-01-01",
                "status": "active",
                "linked_user_id": "user-a",
            },
            {
                "id": "legacy-b",
                "name": "موظف ب",
                "category": "employee",
                "country": "yemen",
                "monthly_amount": 1500.555,
                "start_date": "2025-06-01",
                "stopped_at": "2026-07-31",
                "status": "stopped",
            },
        ],
        "team_users": [
            {"id": "owner-1", "name": "المالك", "email": "owner@example.com"},
            {"id": "user-a", "name": "موظف أ", "email": "a@example.com"},
            {"id": "user-b", "name": "موظف ب", "email": "b@example.com"},
        ],
        "role_assignments": [
            {
                "user_id": "user-a",
                "role_key": "warehouse_operator",
                "enabled": True,
                "effective_permissions": [
                    "inventory.receipts.read",
                    "inventory.receipts.write",
                ],
                "warehouse_ids": ["warehouse-1"],
                "fulfillment_responsibilities": ["stock_preparation"],
            }
        ],
        "ledger_rows": [
            {"employee_id": "legacy-a", "sub_account": "salary_payable", "side": "credit", "total": 500},
            {"employee_id": "legacy-a", "sub_account": "salary_payable", "side": "debit", "total": 100},
            {"employee_id": "legacy-a", "sub_account": "advance", "side": "debit", "total": 250},
            {"employee_id": "legacy-a", "sub_account": "advance", "side": "credit", "total": 50},
            {"employee_id": "legacy-b", "sub_account": "custody", "side": "debit", "total": 80},
        ],
        "existing_employees": [],
        "existing_contracts": [],
    }
    data.update(overrides)
    return build_employee_migration_preview(**data)


def test_preview_preserves_salary_identity_account_role_and_ledger_balances():
    result = _preview()

    assert result["writes_made"] is False
    assert result["legacy_payroll_authoritative"] is True
    assert result["ledger_authoritative"] is True
    assert result["summary"] == {
        "legacy_employees": 2,
        "active_employees": 1,
        "stopped_employees": 1,
        "active_monthly_salary_total": 3000.0,
        "linked_login_accounts": 1,
        "accounts_needing_review": 1,
        "employees_without_login": 0,
        "already_migrated": 0,
        "ready_to_create": 2,
        "blocking_issues": 0,
        "warnings": 1,
        "salary_payable_total": 400.0,
        "advance_total": 200.0,
        "custody_total": 80.0,
    }

    active = next(row for row in result["employees"] if row["legacy_employee_id"] == "legacy-a")
    assert active["employee_id"].startswith("empv2_")
    assert active["financial_entity_id"] == "legacy-a"
    assert active["salary_contract"]["monthly_amount"] == 3000.0
    assert active["account"]["status"] == "linked"
    assert active["account"]["account_user_id"] == "user-a"
    assert active["operational_role"]["role_key"] == "warehouse_operator"
    assert "inventory.receipts.write" in active["operational_role"]["effective_permissions"]
    assert active["financial_snapshot"] == {
        "salary_payable": 400.0,
        "advance": 200.0,
        "custody": 0.0,
    }

    stopped = next(row for row in result["employees"] if row["legacy_employee_id"] == "legacy-b")
    assert stopped["salary_contract"]["monthly_amount"] == 1500.56
    assert stopped["salary_contract"]["status"] == "ended"
    assert stopped["salary_contract"]["effective_to"] == "2026-07-31"
    assert stopped["account"]["status"] == "review_required"
    assert stopped["account"]["account_user_id"] is None
    assert stopped["account"]["suggested_account"]["id"] == "user-b"


def test_name_only_match_is_never_silently_linked():
    result = _preview(
        legacy_rows=[{
            "id": "legacy-x",
            "name": "نفس الاسم",
            "monthly_amount": 1000,
            "start_date": "2026-01-01",
            "status": "active",
        }],
        team_users=[{"id": "user-x", "name": "نفس الاسم", "email": "x@example.com"}],
        role_assignments=[],
        ledger_rows=[],
    )

    row = result["employees"][0]
    assert row["account"]["method"] == "unique_name_suggestion"
    assert row["account"]["status"] == "review_required"
    assert row["account"]["account_user_id"] is None
    assert row["account"]["suggested_account"]["id"] == "user-x"
    assert result["summary"]["blocking_issues"] == 0


def test_duplicate_legacy_identity_blocks_shadow_apply_readiness():
    duplicate = {
        "id": "legacy-dup",
        "name": "مكرر",
        "monthly_amount": 2000,
        "start_date": "2026-01-01",
        "status": "active",
    }
    result = _preview(
        legacy_rows=[duplicate, {**duplicate, "name": "مكرر آخر"}],
        team_users=[],
        role_assignments=[],
        ledger_rows=[],
    )

    assert result["summary"]["blocking_issues"] == 2
    assert result["summary"]["ready_to_create"] == 0
    assert all(row["migration_status"] == "blocked" for row in result["employees"])
    assert all("duplicate_or_missing_legacy_employee_id" in row["blockers"] for row in result["employees"])


def test_existing_shadow_is_idempotent_and_salary_drift_is_visible():
    baseline = _preview()
    active = next(row for row in baseline["employees"] if row["legacy_employee_id"] == "legacy-a")
    result = _preview(
        existing_employees=[{
            "id": active["employee_id"],
            "legacy_employee_id": "legacy-a",
        }],
        existing_contracts=[{
            "id": active["salary_contract"]["id"],
            "legacy_salary_id": "legacy-a",
            "monthly_amount": 2800,
        }],
    )

    active_after = next(row for row in result["employees"] if row["legacy_employee_id"] == "legacy-a")
    assert active_after["migration_status"] == "already_migrated"
    assert active_after["shadow_exists"] is True
    assert active_after["salary_contract"]["shadow_exists"] is True
    assert "legacy_salary_changed_after_shadow_migration" in active_after["warnings"]
    assert result["summary"]["already_migrated"] == 1


def test_source_fingerprint_and_v2_ids_are_deterministic():
    first = _preview()
    second = _preview(
        legacy_rows=list(reversed([
            {
                "id": "legacy-a",
                "name": "موظف أ",
                "category": "employee",
                "country": "saudi",
                "monthly_amount": 3000,
                "start_date": "2026-01-01",
                "status": "active",
                "linked_user_id": "user-a",
            },
            {
                "id": "legacy-b",
                "name": "موظف ب",
                "category": "employee",
                "country": "yemen",
                "monthly_amount": 1500.555,
                "start_date": "2025-06-01",
                "stopped_at": "2026-07-31",
                "status": "stopped",
            },
        ])),
    )

    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert {
        row["legacy_employee_id"]: row["employee_id"] for row in first["employees"]
    } == {
        row["legacy_employee_id"]: row["employee_id"] for row in second["employees"]
    }


def test_employee_v2_router_page_and_navigation_are_wired_together():
    server = (ROOT / "backend/server.py").read_text(encoding="utf-8")
    app = (ROOT / "frontend/src/App.js").read_text(encoding="utf-8")
    navigation = (
        ROOT / "frontend/src/components/MezanV2NavigationShell.jsx"
    ).read_text(encoding="utf-8")

    assert "make_employees_v2_router" in server
    assert "api.include_router(make_employees_v2_router(db, current_user))" in server
    assert "await ensure_employee_v2_indexes(db)" in server
    assert 'import EmployeesV2 from "./pages/EmployeesV2";' in app
    assert 'path="/employees-v2"' in app
    assert '{ to: "/employees-v2", label: "إدارة الموظفين", exactSearch: true }' in navigation
    assert '{ to: "/employees-v2?workspace=migration", label: "تقرير الترحيل والرواتب" }' in navigation
    assert '{ to: "/employees-v2?workspace=permissions", label: "الصلاحيات وإدارة التجهيز" }' in navigation
