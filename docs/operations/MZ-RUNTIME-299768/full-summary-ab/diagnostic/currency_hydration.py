"""Run from the repository root with PYTHONPATH=backend. In-memory synthetic data only.

Each trial continuously requeues a ready asyncio callback during the currency
pass. Its largest gap measures scheduling opportunities during that pass.
Full HTTP timings and scheduled-tick delay are measured separately.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
from time import perf_counter

from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
from order_currency import hydrate_order_currency_fields_cooperative

BASE = "939d4a8f7cfce9ca0c93a99cc9d2595490f66b34"
BASE_FILE = "backend/order_currency.py"


def fixture(size):
    orders, projections = [], []
    for i in range(size):
        order = {"order_number": str(1000000 + i), "currency": "SAR", "total_amount": 100}
        if i % 2:
            order.update(currency="QAR", total_amount=50, total_amount_sar=51.5,
                         exchange_rate_to_sar="1.03", currency_conversion_status="verified", accounting_currency="SAR")
        orders.append(order)
        projections.append({"order_number": order["order_number"],
                            "raw_by_source": {"salla_direct": {"currency": order["currency"]}}})
    return orders, projections


async def trial(arm, frozen, orders, projections):
    rows = deepcopy(orders)
    ticks = []
    loop = asyncio.get_running_loop()
    done = False

    def observer():
        ticks.append(perf_counter())
        if not done:
            loop.call_soon(observer)

    loop.call_soon(observer)
    await asyncio.sleep(0)
    start = perf_counter()
    if arm == "A":
        frozen(rows, projections)
    else:
        await hydrate_order_currency_fields_cooperative(rows, projections, checkpoint=CPUWorkBudget(PARSER_BUDGET_MS).checkpoint)
    end = perf_counter()
    done = True
    await asyncio.sleep(0)
    assert rows[0]["total_amount_sar"] == 100 and rows[-1]["total_amount_sar"] == 51.5
    gap = max(b - a for a, b in zip(ticks, ticks[1:]))
    return (end - start) * 1000, gap * 1000, hashlib.sha256(json.dumps(rows).encode()).hexdigest()


async def main():
    source = subprocess.check_output(["git", "show", f"{BASE}:{BASE_FILE}"])
    namespace = {}
    exec(compile(source, f"{BASE}:{BASE_FILE}", "exec"), namespace)
    frozen = namespace["hydrate_order_currency_fields"]
    runs = []
    for size in (2048, 8192):
        orders, projections = fixture(size)
        arms = {"A": [], "B": []}
        for arm in "ABBA" * 5:
            arms[arm].append(await trial(arm, frozen, orders, projections))
        hashes = {row[2] for trials in arms.values() for row in trials}
        assert len(hashes) == 1
        runs.append({"orders": size, "output_sha256": hashes.pop(), "arms": {
            arm: {"trials": len(trials), "median_elapsed_ms": round(median(row[0] for row in trials), 3),
                  "median_largest_ready_callback_gap_ms": round(median(row[1] for row in trials), 3)}
            for arm, trials in arms.items()}})
    print(json.dumps({"base": BASE, "candidate_module_sha256": hashlib.sha256(Path(BASE_FILE).read_bytes()).hexdigest(),
                      "python": sys.version.split()[0], "order": "ABBA x 5", "runs": runs}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
