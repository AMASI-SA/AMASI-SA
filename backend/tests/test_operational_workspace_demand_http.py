"""Real Mongo / actual subprocess boundary and same-data read-work comparison."""
import asyncio
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import time

import httpx
import pytest
from fastapi import FastAPI, Header

from demand_read_mongo_fixture import read_db, read_mongo_uri
import demand_read_jobs as jobs
import demand_read_store as store
import operational_balance_workspace as workspace
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, initial
from operational_workspace_read_builders import authorize_spec, build_projection

BASE = "/api/operational-balances/workspace"
HEADERS = {"X-Mezan-Read-Consumer": "synthetic-visible-consumer"}


async def seed_workspace(db, *, suppliers=1, movements=16):
    await db.users.insert_many([
        {"id": "owner", "role": "owner", "is_active": True, "name": "مالك تجريبي"},
        {"id": "staff", "role": "employee", "created_by": "owner", "name": "موظف تجريبي", "is_active": True,
         "operational_balance_access": {"owner_id": "owner", "enabled": True, "version": 1,
             "operations": [], "resources": [{"kind": "supplier", "id": "supplier-000", "use": False,
                 "movements": "own", "balance": False, "report": False, "print": False}]}},
        {"id": "other", "role": "owner", "is_active": True},
    ])
    await db.mz2_financial_accounts.insert_many([
        {"id": "bank", "user_id": "owner", "name": "بنك تجريبي", "account_type": "bank", "currency": "SAR", "status": "active"},
        {"id": "cash", "user_id": "owner", "name": "صندوق تجريبي", "account_type": "cash", "currency": "SAR", "status": "active"},
    ])
    await db.mezan_suppliers_v2.insert_many([
        {"id": f"supplier-{i:03}", "user_id": "owner", "name": f"مورد تجريبي {i:03}", "status": "active"}
        for i in range(suppliers)])
    state = initial("owner")
    state.update(status="active", revision=5, started_at="2026-11-01T00:00:00+00:00")
    for kind, identity, amount, direction in [("bank", "bank", "1000000.00", "for_us"),
            ("cash", "cash", "500.00", "for_us"),
            *[("supplier", f"supplier-{i:03}", "1000.00", "for_party") for i in range(suppliers)]]:
        key = f"{kind}:{identity}"
        state["openings"][key] = {"id": key, "party_type": kind, "party_id": identity, "currency": "SAR",
            "amount": amount, "direction": direction, "name": identity, "actor_id": "owner",
            "created_at": "2026-11-01T00:00:00+00:00"}
    state["movements"] = [{"id": f"movement-{i:05}", "party_type": "supplier", "party_id": f"supplier-{i % suppliers:03}",
        "currency": "SAR", "amount": "0.10", "actual_fee_amount": "0.00", "kind": "payment", "direction": "outgoing",
        "settlement_scope": "account", "allocations": [], "actor_id": "owner",
        "recorded_by": {"id": "owner", "name": "مالك تجريبي"}, "source": "mezan2", "bank_id": "bank", "bank_kind": "bank",
        "bank_name": "بنك تجريبي", "name": f"مورد تجريبي {i % suppliers:03}", "business_date": f"2026-11-{i % 20 + 2:02}",
        "occurred_at": f"2026-11-22T10:{i // 60 % 60:02}:{i % 60:02}+00:00", "reference": f"مرجع-{i:05}",
        "workspace_operation": "supplier.payment", "workspace_reason": "payment"}
        for i in range(movements)]
    await db[STATES].insert_one(state)
    return state


def application(db):
    app = FastAPI()
    async def principal(x_test_user: str = Header(default="owner")):
        return {"id": x_test_user, "_session_client": "web"}
    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    @app.get("/health")
    async def loop_health():
        # Mirrors production's no-I/O health scheduling boundary. Full deployed
        # health and its release identity are separately verified after publish.
        return {"ok": True}
    return app


