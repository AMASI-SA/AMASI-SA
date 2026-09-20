"""Offline preparation checks only. No app imports, env files, network or Mongo."""
import ast
import argparse
import asyncio
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "backend/financial_movements_routes.py").exists())

class FixtureError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise FixtureError(code)


def signed(row):
    return D(row["amount"]) * (1 if row["side"] == "debit" else -1)


def reconcile_case(case, inventory_accounts):
    """Cross-check each GL account/pool delta against detail, before expectations."""
    mapping = case["stock_mapping"]
    allowed = {(m["account"], m["cost_pool"]) for m in mapping.values()}
    require(all(m["account"] in inventory_accounts for m in mapping.values()),
            "invalid_stock_mapping")
    seen, used, allocation_ids = {}, defaultdict(D), set()
    event_count = 0
    for ev in case["events"]:
        gl, detail = defaultdict(D), defaultdict(D)
        for row in ev["legs"]:
            if row["account"] in inventory_accounts:
                key = (row["account"], row.get("cost_pool"))
                require(key in allowed, "unmapped_inventory_leg")
                gl[key] += signed(row)
        for row in ev["stock"]:
            require(row["item"] in mapping, "unmapped_stock_item")
            target = mapping[row["item"]]
            require(row.get("cost_pool") == target["cost_pool"], "stock_pool_mismatch")
            detail[(target["account"], target["cost_pool"])] += D(row["value"])
        require(all(gl[k] == detail[k] for k in set(gl) | set(detail)),
                "inventory_detail_mismatch")
        event_count += bool(gl or detail)
        if ev.get("kind") == "cost_allocation":
            allocation = ev["allocation"]
            source = seen.get(allocation["source_event_id"], {})
            origin = source.get("cost_origin", {})
            require(bool(origin.get("key")) and bool(origin.get("evidence_ref"))
                    and origin["key"] == allocation["cost_origin_key"],
                    "invalid_cost_origin")
            require(allocation["purpose"] == "inbound_acquisition"
                    and D(origin["net"]) == D(origin["inbound_eligible_value"]) + D(origin["outbound_value"]),
                    "invalid_cost_purpose")
            require(sum((signed(r) for r in source["legs"]
                         if r["account"] == "acquisition_clearing"), D(0))
                    == D(origin["inbound_eligible_value"]), "unfunded_cost_origin")
            receipt = seen.get(allocation["receipt_event_id"], {})
            require(receipt.get("kind") == "receipt"
                    and receipt.get("valuation_status") == "accepted"
                    and bool(receipt.get("evidence_ref"))
                    and any(r["item"] == allocation["item"]
                            and r["cost_pool"] == allocation["cost_pool"]
                            and D(r["quantity"]) > 0 and D(r["value"]) > 0
                            for r in receipt.get("stock", [])),
                    "invalid_allocation_receipt")
            require(allocation["id"] not in allocation_ids, "duplicate_fixture_allocation")
            allocation_ids.add(allocation["id"])
            value = D(allocation["value"])
            used[origin["key"]] += value
            require(value > 0 and used[origin["key"]] <= D(origin["inbound_eligible_value"]),
                    "excess_cost_allocation")
            require(len(ev["stock"]) == 1, "allocation_detail_required")
            row = ev["stock"][0]
            require(row["item"] == allocation["item"]
                    and row["cost_pool"] == allocation["cost_pool"]
                    and row.get("receipt_event_id") == receipt["id"]
                    and row.get("allocation_id") == allocation["id"]
                    and row.get("cost_origin_key") == origin["key"]
                    and D(row["quantity"]) == 0 and D(row["value"]) == value,
                    "invalid_value_only_allocation")
            target = mapping[row["item"]]
            expected_legs = [(target["account"], "debit", value),
                             ("acquisition_clearing", "credit", value)]
            require(sorted((r["account"], r["side"], D(r["amount"])) for r in ev["legs"])
                    == sorted(expected_legs), "allocation_repeats_source_accounting")
        require(ev["id"] not in seen, "duplicate_fixture_event")
        seen[ev["id"]] = ev
    return event_count


def fixtures_check(data):
    assert data["synthetic_only"] is True and data["currency"] == "SAR"
    count, reconciled = 0, 0
    for case in data["scenarios"]:
        reconciled += reconcile_case(case, set(data["inventory_accounts"]))
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
    print(f"PASS reconciliation: {reconciled} event-level inventory account/pool deltas match stock detail.")
    print("PASS freight_split: receipt 10 units / 200 + value-only freight 60 = 10 units / 260 / average 26; outbound 40 excluded.")


NEGATIVES = {
    "missing-stock-value": "inventory_detail_mismatch",
    "invalid-receipt": "invalid_allocation_receipt",
    "invalid-origin": "invalid_cost_origin",
}


def negative_data(data, name):
    broken = deepcopy(data)
    case = next(c for c in broken["scenarios"] if c["id"] == "freight_split")
    ev = next(e for e in case["events"] if e["id"] == "inbound_allocation")
    if name == "missing-stock-value":
        ev["stock"] = []  # GL stays balanced; only the detail increase is removed.
    elif name == "invalid-receipt":
        ev["allocation"]["receipt_event_id"] = "nonexistent-receipt"
    elif name == "invalid-origin":
        ev["allocation"]["cost_origin_key"] = "nonexistent-origin"
    return broken


def negative_checks(data):
    for name, expected in NEGATIVES.items():
        broken = negative_data(data, name)
        case = next(c for c in broken["scenarios"] if c["id"] == "freight_split")
        require(all(sum((signed(r) for r in e["legs"]), D(0)) == 0
                    for e in case["events"]), "negative_journal_not_balanced")
        try:
            reconcile_case(case, set(data["inventory_accounts"]))
        except FixtureError as exc:
            require(str(exc) == expected, "unexpected_negative_failure")
            print(f"PASS negative {name}: balanced GL rejected with {exc}.")
        else:
            raise FixtureError(f"negative_case_accepted:{name}")

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--negative-case", choices=NEGATIVES)
    parser.add_argument("--negative-tests", action="store_true")
    parser.add_argument("--legacy-helper-probe", action="store_true",
                        help="Optional historical probe; not needed for freight corrections.")
    args = parser.parse_args()
    data = json.loads((HERE / "synthetic-fixtures.json").read_text(encoding="utf-8"))
    try:
        if args.negative_case:
            fixtures_check(negative_data(data, args.negative_case))
        else:
            if args.negative_tests:
                negative_checks(data)
            fixtures_check(data)
            if args.legacy_helper_probe:
                asyncio.run(helper_probe())
    except FixtureError as exc:
        print(f"REJECTED: {exc}")
        raise SystemExit(1)
