"""Dependency-free focused probe; not a replacement for the repository pytest gate.

Loads the actual salary reducer and actual shared payroll/calendar functions from
the retained source files. Only unavailable app imports are omitted. The DB read
boundary is an in-memory, read-only fixture; no live endpoints or databases exist.
"""
import argparse
import ast
import asyncio
import calendar
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OWNER = "synthetic-salary-scope-owner"
DAY = date(2026, 11, 14)


def load_functions(variant):
    employee_file = ROOT / "references/backend/employee_payroll_status.py"
    employee = ast.parse(employee_file.read_text())
    employee.body = [node for node in employee.body
        if not (isinstance(node, ast.ImportFrom) and node.module == "tz_utils")]
    shared = {"riyadh_today": lambda: DAY}
    exec(compile(employee, str(employee_file), "exec"), shared)
    expenses_file = ROOT / "references/backend/expenses_routes.py"
    expense_ast = ast.parse(expenses_file.read_text())
    selected = {"_parse_iso", "_days_in_month", "_daily_from_monthly", "_salary_active_on"}
    expense_ast.body = [node for node in expense_ast.body
        if isinstance(node, ast.FunctionDef) and node.name in selected]
    expense = {"calendar": calendar, "date": date, "datetime": datetime, "Optional": Optional,
        "employee_salary_active_on": shared["salary_active_on"]}
    exec(compile(expense_ast, str(expenses_file), "exec"), expense)
    budget = {}
    budget_file = ROOT / "references/backend/dashboard_cpu_budget.py"
    exec(compile(budget_file.read_text(), str(budget_file), "exec"), budget)
    source = ROOT / variant / "backend/order_engine/campaign_hover_operating.py"
    tree = ast.parse(source.read_text())
    tree.body = [node for node in tree.body
        if not isinstance(node, (ast.Import, ast.ImportFrom))
        or isinstance(node, ast.ImportFrom) and node.module == "__future__"]
    namespace = {"asyncio": asyncio, "math": math, "defaultdict": defaultdict,
        "datetime": datetime, "timezone": timezone, "Any": Any, "ZoneInfo": ZoneInfo,
        "SALLA_RAW_CURRENCY_PROJECTION": {},
        "CPUWorkBudget": budget["CPUWorkBudget"], "PARSER_BUDGET_MS": budget["PARSER_BUDGET_MS"],
        "_daily_from_monthly": expense["_daily_from_monthly"],
        "_salary_active_on": expense["_salary_active_on"]}
    for name in ["EMPLOYEES_COLLECTION", "SALARY_CONTRACTS_COLLECTION", "employee_salary_rows",
                 "contract_salary_row", "normalized_salary_revisions", "salary_amount_on"]:
        namespace[name] = shared[name]
    exec(compile(tree, str(source), "exec"), namespace)
    return namespace


class Cursor:
    def __init__(self, rows):
        self.rows, self.timeout, self.bound = rows, None, None

    def max_time_ms(self, value):
        assert value == 1500
        self.timeout = value
        return self

    def limit(self, value):
        assert 1 <= value <= 1001
        self.bound = value
        return self

    async def to_list(self, length):
        assert self.timeout == 1500 and self.bound == length
        return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def find(self, query, projection):
        assert query["user_id"] == OWNER
        self.db.reads[self.name] += 1
        rows = []
        for row in self.db.rows[self.name]:
            matches = all(row.get(key) in value["$in"] if isinstance(value, dict)
                else row.get(key) == value for key, value in query.items())
            if matches:
                rows.append({key: deepcopy(row[key]) for key, include in projection.items()
                    if include and key in row})
        return Cursor(rows)


class ReadDB:
    def __init__(self, employees, contracts):
        self.rows = {"mezan_employees_v2": employees,
            "mezan_employee_salary_contracts_v2": contracts, "operating_salaries": []}
        self.reads = Counter()

    def __getitem__(self, name):
        assert name in self.rows
        return Collection(self, name)