async def settle(db):
    return await jobs.execute_once(db, builder=build_projection, authorize=authorize_spec)


@pytest.mark.asyncio
async def test_real_http_pages_coalesce_reuse_filters_and_reject_revoked_scope(read_db, monkeypatch):
    db = read_db
    before = await seed_workspace(db, movements=40)
    calls = 0
    original = workspace.report
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(workspace, "report", counted)
    path = BASE + "/movements"
    filters = {"demand": True, "q": "مرجع", "date_from": "2026-11-03", "date_to": "2026-11-20", "page_size": 8}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://isolated") as client:
        baseline = await client.get(path, params={k: v for k, v in filters.items() if k != "demand"})
        assert baseline.status_code == 200, baseline.text
        calls = 0
        pending = await asyncio.gather(*[client.get(path, params={**filters, "page": page},
            headers={"X-Mezan-Read-Consumer": f"consumer_page_{page}"}) for page in (1, 2, 3)])
        assert all(r.status_code == 202 for r in pending), [r.text for r in pending]
        assert len({r.json()["key"] for r in pending}) == 1 and calls == 0
        assert await settle(db) is True and calls == 1
        rows = []
        for page in range(1, 5):
            response = await client.get(path, params={**filters, "page": page}, headers=HEADERS)
            assert response.status_code == 200, response.text
            rows.extend(response.json()["items"])
            assert response.json()["snapshot"]["revision"] == 5
        assert calls == 1
        assert rows[:8] == baseline.json()["items"]
        assert len({r["event_id"] for r in rows}) == len(rows)
        # Exact same prepared body remains actor-bound after a policy change.
        staff = {**HEADERS, "x-test-user": "staff"}
        response = await client.get(BASE + "/home", headers=staff)
        assert response.status_code == 202
        assert await settle(db) is True
        assert (await client.get(BASE + "/home", headers=staff)).status_code == 200
        await db.users.update_one({"id": "staff"}, {"$set": {"disabled": True}})
        assert (await client.get(BASE + "/home", headers=staff)).status_code == 403
        await db[STATES].update_one({"_id": "owner"}, {"$inc": {"revision": 1}})
        changed = await client.get(path, params=filters, headers=HEADERS)
        assert changed.status_code == 202 and changed.json()["key"] != pending[0].json()["key"]
    after = await db[STATES].find_one({"_id": "owner"})
    after["revision"] = before["revision"]
    assert after == before


@pytest.mark.asyncio
async def test_directory_http_acquires_only_one_page_and_focus_uses_stored_ordinals(read_db):
    db = read_db
    await seed_workspace(db, suppliers=60, movements=40)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://isolated") as client:
        query = {"group": "parties", "kind": "supplier", "q": "تجريبي", "page_size": 24}
        assert (await client.get(BASE + "/directory", params=query, headers=HEADERS)).status_code == 202
        assert await settle(db) is True
        page = await client.get(BASE + "/directory", params={**query, "page": 2}, headers=HEADERS)
        assert page.status_code == 200 and page.json()["total"] == 60
        assert len(page.json()["items"]) == 24
        assert [r["id"] for r in page.json()["items"]] == [f"supplier-{i:03}" for i in range(24, 48)]
        path = BASE + "/ledger/bank/bank"
        assert (await client.get(path, params={"demand": True}, headers=HEADERS)).status_code == 202
        assert await settle(db) is True
        focus = await client.get(path, params={"demand": True, "focus_id": "movement-00000", "page_size": 5}, headers=HEADERS)
        assert focus.status_code == 200, focus.text
        assert any(r["id"] == "movement-00000" for r in focus.json()["items"])
        assert focus.json()["page"] > 1


