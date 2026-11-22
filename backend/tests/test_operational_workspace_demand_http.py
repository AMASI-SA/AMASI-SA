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
async def test_statement_reassembles_full_period_and_report_binds_fresh_bootstrap(read_db):
    db = read_db
    before = await seed_workspace(db, suppliers=2, movements=360)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://isolated") as client:
        path = BASE + "/statements/bank/bank"
        period = {"date_from": "2026-11-02", "date_to": "2026-11-21", "purpose": "view"}
        canonical = await client.get(path, params=period)
        assert canonical.status_code == 200, canonical.text
        assert len(canonical.json()["items"]) > 300
        pending = await client.get(path, params={**period, "demand": True}, headers=HEADERS)
        assert pending.status_code == 202, pending.text
        assert await settle(db) is True
        ready = await client.get(path, params={**period, "demand": True}, headers=HEADERS)
        assert ready.status_code == 200, ready.text
        for key, value in canonical.json().items():
            if key != "generated_at":
                assert ready.json()[key] == value, key
        bootstrap = (await client.get(BASE + "/bootstrap")).json()
        report_path = BASE + "/report"
        report_query = {"view": "summary"}
        canonical = await client.get("/api/operational-balances/financial-position", params=report_query)
        assert canonical.status_code == 200, canonical.text
        assert (await client.get(report_path, params=report_query, headers=HEADERS)).status_code == 202
        assert await settle(db) is True
        report = await client.get(report_path, params=report_query, headers=HEADERS)
        assert report.status_code == 200, report.text
        for key in ("session_scope", "permission_version", "owner_override"):
            assert report.json()[key] == bootstrap[key]
        for key, value in canonical.json().items():
            if key != "generated_at":
                assert report.json()[key] == value, key
        # A stale forecast/report request must reject the known mismatch before
        # starting a heavy job, preserving the existing snapshot-binding error.
        await db[STATES].update_one({"_id": "owner"}, {"$inc": {"revision": 1}})
        rejected = await client.get(report_path, params={**report_query, "expected_revision": 5,
            "expected_started_at": before["started_at"], "expected_as_of": "2026-11-21T00:00:00+00:00"}, headers=HEADERS)
        assert rejected.status_code == 409, rejected.text
    after = await db[STATES].find_one({"_id": "owner"})
    after["revision"] = before["revision"]
    assert after == before


