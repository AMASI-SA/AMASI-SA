"""C3 disposable actual-HTTP fixture. Synthetic auth/Salla, real native ledger.

Allows only delivery proof upload, delivered status and explicit metadata matching
on the unique fixture database. No production server import or general API proxy.
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pytest import MonkeyPatch
from accounting_shipping_native_routes import install_shipping_native_routes
from accounting_shipping_native_contract import EVENTS, EVIDENCE, SETUP
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router
import store_delivery_driver_app_routes as driver_routes
import test_mz2_driver_pos_manual_review as native_fixture
from test_mz2_driver_physical_cash import (cash as cash_fixture, confirmed_fixture, native_handover,
    operational_handover, prepare_delivery, financial_snapshot)
from test_mz2_driver_payment_review import driver_delivery
from test_mz2_driver_pos_manual_review import snapshot
from test_mz2_shipping_native import OWNER

uri = urlparse(os.environ.get("MZ2_TEST_MONGO_URI", ""))
if (uri.scheme != "mongodb" or uri.hostname not in {"127.0.0.1", "localhost", "::1"}
        or uri.username or uri.password or not parse_qs(uri.query).get("replicaSet")):
    raise RuntimeError("Disposable loopback Mongo replica set is mandatory")
DIST = Path(os.environ["MZ2_CASH_DIST"]).resolve()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def financial_fingerprint(db):
    finance = await financial_snapshot(db)
    all_rows = await snapshot(db)
    for name in (EVENTS, EVIDENCE, SETUP, "mz2_daily_movements", "mz2_financial_accounts",
                 "mz2_opening_facts_v2", "accounting_source_files"):
        finance[name] = all_rows.get(name, [])
    return digest(finance)


@asynccontextmanager
async def lifespan(app):
    monitors = []
    original = native_fixture.NoLegacy
    class Monitor(original):
        def __init__(self):
            super().__init__()
            monitors.append(self)
    native_fixture.NoLegacy = Monitor
    native = native_fixture.manual.__wrapped__()
    patch = MonkeyPatch()
    cash = None
    try:
        fixture = await anext(native)
        cash = cash_fixture.__wrapped__(fixture, patch)
        ctx = await anext(cash)
        seed = await confirmed_fixture(ctx, "native-seed", expected="200.00", actual="200.00")
        event = await native_handover(ctx, seed, value="200.00")
        await driver_delivery(ctx.db, "cash", "historical-missing")
        deliveries = []
        for number, expected in (("browser-1", "500.00"), ("browser-2", "300.00")):
            assignment, _ = await prepare_delivery(ctx, number, expected=expected)
            deliveries.append({"id": assignment, "order_number": number, "order_id": "salla-" + number,
                "barcode": number, "outstanding_amount": expected})
        handover = await operational_handover(ctx, key="browser-recorded-operational-handover", amount="450.00")
        await ctx.db.users.insert_one({"id": "c3-browser-accountant", "role": "accountant", "created_by": OWNER,
            "is_active": True, "accounting_permissions": ["accounting.shipping.view"]})
        baseline = await financial_fingerprint(ctx.db)
        baseline_all = digest(await snapshot(ctx.db))
        app.state.http = []
        app.state.faults = []

        async def accountant():
            return {"id": "c3-browser-accountant", "role": "accountant", "created_by": OWNER,
                    "accounting_permissions": ["accounting.shipping.view"]}
        async def driver():
            return dict(ctx.driver)

        router = APIRouter()
        install_shipping_native_routes(router, ctx.db, accountant)
        app.include_router(router, prefix="/api")
        app.include_router(driver_routes.make_store_delivery_driver_app_router(ctx.db, driver), prefix="/api")
        app.include_router(make_store_delivery_payment_evidence_router(ctx.db, driver), prefix="/api")

        @app.get("/__test/proof")
        async def proof():
            current = await financial_fingerprint(ctx.db)
            collections = await ctx.db.store_delivery_collections.find({"user_id": OWNER}, {"_id": 0}).to_list(None)
            links = await ctx.db.mz2_driver_cash_reconciliations_v1.find({"user_id": OWNER}, {"_id": 0}).to_list(None)
            return {"synthetic_only": True, "database": ctx.db.name, "financial_baseline_hash": baseline,
                "financial_current_hash": current, "financial_unchanged": current == baseline,
                "full_database_baseline_hash": baseline_all, "full_database_current_hash": digest(await snapshot(ctx.db)),
                "write_control": await ctx.db.mz2_atomic_owners.find_one({"_id": OWNER}, {"_id": 0}),
                "legacy_accesses": [entry for monitor in monitors for entry in monitor.accesses],
                "deliveries": deliveries, "collections": collections, "links": links,
                "native_source_id": event["_id"], "native_group": event["txn_group_id"],
                "operational_source_id": handover["id"], "seed_collection_id": seed["id"],
                "salla_transport": "explicit_test_only_AsyncMock", "salla_calls": ctx.salla.await_count,
                "http": app.state.http, "synthetic_faults": app.state.faults,
                "production_writes": 0, "smoke_b": "NOT_PERFORMED", "full_16_stage_uat": "NOT_PERFORMED"}

        @app.post("/__test/cancel-second-assignment")
        async def cancel_fixture():
            # Explicit synthetic source-change injection; not a business cancel flow.
            result = await ctx.db.store_delivery_assignments.update_one({"user_id": OWNER,
                "id": "physical-assignment-browser-2", "status": "delivered"}, {"$set": {"status": "cancelled"}})
            app.state.faults.append({"kind": "existing_assignment_status_changed", "matched": result.matched_count})
            return {"synthetic_fault_only": True, "matched": result.matched_count}

        app.mount("/", StaticFiles(directory=DIST, html=True), name="isolated-c3-ui")
        yield
        print(json.dumps({"database": ctx.db.name, "shutdown_financial_unchanged": await financial_fingerprint(ctx.db) == baseline,
            "legacy_accesses": [entry for monitor in monitors for entry in monitor.accesses]}), flush=True)
    finally:
        if cash is not None:
            await cash.aclose()
        await native.aclose()
        patch.undo()
        native_fixture.NoLegacy = original


app = FastAPI(lifespan=lifespan)
ALLOWED_POSTS = {"/api/store-delivery/evidence/delivery-proof", "/api/store-delivery/app/deliveries/status",
    "/api/accounting-module/shipping-v2/driver-cash/driver-f/reconciliations", "/__test/cancel-second-assignment"}


@app.middleware("http")
async def isolated_routes(request: Request, call_next):
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return JSONResponse({"detail": "loopback_fixture_only"}, status_code=403)
    if request.method not in {"GET", "HEAD"} and (request.method != "POST" or request.url.path not in ALLOWED_POSTS):
        return JSONResponse({"detail": "fixture_write_not_allowlisted"}, status_code=405)
    response = await call_next(request)
    if hasattr(app.state, "http") and (request.url.path.startswith("/api/") or request.method == "POST"):
        app.state.http.append({"method": request.method, "path": request.url.path, "status": response.status_code})
    return response


async def main():
    import uvicorn
    stop = Path(os.environ["MZ2_CASH_STOP_FILE"])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.environ.get("MZ2_CASH_PORT", "18770")), log_level="warning"))
    async def watch():
        while not server.should_exit:
            if stop.exists():
                server.should_exit = True
                return
            await asyncio.sleep(0.25)
    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        server.should_exit = True
        await watcher


if __name__ == "__main__":
    asyncio.run(main())