@pytest.mark.asyncio
async def test_actual_child_keeps_health_and_bootstrap_responsive_on_same_large_fixture(read_db, read_mongo_uri, monkeypatch):
    db = read_db
    before = await seed_workspace(db, suppliers=200, movements=5000)
    monkeypatch.setenv("MONGO_URL", read_mongo_uri)
    monkeypatch.setenv("DB_NAME", db.name)
    monkeypatch.setenv("MEZAN_BACKGROUND_WORKER_PROCESS", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    metrics = []
    monkeypatch.setattr(jobs, "_emit_metrics", lambda record: metrics.append(record))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://isolated") as client:
        start = time.perf_counter()
        initial_response = await client.get(BASE + "/home", headers=HEADERS)
        assert initial_response.status_code == 202, initial_response.text
        claim = await jobs.claim_next(db)
        assert claim is not None
        work = asyncio.create_task(jobs.run_claim(db, claim))
        probe_times = []
        bootstrap_times = []
        try:
            while not work.done():
                probe = time.perf_counter()
                health = await client.get("/health")
                probe_times.append((time.perf_counter() - probe) * 1000)
                assert health.status_code == 200
                probe = time.perf_counter()
                light = await client.get(BASE + "/bootstrap", headers={"x-test-user": "staff"})
                bootstrap_times.append((time.perf_counter() - probe) * 1000)
                assert light.status_code == 200 and light.json()["resources"] == []
                await asyncio.sleep(.02)
            await work
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
        ready = await client.get(BASE + "/home", headers=HEADERS)
        cold_ms = (time.perf_counter() - start) * 1000
        assert ready.status_code == 200, (ready.text, metrics)
        assert ready.json()["summary"]["total"] == "1000000.00"  # million +500 cash -500 paid
        assert metrics[0]["pid"] != os.getpid() and metrics[0]["outcome"] == "ready"
        assert max(probe_times) < 1000 and max(bootstrap_times) < 1000
        baseline_times, ready_times = [], []
        old = None
        for _ in range(5):
            stamp = time.perf_counter()
            old = await client.get(BASE + "/context", params={"home_preview": True, "provider_positions": True, "ad_bank_fx": True})
            baseline_times.append((time.perf_counter() - stamp) * 1000)
            assert old.status_code == 200
        for _ in range(20):
            stamp = time.perf_counter()
            result = await client.get(BASE + "/home", headers=HEADERS)
            ready_times.append((time.perf_counter() - stamp) * 1000)
            assert result.status_code == 200
            assert result.json()["recent_movements"] == old.json()["recent_movements"]
        concurrent_start = time.perf_counter()
        together = await asyncio.gather(*[client.get(BASE + "/home", headers=HEADERS) for _ in range(6)])
        concurrent_ms = (time.perf_counter() - concurrent_start) * 1000
        assert all(r.status_code == 200 for r in together)
        p95 = sorted(ready_times)[math.ceil(.95 * len(ready_times)) - 1]
        assert p95 < 1000
    assert await db[STATES].find_one({"_id": "owner"}) == before
    evidence = {"environment": "isolated loopback Mongo; ASGI transport; no browser/network timing",
        "data": {"suppliers": 200, "movements": 5000}, "cold_home_ms": cold_ms,
        "home_legacy_ms": baseline_times, "home_ready_ms": ready_times, "home_ready_p95_ms": p95,
        "six_ready_reads_total_ms": concurrent_ms, "health_equivalent_max_ms": max(probe_times),
        "actual_staff_bootstrap_max_ms": max(bootstrap_times), "child": metrics,
        "legacy_home_bytes": len(old.content), "ready_home_bytes": len(ready.content),
        "legacy_resources": len(old.json()["resources"]), "ready_resources": len(ready.json()["resources"]),
        "first_useful_target_met": cold_ms < 2000, "ready_target_met": p95 < 1000}
    if destination := os.environ.get("DEMAND_READ_BENCHMARK_REPORT"):
        Path(destination).write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print("CONTROLLED_DEMAND_READ " + json.dumps(evidence, ensure_ascii=False))
