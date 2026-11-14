"""Offline probe using exact production functions and synthetic read-only data.

This is not pytest and does not claim to replace the full repository CI. It
executes the committed tests with a tiny fixture/parameter runner because this
workspace lacks pytest/mongomock. The only emulated persistence is in memory.
Unrelated application imports are sealed; relevant currency/calendar/status,
scope, route, cache, campaign attribution and profit arithmetic are real code.
"""
import argparse
import ast
import asyncio
import calendar
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib.util
import inspect
import itertools
import json
import math
from pathlib import Path
import re
import sys
import traceback
import types
from typing import Any, Optional
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "references/backend"
MISSING = object()


def module(name, path=None, *, select=None, namespace=None):
    target = types.ModuleType(name)
    target.__file__ = str(path) if path else "<sealed offline seam>"
    target.__package__ = name.rpartition(".")[0]
    if namespace:
        target.__dict__.update(namespace)
    sys.modules[name] = target
    if path:
        tree = ast.parse(path.read_text(), filename=str(path))
        if select:
            tree.body = [node for node in tree.body if select(node)]
        exec(compile(tree, str(path), "exec"), target.__dict__)
    return target


def package(name):
    value = module(name)
    value.__path__ = []
    return value


def chosen(names):
    def select(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return node.name in names
        if isinstance(node, ast.Assign):
            return any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
        return isinstance(node, ast.ImportFrom) and node.module == "__future__"
    return select


class Mark:
    def __getattr__(self, key):
        if key == "parametrize":
            def parametrize(names, values):
                def decorate(fn):
                    fn._parameters = getattr(fn, "_parameters", []) + [(names, values)]
                    return fn
                return decorate
            return parametrize
        return lambda fn: fn


class MonkeyPatch:
    def __init__(self):
        self.saved = []

    def setattr(self, obj, key, value):
        self.saved.append((obj, key, getattr(obj, key)))
        setattr(obj, key, value)

    def undo(self):
        for obj, key, value in reversed(self.saved):
            setattr(obj, key, value)


def fixture(*args, **kwargs):
    def decorate(fn):
        fn._fixture_options = kwargs
        return fn
    return decorate(args[0]) if args else decorate


def nested(row, path):
    value = row
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, child) for child in expected):
                return False
            continue
        value = nested(row, key)
        if not isinstance(expected, dict):
            if value is MISSING or value != expected:
                return False
            continue
        for op, other in expected.items():
            if op == "$in":
                if value not in other:
                    return False
            elif op == "$ne":
                if value == other:
                    return False
            elif op in ("$gte", "$lte", "$lt", "$gt"):
                if value is MISSING:
                    return False
                if not {"$gte": lambda: value >= other, "$lte": lambda: value <= other,
                        "$lt": lambda: value < other, "$gt": lambda: value > other}[op]():
                    return False
            elif op == "$type":
                if other == "object" and not isinstance(value, dict):
                    return False
            elif op == "$exists":
                if (value is not MISSING) != other:
                    return False
            else:
                raise AssertionError("unsupported query " + op)
    return True


def projected(row, projection):
    if not projection or not any(v for k, v in projection.items() if k != "_id"):
        return {k: deepcopy(v) for k, v in row.items() if not projection or projection.get(k, 1)}
    result = {}
    for key, include in projection.items():
        if not include:
            continue
        value = nested(row, key)
        if value is MISSING:
            continue
        target = result
        parts = key.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = deepcopy(value)
    return result


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def max_time_ms(self, value):
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows[:length])

    def __aiter__(self):
        self.iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self.iterator))
        except StopIteration:
            raise StopAsyncIteration


class Collection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))

    async def insert_many(self, rows):
        self.rows.extend(deepcopy(rows))

    def find(self, query, projection=None):
        return Cursor([projected(row, projection) for row in self.rows if matches(row, query)])

    async def find_one(self, query, projection=None, **kwargs):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def count_documents(self, query):
        return sum(matches(row, query) for row in self.rows)

    async def update_one(self, query, change):
        assert set(change) == {"$set"}
        for row in self.rows:
            if matches(row, query):
                row.update(deepcopy(change["$set"]))
                return


class Database:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, key):
        return self.collections.setdefault(key, Collection())

    def __getattr__(self, key):
        return self[key]


class Client:
    def __getattr__(self, key):
        return Database()


