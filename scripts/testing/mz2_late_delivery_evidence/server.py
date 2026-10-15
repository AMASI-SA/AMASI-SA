"""Loopback evidence-only acceptance. Synthetic auth passes through real native route context."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse, parse_qs
from bson import json_util
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from mobile_app_request_context import mobile_app_request_user
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router
from store_delivery_late_evidence import install_review_routes, EVENTS
import test_mz2_shipping_native as native
from test_mz2_late_delivery_evidence import late, seed_delivery, DRIVER, REVIEWER
uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
parsed = urlparse(uri)
if parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or not parse_qs(parsed.query).get("replicaSet"):
    raise RuntimeError("Unauthenticated disposable loopback replica required")
DIST = Path(os.environ["MZ2_LATE_DIST"]).resolve()
DRIVER_PATH = "/api/store-delivery/evidence/late-delivery"
REVIEW_PATH = "/api/accounting-module/shipping-v2/late-delivery-evidence"
async def snapshot(db):
    result = {}
    for name in await db.list_collection_names():
        if name == EVENTS:
            continue
        rows = await db[name].find({}).to_list(None)
        if name == "store_delivery_delivery_proofs":
            rows = [row for row in rows if row.get("origin") != "late_attachment"]
        if name == "mz2_atomic_owners":
            rows = [{key: value for key, value in row.items() if key != "revision"} for row in rows]
        result[name] = sorted(json_util.dumps(row, sort_keys=True) for row in rows)
    return result

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

@asynccontextmanager
async def lifespan(app):
    generator = native.db.__wrapped__()
    context = None
    original_monitor = native.NoLegacy
    monitors = []
    class Monitor(original_monitor):
        def __init__(self):
            super().__init__(); monitors.append(self)
    native.NoLegacy = Monitor
    try:
        database = await anext(generator)
        context = late.__wrapped__(database)
        await anext(context)
        await seed_delivery(database, "1", original=True)
        await seed_delivery(database, "2", absent_c3=True)
        await database.users.insert_one({"id":"late-viewer", "role":"employee", "created_by":native.OWNER, "is_active":True, "accounting_permissions":["accounting.shipping.view"]})
        await database.mz2_atomic_owners.update_one({"_id":native.OWNER},{"$set":{"writes_paused":True}})
        baseline = await snapshot(database)
        async def actor(request: Request):
            identity = {"driver":DRIVER,"reviewer":REVIEWER,"viewer":"late-viewer"}.get(request.headers.get("x-synthetic-actor", "driver"))
            if not identity: raise HTTPException(403, "synthetic_actor_required")
            user = await database.users.find_one({"id":identity},{"_id":0})
            if identity == DRIVER: user["_session_client"] = "amasi_mobile"
            return await mobile_app_request_user(database,user,path=request.url.path,method=request.method)
        app.include_router(make_store_delivery_payment_evidence_router(database,actor),prefix="/api")
        router = APIRouter()
        install_review_routes(router,database,actor)
        app.include_router(router,prefix="/api")
        app.state.http = []
        @app.get("/__test/proof")
        async def proof():
            current = await snapshot(database)
            events = await database[EVENTS].find({},{"_id":0}).to_list(None)
            return {"synthetic_only":True,"database":database.name,"baseline_hash":digest(baseline),"current_hash":digest(current),
                "financial_controls_originals_c3_unchanged":current == baseline,
                "write_control":await database.mz2_atomic_owners.find_one({"_id":native.OWNER},{"_id":0}),
                "changed_collections":[name for name in set(baseline)|set(current) if baseline.get(name)!=current.get(name)],
                "attachment_count":sum(row.get("kind")=="attachment" for row in events),"review_count":sum(row.get("kind")=="review" for row in events),
                "legacy_accesses":[row for monitor in monitors for row in monitor.accesses],"http":app.state.http,
                "excluded_from_fingerprint":[EVENTS,"new proof rows with origin=late_attachment","mz2_atomic_owners.revision only"],"production_writes":0}
        app.mount("/",StaticFiles(directory=DIST,html=True))
        yield
        print(json.dumps({"shutdown_integrity":await snapshot(database)==baseline,"database":database.name,"legacy_accesses":[row for monitor in monitors for row in monitor.accesses]}),flush=True)
    finally:
        if context: await context.aclose()
        await generator.aclose()
        native.NoLegacy = original_monitor

app = FastAPI(lifespan=lifespan)
@app.middleware("http")
async def boundary(request: Request, call_next):
    path = request.url.path
    if request.url.hostname not in {"127.0.0.1","localhost","::1"}:
        return JSONResponse({"detail":"loopback_only"},403)
    api_read = path == DRIVER_PATH or path == REVIEW_PATH or re.fullmatch(re.escape(REVIEW_PATH)+r"/[^/]+/original",path)
    allowed_post = path == DRIVER_PATH or re.fullmatch(re.escape(REVIEW_PATH)+r"/[^/]+/review",path)
    if request.method not in {"GET","HEAD"} and not (request.method == "POST" and allowed_post):
        return JSONResponse({"detail":"evidence_only_write_boundary"},405)
    if path.startswith("/api/") and request.method in {"GET","HEAD"} and not api_read:
        return JSONResponse({"detail":"evidence_only_read_boundary"},405)
    response = await call_next(request)
    if path.startswith("/api/") and hasattr(app.state,"http"):
        app.state.http.append({"method":request.method,"path":path,"status":response.status_code})
    return response

async def main():
    import uvicorn
    stop = Path(os.environ["MZ2_LATE_STOP_FILE"])
    if stop.exists(): raise RuntimeError("Use a new stop-file path")
    server = uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=int(os.environ.get("MZ2_LATE_PORT","18772")),log_level="warning"))
    async def watch():
        while not server.should_exit:
            if stop.exists(): server.should_exit=True; return
            await asyncio.sleep(.25)
    task=asyncio.create_task(watch())
    try: await server.serve()
    finally:
        server.should_exit=True
        await task
if __name__ == "__main__": asyncio.run(main())
