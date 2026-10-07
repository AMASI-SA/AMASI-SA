"""Execute the actual dashboard function without importing server/bootstrap.

Only transitively referenced declarations/import aliases are extracted. The
Mongo database is injected, and loading dotenv/app/client/bootstrap statements
is prohibited. External calculator modules are shared unchanged by both sides.
"""
from __future__ import annotations

import ast
import builtins
import copy
import logging
from pathlib import Path
import subprocess
import symtable
import sys
import types
import uuid
from zoneinfo import ZoneInfo

BASE_SHA = "3a2cc4baab7119e59b66a9d667486e118a527e7a"
REPOSITORY = Path(__file__).resolve().parents[2]


def _references(node):
    table = symtable.symtable(ast.unparse(node), "dashboard-fixture", "exec")
    def walk(scope):
        found = {s.get_name() for s in scope.get_symbols() if s.is_referenced() and (s.is_global() or scope.get_type() == "module")}
        for child in scope.get_children():
            found.update(walk(child))
        return found
    return walk(table) - set(dir(builtins))


def extract_dashboard(db, *, revision=None):
    if revision is None:
        source = (REPOSITORY / "backend/server.py").read_text(encoding="utf-8")
    else:
        source = subprocess.run(["git", "show", f"{revision}:backend/server.py"], cwd=REPOSITORY,
                                capture_output=True, text=True, encoding="utf-8", check=True).stdout
    tree = ast.parse(source)
    declarations = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                selected = copy.deepcopy(node)
                selected.names = [copy.deepcopy(alias)]
                declarations[name] = selected
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            selected = copy.deepcopy(node)
            selected.decorator_list = []
            if node.name == "dashboard":
                # Calls pass all behavior-affecting options explicitly. FastAPI
                # dependency defaults would otherwise pull auth/bootstrap in.
                selected.args.defaults = [ast.Constant(None) for _ in selected.args.defaults]
            declarations[node.name] = selected
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    declarations[target.id] = copy.deepcopy(node)
    module_name = "_dashboard_fixture_" + uuid.uuid4().hex
    module = types.ModuleType(module_name)
    module.__dict__.update(db=db, logger=logging.getLogger(module_name), RIYADH_TZ=ZoneInfo("Asia/Riyadh"))
    sys.modules[module_name] = module
    loaded = set(module.__dict__)
    loading = set()
    forbidden = {"app", "api", "client", "mongo_client", "MONGO_URL", "ROOT_DIR", "load_dotenv"}
    def load(name):
        if name in loaded or name in dir(builtins):
            return
        if name in forbidden:
            raise AssertionError(f"unsafe dashboard fixture dependency: {name}")
        if name in loading:
            return
        if name not in declarations:
            raise AssertionError(f"unresolved dashboard fixture global: {name}")
        node = declarations[name]
        if isinstance(node, ast.ImportFrom) and node.module == "server":
            raise AssertionError("server import forbidden")
        loading.add(name)
        for dependency in _references(node) - {name}:
            load(dependency)
        unit = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
        exec(compile(ast.fix_missing_locations(unit), f"dashboard-fixture:{revision or 'working'}", "exec"), module.__dict__)
        loaded.add(name)
        loading.remove(name)
    try:
        load("dashboard")
        function = module.dashboard
        function.fixture_globals = tuple(sorted(loaded))
        function.fixture_source = revision or "working"
        return function
    finally:
        # Function globals keep their namespace alive only while the test uses
        # it; avoid retaining each extracted function in a process-global cache.
        sys.modules.pop(module_name, None)


async def seed_dashboard(db, *, owner="owner", count=12):
    from auth import ensure_user_settings
    settings = await ensure_user_settings(db, owner)
    await db.settings.update_one({"user_id": owner}, {"$set": {
        "hide_inferred_date_orders": True,
        "report_included_statuses": ["completed", "delivered"],
    }})
    orders = []
    for number in range(count):
        foreign = number % 4 == 0
        orders.append({
            "user_id": owner, "order_number": str(number), "order_date": "2026-09-15",
            "order_date_inferred": number % 11 == 0,
            "order_status": "cancelled" if number % 5 == 0 else "completed",
            "payment_method": "mada" if number % 2 else "cash on delivery",
            "shipping_company": "smsa" if number % 3 else "aramex",
            "total_amount": 100 + number / 10, "currency": "USD" if foreign else "SAR",
            "shipping_amount": 12, "cod_amount": 100 if number % 2 == 0 else 0,
            "data_source": "salla_direct", "total_product_cost": 21.5,
            "products": [{"product_id": "p1", "name": "Fixture", "quantity": 1, "price": 50}],
            "raw_by_source": {"salla_direct": {
                "currency": "USD" if foreign else "SAR", "total_amount": 100 + number / 10,
                "exchange_rate": 3.75 if foreign else 1, "utm_source": "snapchat",
            }},
        })
        if len(orders) == 128:
            await db.unified_orders.insert_many(orders)
            orders = []
    if orders:
        await db.unified_orders.insert_many(orders)
    await db.unified_orders.insert_one({"user_id": "other", "order_number": "other", "order_date": "2026-09-15", "total_amount": 999999})
    await db.daily_costs.insert_one({"user_id": owner, "date": "2026-09-15", "product_costs": 12, "snapchat_ads": 9, "tiktok_ads": 7, "instagram_ads": 5})
    return settings


def financial_business_payload(response):
    """Exclude additive navigation only; retain every row and financial value."""
    from copy import deepcopy
    result = deepcopy(response)
    metadata = result.pop('financial_pagination')
    assert set(metadata) == {'payments', 'shipping', 'sources', 'months'}
    for row in result['payment_breakdown']:
        page = row.pop('sub_methods_pagination')
        assert page['total'] == len(row['sub_methods'])
        assert not page['has_more']
    return result