def load(variant):
    pytest = module("pytest", namespace={"mark": Mark(), "fixture": fixture})
    module("mongomock_motor", namespace={"AsyncMongoMockClient": Client})
    module("tz_utils", namespace={"riyadh_today": lambda: date(2026, 11, 14)})
    employee = module("employee_payroll_status", REF / "employee_payroll_status.py")
    expense_names = {"_parse_iso", "_days_in_month", "_daily_from_monthly", "_daily_from_annual",
        "_salary_active_on", "_rental_active_on", "_prepaid_active_on", "_daily_from_prepaid",
        "compute_operating_expenses_for_range"}
    expense = module("expenses_routes", REF / "expenses_routes.py", select=chosen(expense_names),
        namespace={"calendar": calendar, "date": date, "datetime": datetime, "timedelta": timedelta,
            "Optional": Optional, "employee_salary_active_on": employee.salary_active_on,
            "employee_salary_amount_on": employee.salary_amount_on,
            "employee_salary_rows": employee.employee_salary_rows})
    module("recurring_obligations_routes", REF / "recurring_obligations_routes.py",
        select=lambda node: getattr(node, "lineno", 0) <= 340)
    module("dashboard_cpu_budget", REF / "dashboard_cpu_budget.py")
    module("dashboard_v2_routes", REF / "dashboard_v2_routes.py",
        select=chosen({"_ARABIC_DIACRITICS_RE", "_normalize_match_text", "_matches_any"}),
        namespace={"re": re, "unicodedata": unicodedata})
    module("sold_products_report_v2", REF / "sold_products_report_v2.py",
        select=chosen({"SELECTABLE_STATUSES", "DEFAULT_STATUS_NAMES", "_normalized", "order_matches_status"}),
        namespace={"unicodedata": unicodedata, "Any": Any})
    module("sold_financial_report", REF / "sold_financial_report.py")
    module("order_currency", REF / "order_currency.py")
    module("salla_marketing_attribution", REF / "salla_marketing_attribution.py")
    package("salla_integration")
    module("salla_integration.abandoned_carts", REF / "salla_integration/abandoned_carts.py",
        select=chosen({"_text", "_first", "parse_salla_datetime"}),
        namespace={"datetime": datetime, "timezone": timezone, "Any": Any, "re": re,
            "ZoneInfo": ZoneInfo, "ZoneInfoNotFoundError": ZoneInfoNotFoundError})
    module("marketing_report_costs", REF / "marketing_report_costs.py", select=chosen({"number"}),
        namespace={"isfinite": math.isfinite})
    async def sealed(*args, **kwargs):
        raise AssertionError("unrelated product evidence reader was invoked")
    module("report_option_recovery", namespace={"attach_report_option_evidence_cooperative": sealed})
    module("dashboard_v2_ad_costs", namespace={"apply_cost_settings_to_fact_rows": sealed})
    package("order_engine")
    candidate = ROOT / "candidate/backend/order_engine"
    active = ROOT / variant / "backend/order_engine"
    module("order_engine.campaign_hover_scope", candidate / "campaign_hover_scope.py")
    module("order_engine.campaign_hover_operating", active / "campaign_hover_operating.py")
    module("order_engine.campaign_hover_profit", active / "campaign_hover_profit.py")
    module("order_engine.campaign_hover", active / "campaign_hover.py")
    module("order_engine.campaign_hover_native_sources", REF / "order_engine/campaign_hover_native_sources.py")
    package("tests")
    testroot = ROOT / "candidate/backend/tests"
    operating_tests = module("tests.test_campaign_hover_operating", testroot / "test_campaign_hover_operating.py")
    context_tests = module("tests.test_campaign_hover_operating_context", testroot / "test_campaign_hover_operating_context.py")
    refresh_tests = module("tests.test_campaign_hover_refresh", testroot / "test_campaign_hover_refresh.py")
    return [operating_tests, context_tests, refresh_tests]


def cases(fn):
    combinations = [{}]
    for names, values in getattr(fn, "_parameters", []):
        names = [name.strip() for name in names.split(",")]
        combinations = [{**prior, **dict(zip(names, value if len(names) > 1 else [value]))}
            for prior in combinations for value in values]
    return combinations


def run(variant):
    modules = load(variant)
    outcomes = []
    for testmodule in modules:
        fixtures = [fn for fn in vars(testmodule).values()
            if inspect.isfunction(fn) and getattr(fn, "_fixture_options", {}).get("autouse")]
        for name, fn in vars(testmodule).items():
            if not name.startswith("test_") or not inspect.isfunction(fn):
                continue
            if variant == "baseline" and name != "test_public_selected_day_stays_fixed_across_riyadh_midnight_and_keeps_account_day":
                continue
            for index, parameters in enumerate(cases(fn)):
                patch, teardowns = MonkeyPatch(), []
                label = testmodule.__name__ + "." + name + "[" + str(index) + "]"
                try:
                    for setup in fixtures:
                        value = setup(**({"monkeypatch": patch} if "monkeypatch" in inspect.signature(setup).parameters else {}))
                        if inspect.isgenerator(value):
                            next(value)
                            teardowns.append(value)
                    kwargs = {**parameters}
                    if "monkeypatch" in inspect.signature(fn).parameters:
                        kwargs["monkeypatch"] = patch
                    outcome = fn(**kwargs)
                    if inspect.isawaitable(outcome):
                        asyncio.run(outcome)
                    outcomes.append({"test": label, "status": "passed"})
                except Exception as exc:
                    outcomes.append({"test": label, "status": "failed",
                        "error": type(exc).__name__ + ": " + str(exc),
                        "traceback": traceback.format_exc()})
                finally:
                    for teardown in reversed(teardowns):
                        try:
                            next(teardown)
                        except StopIteration:
                            pass
                    patch.undo()
    failed = [entry for entry in outcomes if entry["status"] != "passed"]
    result = {"variant": variant, "synthetic_only": True, "runner": "isolated-not-pytest",
        "total": len(outcomes), "passed": len(outcomes) - len(failed), "failed": len(failed),
        "failures": failed}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.variant))