@pytest.mark.asyncio
async def test_ready_position_and_release_are_actor_scoped_and_do_not_write_money(read_db):
    db = read_db
    before = await seed_workspace(db, suppliers=30, movements=40)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://isolated") as client:
        pending = await client.get(BASE + "/position", headers=HEADERS)
        assert pending.status_code == 202, pending.text
        key = pending.json()["key"]
        assert await settle(db) is True
        response = await client.get(BASE + "/position", params={"page": 2}, headers=HEADERS)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["page_size"] == 24 and len(body["items"]) <= 24
        assert body["summary"]["available_cash"]["SAR"] == "1000496.00"
        assert body["report"]["view"] == "summary"
        before_job = await db[store.JOBS].find_one({"_id": key})
        other = await client.delete(BASE + "/read-demand/" + key,
            params={"consumer_id": HEADERS["X-Mezan-Read-Consumer"]}, headers={"x-test-user": "other"})
        assert other.status_code == 200
        assert await db[store.JOBS].find_one({"_id": key}) == before_job
        # A genuinely unused cold demand stops without computing a report.
        pending = await client.get(BASE + "/directory", params={"group": "parties", "kind": "supplier"}, headers=HEADERS)
        assert pending.status_code == 202
        result = await client.delete(BASE + "/read-demand/" + pending.json()["key"],
            params={"consumer_id": HEADERS["X-Mezan-Read-Consumer"]})
        assert result.status_code == 200
        assert await settle(db) is False
    assert await db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_same_data_twenty_filtered_pages_reuse_one_report_and_source_read(read_db, monkeypatch):
    db = read_db
    before = await seed_workspace(db, suppliers=200, movements=5000)
    import operational_financial_position_state as financial_source
    counts = {"reports": 0, "financial_snapshots": 0}
    report, read_state = workspace.report, financial_source.read_financial_position_state
    def counted_report(*args, **kwargs):
        counts["reports"] += 1
        return report(*args, **kwargs)
    async def counted_read(*args, **kwargs):
        counts["financial_snapshots"] += 1
        return await read_state(*args, **kwargs)
    monkeypatch.setattr(workspace, "report", counted_report)
    monkeypatch.setattr(financial_source, "read_financial_position_state", counted_read)
    filters = {"q": "مرجع", "date_from": "2026-11-03", "date_to": "2026-11-20", "page_size": 8}
    path = BASE + "/movements"
    legacy_times, ready_times, canonical = [], [], []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://isolated") as client:
        for page in range(1, 21):
            stamp = time.perf_counter()
            response = await client.get(path, params={**filters, "page": page})
            legacy_times.append((time.perf_counter() - stamp) * 1000)
            assert response.status_code == 200, response.text
            canonical.append(response.json())
        baseline = dict(counts)
        assert baseline == {"reports": 20, "financial_snapshots": 20}
        counts.update(reports=0, financial_snapshots=0)
        cold_start = time.perf_counter()
        pending = await asyncio.gather(*[client.get(path, params={**filters, "demand": True, "page": i + 1},
            headers={"X-Mezan-Read-Consumer": f"register_benchmark_{i}"}) for i in range(6)])
        assert all(r.status_code == 202 for r in pending), [r.text for r in pending]
        assert len({r.json()["key"] for r in pending}) == 1
        assert counts == {"reports": 0, "financial_snapshots": 0}
        assert await settle(db) is True
        cold_build_ms = (time.perf_counter() - cold_start) * 1000
        for page in range(1, 21):
            stamp = time.perf_counter()
            response = await client.get(path, params={**filters, "demand": True, "page": page}, headers=HEADERS)
            ready_times.append((time.perf_counter() - stamp) * 1000)
            assert response.status_code == 200, response.text
            for field in ("items", "total", "page", "page_size", "total_pages"):
                assert response.json()[field] == canonical[page - 1][field]
        assert counts == {"reports": 1, "financial_snapshots": 1}
        stamp = time.perf_counter()
        results = await asyncio.gather(*[client.get(path, params={**filters, "demand": True, "page": i + 1},
            headers=HEADERS) for i in range(6)])
        concurrent_ms = (time.perf_counter() - stamp) * 1000
        assert all(r.status_code == 200 for r in results)
        assert counts == {"reports": 1, "financial_snapshots": 1}
    assert await db[STATES].find_one({"_id": "owner"}) == before
    p95 = lambda values: sorted(values)[math.ceil(.95 * len(values)) - 1]
    evidence = {"environment": "isolated loopback Mongo; ASGI; in-process builder test seam for exact work counts; no browser/network timing",
        "data": {"suppliers": 200, "movements": 5000, "pages": 20, "page_size": 8},
        "filters": filters, "legacy_work": baseline, "demand_work_including_initial_build": counts,
        "report_reduction_fraction": 1 - counts["reports"] / baseline["reports"],
        "legacy_ms": legacy_times, "ready_ms": ready_times,
        "legacy_p95_ms": p95(legacy_times), "ready_p95_ms": p95(ready_times),
        "cold_build_ms": cold_build_ms, "six_ready_reads_total_ms": concurrent_ms,
        "financial_results_equal": True, "source_unchanged": True}
    assert evidence["report_reduction_fraction"] >= .8
    assert evidence["ready_p95_ms"] < 1000
    if destination := os.environ.get("DEMAND_READ_REGISTER_BENCHMARK_REPORT"):
        Path(destination).write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print("CONTROLLED_REGISTER_READ " + json.dumps(evidence, ensure_ascii=False))


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
