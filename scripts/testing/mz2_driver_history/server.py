"""C2 local browser acceptance fixture; never imports the production server.

Seed native decisions before a full-database fingerprint, then expose GET only.
The sole test database comes from the existing UUID-isolated manual-POS fixture.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient

uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
parsed = urlparse(uri)
if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username or parsed.password or not parse_qs(parsed.query).get("replicaSet")):
    raise RuntimeError("An unauthenticated disposable loopback Mongo replica set is mandatory")
DIST = Path(os.environ["MZ2_HISTORY_DIST"]).resolve()

from accounting_atomic import atomic_owner
from accounting_ledger_v2 import reverse_journal_v2
from accounting_shipping_native_contract import EVENTS
from accounting_shipping_native_routes import install_shipping_native_routes
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
from store_delivery_payment_resubmission_routes import make_store_delivery_payment_resubmission_router
import test_mz2_driver_pos_manual_review as fixture_module
from test_mz2_driver_review_history import decided, resubmit_rejected
from test_mz2_driver_pos_manual_review import snapshot, approval, post
from test_mz2_shipping_native import OWNER


async def fingerprint(db):
    data = await snapshot(db)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def seed_gap(ctx):
    assignment, prior, _ = await decided(ctx, "gap", decision="rejected")
    await ctx.db.store_delivery_assignments.update_one({"id": assignment, "user_id": OWNER},
        {"$set": {"active": True}})
    content = b"Synthetic missing-history revision replacement"
    await ctx.db.store_delivery_receipts.insert_one({"user_id": OWNER, "driver_id": "driver-f",
        "assignment_id": assignment, "token": "gap-replacement", "status": "uploaded",
        "content": content, "sha256": hashlib.sha256(content).hexdigest()})
    child = FastAPI()

    async def driver():
        return {"id": "history-driver", "role": "store_driver", "created_by": OWNER}

    child.include_router(make_store_delivery_payment_resubmission_router(ctx.db, driver))
    async with AsyncClient(transport=ASGITransport(child), base_url="http://isolated-seed") as http:
        result = await http.post("/store-delivery/app/payment-review/" + assignment + "/resubmit",
                                json={"receipt_reference": "gap-replacement"})
        if result.status_code != 200:
            raise RuntimeError(result.text)
    # Explicit synthetic fault injection before the browser fingerprint.
    await ctx.db[EVENTS].delete_one({"user_id": OWNER, "_id": prior["_id"]})
    return prior["review_id"]


@asynccontextmanager
async def lifespan(app):
    # Instrument the fixture's existing Mongo monitor; no financial seam is mocked.
    original_monitor = fixture_module.NoLegacy
    monitors = []

    class ObservedMonitor(original_monitor):
        def __init__(self):
            super().__init__()
            monitors.append(self)

    fixture_module.NoLegacy = ObservedMonitor
    fixture = fixture_module.manual.__wrapped__()
    try:
        native = await anext(fixture)
        ctx = SimpleNamespace(native=native, db=native.db)
        # Fifty-one ordinary rejections force actual UI limit=50 pagination.
        for number in range(51):
            await decided(ctx, "older-" + str(number), decision="rejected")
        assignment, rejection, _ = await decided(ctx, "1", decision="rejected")
        await resubmit_rejected(ctx, assignment)
        approved = await post(native, assignment, approval(native))
        if approved.status_code != 200:
            raise RuntimeError(approved.text)
        _, bank, _ = await decided(ctx, "bank", method="bank_transfer")
        _, reversed_event, _ = await decided(ctx, "reversed")
        at = datetime.now(timezone.utc).isoformat()

        async def reverse(scoped):
            return await reverse_journal_v2(scoped._db, user_id=OWNER, actor_id=OWNER,
                actor_name=OWNER, original_txn_group_id=reversed_event["txn_group_id"],
                effective_at=at, reason="Synthetic existing reversal for history browser proof",
                mongo_session=scoped._session)

        await atomic_owner(native.db, OWNER, reverse)
        gap_id = await seed_gap(ctx)
        for identity in ("c2-history-viewer", "c2-history-revoked"):
            await native.db.users.insert_one({"id": identity, "role": "accountant", "created_by": OWNER,
                "is_active": True, "accounting_permissions": ["accounting.shipping.view"]})
        # Revoke the persisted grant while synthetic auth later retains old claims.
        await native.db.users.update_one({"id": "c2-history-revoked"},
            {"$set": {"accounting_permissions": ["accounting.home.view"]}})
        await native.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
        baseline = await fingerprint(native.db)

        async def synthetic_auth(request: Request):
            actor_id = "c2-history-revoked" if request.headers.get("x-synthetic-actor") == "revoked" else "c2-history-viewer"
            return {"id": actor_id, "role": "accountant", "created_by": OWNER,
                    "accounting_permissions": ["accounting.shipping.view"]}

        router = APIRouter()
        install_shipping_native_routes(router, native.db, synthetic_auth)
        app.include_router(router, prefix="/api")
        app.include_router(make_store_delivery_payment_review_router(native.db, synthetic_auth), prefix="/api")
        app.state.http = []

        @app.get("/__test/proof")
        async def proof():
            actual = await fingerprint(native.db)
            return {"synthetic_only": True, "database": native.db.name,
                "baseline_hash": baseline, "current_hash": actual, "all_collections_unchanged": actual == baseline,
                "legacy_accesses": [entry for monitor in monitors for entry in monitor.accesses],
                "owner": OWNER, "pos_identity": native.fact["id"], "pos_name": native.fact["display_name"],
                "rejected_event": rejection["_id"], "pos_group": approved.json()["txn_group_id"],
                "bank_group": bank["txn_group_id"], "reversed_group": reversed_event["txn_group_id"],
                "gap_review_id": gap_id, "expected_native_decisions": 55,
                "http": app.state.http, "financial_browser_writes": 0,
                "production_smoke_b": "NOT_PERFORMED", "full_16_stage_business_uat": "NOT_PERFORMED"}

        app.mount("/", StaticFiles(directory=DIST, html=True), name="isolated-c2-ui")
        yield
        print(json.dumps({"shutdown_database_unchanged": await fingerprint(native.db) == baseline,
                          "legacy_accesses": [entry for monitor in monitors for entry in monitor.accesses]}), flush=True)
        try:
            await anext(fixture)
        except StopAsyncIteration:
            pass
    finally:
        await fixture.aclose()
        fixture_module.NoLegacy = original_monitor


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def read_only_loopback(request: Request, call_next):
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return JSONResponse({"detail": "loopback_fixture_only"}, status_code=403)
    if request.method not in {"GET", "HEAD"}:
        return JSONResponse({"detail": "browser_fixture_read_only"}, status_code=405)
    response = await call_next(request)
    if hasattr(app.state, "http") and request.url.path.startswith("/api/"):
        app.state.http.append({"method": request.method, "path": request.url.path,
            "query": request.url.query, "status": response.status_code,
            "actor": request.headers.get("x-synthetic-actor", "viewer")})
    return response


async def main():
    import uvicorn
    port = int(os.environ.get("MZ2_HISTORY_PORT", "18769"))
    stop_file = Path(os.environ["MZ2_HISTORY_STOP_FILE"])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    async def shutdown_watch():
        while not server.should_exit:
            if stop_file.exists():
                server.should_exit = True
                return
            await asyncio.sleep(0.25)

    watcher = asyncio.create_task(shutdown_watch())
    try:
        await server.serve()
    finally:
        server.should_exit = True
        await watcher


if __name__ == "__main__":
    asyncio.run(main())
