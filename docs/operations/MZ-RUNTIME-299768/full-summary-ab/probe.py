"""Counterbalanced, fixed-work local comparison of the two complete FastAPI summary handlers."""
import asyncio
import hashlib
import json
import math
import os
import socket
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from time import perf_counter

def distribution(values):
    values = sorted(values)
    return {key: round(values[math.ceil(len(values) * fraction) - 1], 3)
            for key, fraction in (("p50", .50), ("p95", .95), ("p99", .99), ("max", 1))}

def collect(root, blocks, size, base, candidate):
    runs = [json.loads((Path(root) / f"{i}.json").read_text()) for i in range(len(blocks))]
    for period in ("month", "selected"):
        hashes = {r["sha256"] for run in runs for r in run["requests"] if r["range"] == period}
        assert len(hashes) == 1, (period, hashes)
    aggregate = {}
    for arm in sorted(set(blocks)):
        rows = [r for run in runs if run["arm"] == arm for r in run["requests"]]
        ticks = [delay for row in rows for delay in row.pop("ticks")]
        aggregate[arm] = {"requests": len(rows), "all_http_200": all(r["status"] == 200 for r in rows),
                          "loop_delay_ms": distribution(ticks), "ticks": len(ticks),
                          "summary_duration_ms": distribution([row["duration_ms"] for row in rows])}
    print(json.dumps({"base": base, "candidate": candidate, "sequence": blocks, "orders": int(size),
                      "tick_interval_ms": 5, "aggregate": aggregate, "blocks": runs}, indent=2))

async def main(port, arm, block, size):
    assert size % 4 == 0 and 0 < size <= 4000
    def network(event, args):
        if event == "socket.connect" and args[1] != ("127.0.0.1", port):
            raise RuntimeError("Only the private fixture MongoDB is allowed")
    sys.addaudithook(network)
    import psutil
    if not Path(f"/proc/{os.getpid()}/stat").exists() and Path("/proc/self/stat").exists():
        original = psutil.Process
        class ProcSelf(original):
            def __init__(self, pid=None):
                super().__init__(int(os.readlink("/proc/self")) if pid is None else pid)
        psutil.Process = ProcSelf
    import httpx
    from fastapi import FastAPI
    from motor.motor_asyncio import AsyncIOMotorClient
    # Keep the arm's backend ahead of the frozen test module on sys.path.
    import dashboard_v2_routes as dash
    from order_currency import summarize_orders_sar
    from orders_db import orders_to_parsed_cooperative
    from test_dashboard_b_baseline_mongo import ReadDB, legacy_handler, populate
    assert Path(dash.__file__).resolve().parent == Path.cwd()
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=8000)
    try:
        raw = client[f"synthetic_b{block}"]
        assert await raw.list_collection_names() == []
        await populate(raw, 1)
        template = await raw.unified_orders.find_one({}, {"_id": 0})
        await raw.unified_orders.delete_many({})
        orders = []
        for i in range(size):
            row = deepcopy(template)
            row.update(order_number=str(1000000 + i), order_date="2026-10-08" if i % 4 < 2 else "2026-10-05")
            if i % 2:
                row.update(currency="QAR", total_amount=50, total_amount_sar=51.5,
                           exchange_rate_to_sar="1.03", currency_conversion_status="verified", accounting_currency="SAR")
                row["raw_by_source"]["salla_direct"]["currency"] = "QAR"
            orders.append(row)
        await raw.unified_orders.insert_many(orders)
        dash._today_riyadh = lambda: date(2026, 10, 8)
        db = ReadDB(raw)
        legacy = legacy_handler(db)
        legacy.__globals__.update(summarize_orders_sar=summarize_orders_sar,
                                  orders_to_parsed_cooperative=orders_to_parsed_cooperative,
                                  _dashboard_deepcopy=deepcopy)
        async def owner(): return {"id": "fixture-owner", "role": "owner"}
        def check_owner(user): assert user["role"] == "owner"
        app = FastAPI()
        app.include_router(dash.make_dashboard_v2_router(db, owner, legacy, check_owner), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as http:
            async def request(period):
                whole = period == "month"
                start = perf_counter()
                done = None
                delays = []
                async def heartbeat():
                    due = start + .005
                    while done is None or due <= done:
                        await asyncio.sleep(max(0, due - perf_counter()))
                        now = perf_counter()
                        while due <= (now if done is None else min(now, done)):
                            delays.append(round((now - due) * 1000, 4))
                            due += .005
                pulse = asyncio.create_task(heartbeat())
                try:
                    reply = await http.get("/api/dashboard-v2", params={"from_date": "2026-10-01" if whole else "2026-10-08",
                                                                      "to_date": "2026-10-08"})
                finally:
                    done = perf_counter()
                    await pulse
                assert reply.status_code == 200, (period, reply.status_code, reply.text[:500])
                payload = reply.json()
                for key, count, summary in [("month_kpis", size, payload["month_kpis"]["currency_conversion"]),
                                             ("totals", size if whole else size // 2, payload["currency_conversion"])]:
                    expected = count // 2 * 151.5
                    assert payload[key]["total_sales"] == expected, (key, payload[key], expected)
                    assert summary["accounting_currency"] == "SAR" and summary["converted_orders_count"] == count
                    assert summary["conversion_complete"] and summary["unverified_orders_count"] == 0
                assert not payload["accounting_write_reached"] and not payload["qoyod_write_reached"]
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                return {"range": period, "status": reply.status_code, "sha256": hashlib.sha256(canonical).hexdigest(),
                        "duration_ms": round((done - start) * 1000, 3), "loop_delay_ms": distribution(delays),
                        "ticks": delays, "selected_sales_sar": payload["totals"]["total_sales"],
                        "month_sales_sar": payload["month_kpis"]["total_sales"]}
            for period in ["month", "selected"]:
                await request(period)
            # Each arm sees both period orders in every block.
            rows = [await request(period) for period in ["month", "selected", "selected", "month"]]
        print(json.dumps({"arm": arm, "block": block, "route_sha256": hashlib.sha256(Path(dash.__file__).read_bytes()).hexdigest(),
                          "mongo": (await client.server_info())["version"], "requests": rows}))
    finally:
        client.close()

if __name__ == "__main__":
    if sys.argv[1] == "collect":
        collect(*sys.argv[2:])
    else:
        port, arm, block, size = sys.argv[1:]
        asyncio.run(main(int(port), arm, int(block), int(size)))
