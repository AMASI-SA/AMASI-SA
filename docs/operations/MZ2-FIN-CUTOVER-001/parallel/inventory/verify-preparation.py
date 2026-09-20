"""Offline preparation checks only. No app imports, env files, network or Mongo."""
import ast
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "backend/financial_movements_routes.py").exists())

def fixtures_check():
    data = json.loads((HERE / "synthetic-fixtures.json").read_text(encoding="utf-8"))
    assert data["synthetic_only"] is True and data["currency"] == "SAR"
    count = 0
    for case in data["scenarios"]:
        balances = defaultdict(D)
        quantities, values = defaultdict(D), defaultdict(D)
        ids = set()
        for ev in case["events"]:
            assert ev["id"] not in ids
            ids.add(ev["id"])
            journal = D(0)
            for row in ev["legs"]:
                amount = D(row["amount"])
                assert amount > 0 and amount == amount.quantize(D(".01"))
                assert row["side"] in ("debit", "credit")
                signed = amount if row["side"] == "debit" else -amount
                balances[row["account"]] += signed
                journal += signed
            assert journal == 0, (case["id"], ev["id"], journal)
            for row in ev["stock"]:
                quantities[row["item"]] += D(row["quantity"])
                values[row["item"]] += D(row["value"])
                assert quantities[row["item"]] >= 0 and values[row["item"]] >= 0
            count += bool(ev["legs"])
        assert dict(balances) == {k: D(v) for k, v in case["expected_balances"].items()}, case["id"]
        assert set(quantities) == set(case["expected_stock"])
        for item, expected in case["expected_stock"].items():
            assert quantities[item] == D(expected["quantity"])
            assert values[item] == D(expected["value"])
            if "average" in expected:
                assert values[item] / quantities[item] == D(expected["average"])
    print(f"PASS: {len(data['scenarios'])} synthetic scenarios, {count} balanced journals; exact expected deltas and stock.")

class MemoryProducts:
    def __init__(self):
        self.row = {"id": "synthetic-product", "user_id": "synthetic-owner",
                    "cost_history": [], "needs_cost": True}
    async def find_one(self, selector, projection=None):
        assert selector["user_id"] == self.row["user_id"]
        return dict(self.row)
    async def update_one(self, selector, update):
        assert selector["user_id"] == self.row["user_id"]
        for key, value in update.get("$push", {}).items():
            self.row[key].append(value)
        self.row.update(update.get("$set", {}))

async def helper_probe():
    path = ROOT / "backend/financial_movements_routes.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    wanted = {"_now", "_r", "_is_active_entry", "_entry_sort_key",
              "recalculate_product_cost", "_apply_product_cost_updates"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    scope = {"datetime": datetime, "timezone": timezone}
    exec(compile(module, str(path), "exec"), scope)
    db = type("MemoryDB", (), {})()
    db.products = MemoryProducts()
    def invoice(key, qty, price):
        return dict(id=key, movement_type="supplier_invoice", supplier_id="synthetic-supplier",
                    doc_date="2026-09-20", line_items=[dict(product_id="synthetic-product",
                    quantity=qty, unit_price=price)])
    apply = scope["_apply_product_cost_updates"]
    first = invoice("invoice1", 10, 5)
    await apply(db, "synthetic-owner", first)
    await apply(db, "synthetic-owner", invoice("invoice2", 30, 7))
    assert len(db.products.row["cost_history"]) == 2
    assert db.products.row["cost_avg"] == 6.5
    print("PASS characterization: invoice helper produces avg=6.5 with no receipts.")
    await apply(db, "synthetic-owner", first)
    assert len(db.products.row["cost_history"]) == 3
    assert db.products.row["cost_avg"] == 6.2
    print("PASS characterization: same invoice helper retry appends again (avg=6.2); NOT a route/DB concurrency test.")

if __name__ == "__main__":
    fixtures_check()
    asyncio.run(helper_probe())
