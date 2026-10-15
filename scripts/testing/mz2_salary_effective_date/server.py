"""Loopback-only, disposable salary setup UI/API proof. No accounting routes."""
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
import employees_v2_routes as routes
import employee_payroll_status as payroll

URI = os.environ["MZ2_TEST_MONGO_URI"]
if not URI.startswith("mongodb://127.0.0.1:"):
    raise RuntimeError("Only disposable loopback Mongo is allowed")
DIST = Path(os.environ["MZ2_FIXTURE_DIST"]).resolve()
OWNER = {"id": "synthetic-salary-owner", "role": "owner", "name": "Synthetic owner"}
client = AsyncIOMotorClient(URI)
db = client["salary_ui_proof_" + uuid4().hex]
routes.riyadh_today = payroll.riyadh_today = lambda: date(2026, 10, 5)
router = routes.make_employees_v2_router(db, lambda: OWNER)


@asynccontextmanager
async def lifespan(app):
    assert (await db.command("hello")).get("setName")
    await db.mz2_atomic_owners.insert_one({"_id": OWNER["id"], "revision": 0, "writes_paused": True})
    create = next(r.endpoint for r in router.routes if r.path == "/employees-v2/management/employees" and "POST" in r.methods)
    for name, effective in [("موظف اختباري — مجدول", "2026-10-31"), ("موظف اختباري — يبدأ اليوم", "2026-10-05")]:
        await create({"confirmation": routes.EMPLOYEE_CREATE_CONFIRMATION, "name": name,
                      "hire_date": "2026-10-01", "status": "active", "monthly_salary": 1500,
                      "salary_effective_date": effective}, OWNER)
    print(f"SYNTHETIC_DATABASE={db.name}", flush=True)
    yield
    # Only this process's random disposable database is cleaned up.
    await client.drop_database(db.name)
    client.close()


app = FastAPI(lifespan=lifespan)
app.include_router(router, prefix="/api")
app.mount("/", StaticFiles(directory=DIST, html=True), name="fixture")
