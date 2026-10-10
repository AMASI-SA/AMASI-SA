"""Real loopback Mezan 2 save sequence; baseline/current timing, no Production."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorCollection
import supplier_receiving_routes as current_routes
from test_supplier_native_invoice_v2 import env, identities, mapping, receiving_session, ACTOR
from test_supplier_refresh_wrapper import installed_live_cost_support


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 10, 50])
@pytest.mark.parametrize("revision", ["baseline", "current"])
async def test_save_sequence_profile(env, monkeypatch, installed_live_cost_support, count, revision):
    db, commands = env
    r = current_routes
    if revision == "baseline":
        root = Path(__file__).resolve().parents[2]
        source = subprocess.check_output(["git", "show", "d55b1b86:backend/supplier_receiving_routes.py"], cwd=root, text=True, encoding="utf-8")
        spec = importlib.util.spec_from_loader("supplier_receiving_routes", loader=None)
        r = importlib.util.module_from_spec(spec)
        r.__file__ = str(root / "backend/supplier_receiving_routes.py")
        monkeypatch.setitem(sys.modules, "supplier_receiving_routes", r)
        exec(compile(source, r.__file__, "exec"), r.__dict__)
    await identities(db); await mapping(db)
    session, payload = await receiving_session(db)
    original_piece = await db[r.PIECES].find_one({"piece_id": "piece"}, {"_id": 0})
    original_event = await db[r.RECEIVING_EVENTS].find_one({"id": "scan-event"}, {"_id": 0})
    for i in range(1, count):
        piece = {**deepcopy(original_piece), "piece_id": f"piece-{i}", "receipt_event_id": f"scan-{i}", "unit_index": i + 1}
        event = {**deepcopy(original_event), **piece, "id": piece["receipt_event_id"]}
        for collection, row in [(r.PIECES, piece), (r.RECEIVING_EVENTS, event), (r.PIECE_EVENTS, event)]:
            await db[collection].insert_one(deepcopy(row))
        payload["invoice_lines"][0]["piece_ids"].append(piece["piece_id"])
    payload["confirmed_total_halalas"] *= count
    await db[r.SESSIONS].update_one({"id": session["id"]}, {"$set": {"scan_count": count}})
    installed_live_cost_support()
    async def current(): return deepcopy(ACTOR)
    app = FastAPI(); app.include_router(r.make_supplier_receiving_router(db, current))
    stages = {}; index_calls = 0; index_seconds = 0
    phase = "setup"; index_stages = {}
    create_index = AsyncIOMotorCollection.create_index
    async def counted(collection, *args, **kwargs):
        nonlocal index_calls, index_seconds
        index_calls += 1; started = perf_counter()
        try: return await create_index(collection, *args, **kwargs)
        finally:
            elapsed = perf_counter() - started
            index_seconds += elapsed
            entry = index_stages.setdefault(phase, {"calls": 0, "ms": 0})
            entry["calls"] += 1; entry["ms"] += elapsed * 1000
    monkeypatch.setattr(AsyncIOMotorCollection, "create_index", counted)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as client:
        async def request(stage, method, path, **kwargs):
            nonlocal phase
            phase = stage
            started = perf_counter()
            response = await client.request(method, "/supplier-receiving-v1/" + path, **kwargs)
            stages[stage] = round((perf_counter() - started) * 1000, 3)
            assert response.status_code == 200, response.text
            return response.json()
        await request("catalog", "GET", "catalog")
        await request("refresh", "POST", f"sessions/{session['id']}/refresh", json={})
        await request("session_before_close", "GET", f"sessions/{session['id']}")
        closed = await request("close", "POST", f"sessions/{session['id']}/close", json=payload)
        await request("session_after_close", "GET", f"sessions/{session['id']}")
        saved = await request("invoice_read", "GET", f"invoices/{closed['supplier_invoice']['id']}")
        assert saved["supplier_invoice"]["total_halalas"] == payload["confirmed_total_halalas"]
        assert saved["supplier_invoice"]["financial_integrity_verified"] is True
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
        phase = "close_replay"
        replay = await client.post(f"/supplier-receiving-v1/sessions/{session['id']}/close", json=payload)
        assert replay.status_code == 200 and replay.json()["supplier_invoice"]["id"] == saved["supplier_invoice"]["id"]
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
    assert index_calls == (36 if revision == "baseline" else 12)
    print("SAVE_PROFILE", json.dumps({"revision": revision, "pieces": count, "ms": stages,
          "indexes_by_stage": index_stages, "index_calls_including_replay": index_calls, "index_ms_including_replay": round(index_seconds * 1000, 3)}, sort_keys=True))