def fixture(scope, invalid):
    employee = {"user_id": OWNER, "id": "extra", "status": "active", "hire_date": "2026-11-01"}
    contract = {"user_id": OWNER, "id": "extra-contract", "employee_id": "extra",
        "currency": "SAR", "monthly_amount": 9000, "effective_from": "2026-11-01"}
    if scope in ("archived", "is_archived", "deleted", "is_deleted"):
        employee[scope] = True
    elif scope == "orphan":
        employee = None
    elif scope == "stopped":
        employee.update(status="inactive", updated_at="2026-11-13T12:00:00Z")
    elif scope in ("unpaid_leave", "returned"):
        employee["status"] = "unpaid_leave" if scope == "unpaid_leave" else "active"
        contract["suspension_periods"] = [{"started_on": "2026-11-12",
            "returned_on": None if scope == "unpaid_leave" else "2026-11-14"}]
    elif scope == "future_start":
        contract["effective_from"] = "2026-11-15"
    elif scope == "hire_day":
        employee["hire_date"] = "2026-11-14"
    elif scope == "ended":
        contract["effective_to"] = "2026-11-13"
    if invalid == "missing_amount":
        contract["monthly_amount"] = None
    elif invalid == "invalid_history":
        contract["salary_revisions"] = [{"effective_from": "invalid", "monthly_amount": 9000}]
    elif invalid == "foreign_currency":
        contract["currency"] = "USD"
    if scope == "future_revision":
        employee.pop("hire_date")
        contract.pop("effective_from")
        contract["salary_revisions"] = [{"effective_from": "2026-11-15", "monthly_amount": 9000}]
    if scope == "duplicate":
        employee = None
        contract.update(employee_id="known", effective_to="2026-11-13")
    if scope in ("disabled_pilot", "pilot_revision", "pilot_baseline", "active_undated", "legacy_undated"):
        employee.pop("hire_date")
        contract.pop("effective_from")
        contract["salary_revisions"] = []
        if scope.startswith("pilot_") or scope == "disabled_pilot":
            contract.update(status="pilot_only", payroll_enabled=False,
                accrual_policy="disabled_during_pilot")
        elif scope == "active_undated":
            contract.update(status="active", payroll_enabled=True,
                accrual_policy="calendar_daily_effective_salary_v1")
        if scope == "pilot_revision":
            contract["salary_revisions"] = [{"effective_from": "2026-11-01", "monthly_amount": 9000}]
        elif scope == "pilot_baseline":
            contract["effective_from"] = "2026-11-01"
    employees = [{"user_id": OWNER, "id": "known", "status": "active", "hire_date": "2026-11-01"}]
    contracts = [{"user_id": OWNER, "id": "known-contract", "employee_id": "known", "currency": "SAR",
        "monthly_amount": 3000, "effective_from": "2026-11-01"}]
    if scope != "none":
        if employee is not None:
            employees.append(employee)
        contracts.append(contract)
    return ReadDB(employees, contracts)


async def probe(variant):
    source = load_functions(variant)
    cases = [("none", "none", False)]
    excluded = ["archived", "is_archived", "deleted", "is_deleted", "orphan",
        "stopped", "unpaid_leave", "future_start", "hire_day", "ended"]
    cases += [(scope, invalid, False) for scope in excluded
        for invalid in ["missing_amount", "invalid_history", "foreign_currency"]]
    cases += [("active", invalid, True) for invalid in ["missing_amount", "invalid_history", "foreign_currency"]]
    cases += [("returned", "missing_amount", True), ("duplicate", "missing_amount", True),
        ("future_revision", "foreign_currency", False)]
    cases += [("disabled_pilot", "none", False), ("pilot_revision", "none", False),
        ("pilot_baseline", "none", False), ("active_undated", "none", True),
        ("legacy_undated", "none", True)]
    results = []
    for scope, invalid, expected_unknown in cases:
        db = fixture(scope, invalid)
        before = deepcopy(db.rows)
        result, error = None, None
        try:
            result = await source["_salary_total"](db, OWNER, DAY,
                source["CPUWorkBudget"](source["PARSER_BUDGET_MS"]))
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        unchanged = db.rows == before
        bounded = all(count == 1 for count in db.reads.values())
        expected = 400.0 if scope in ("pilot_revision", "pilot_baseline") else 100.0
        passed = (bool(error) if expected_unknown else error is None and result == expected) and unchanged and bounded
        results.append({"case": scope + "/" + invalid, "passed": passed,
            "expected_unknown": expected_unknown, "expected": None if expected_unknown else expected,
            "actual": result, "exception": error,
            "data_unchanged": unchanged, "read_counts": dict(db.reads)})
    # Existing executive calendar boundaries: first day unpaid, salary revision,
    # first leave day unpaid, and returned_on as the first paid day.
    for day_number, expected in [(1, 0.0), (2, 100.0), (10, 200.0), (12, 0.0), (14, 200.0)]:
        db = fixture("none", "none")
        db.rows["mezan_employee_salary_contracts_v2"][0].update(monthly_amount=6000,
            salary_revisions=[{"effective_from": "2026-11-01", "monthly_amount": 3000},
                {"effective_from": "2026-11-10", "monthly_amount": 6000}],
            suspension_periods=[{"started_on": "2026-11-12", "returned_on": "2026-11-14"}])
        before = deepcopy(db.rows)
        value, error = None, None
        try:
            value = await source["_salary_total"](db, OWNER, date(2026, 11, day_number),
                source["CPUWorkBudget"](source["PARSER_BUDGET_MS"]))
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        unchanged = db.rows == before
        bounded = all(count == 1 for count in db.reads.values())
        results.append({"case": "valid_revision_leave_calendar/" + str(day_number),
            "passed": error is None and value == expected and unchanged and bounded,
            "expected": expected, "actual": value, "exception": error,
            "data_unchanged": unchanged, "read_counts": dict(db.reads)})
    return {"variant": variant, "scope": "actual salary reducer and shared payroll functions; offline DB fixture",
        "cases": len(results), "passed": sum(row["passed"] for row in results),
        "failed": sum(not row["passed"] for row in results), "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", choices=["baseline", "candidate"])
    args = parser.parse_args()
    result = asyncio.run(probe(args.variant))
    output = ROOT / "checks" / (args.variant + "-salary-scope.json")
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "results"}, indent=2))
    print("Evidence:", output)
    raise SystemExit(1 if result["failed"] else 0)
