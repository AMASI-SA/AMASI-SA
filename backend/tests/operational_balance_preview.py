"""Local-only synthetic preview. Never imports server or reads deployment env.

Run with uvicorn tests.operational_balance_preview:app --host 127.0.0.1
or PYTHONPATH=backend/tests:backend uvicorn operational_balance_preview:app.
Uses a fixed dedicated local Mongo namespace and no production credentials.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from operational_balance_routes import make_operational_balance_router
from operational_balance_worker import run
import asyncio

client = AsyncIOMotorClient("mongodb://127.0.0.1:27305", serverSelectionTimeoutMS=3000)
db = client.operational_balance_preview_20261005


@asynccontextmanager
async def lifespan(app):
    await db.users.update_one({"id":"preview-owner"}, {"$setOnInsert":{
        "id":"preview-owner","role":"owner","is_active":True}}, upsert=True)
    for row in (
        {"id":"preview-bank","name":"الراجحي — حساب تجريبي","account_type":"bank"},
        {"id":"preview-cash","name":"صندوق المتجر — تجريبي","account_type":"cash"},
    ):
        await db.mz2_financial_accounts.update_one({"id":row["id"],"user_id":"preview-owner"}, {"$setOnInsert":{
            **row,"user_id":"preview-owner","currency":"SAR","status":"active"}},upsert=True)
    await db.mezan_suppliers_v2.update_one({"id":"preview-supplier","user_id":"preview-owner"},{"$setOnInsert":{
        "id":"preview-supplier","user_id":"preview-owner","company_name":"مورد المنتجات — تجريبي","status":"active"}},upsert=True)
    await db.mezan_employees_v2.update_one({"id":"preview-employee","user_id":"preview-owner"},{"$setOnInsert":{
        "id":"preview-employee","user_id":"preview-owner","display_name":"موظف تجريبي","status":"active"}},upsert=True)
    task=asyncio.create_task(run(db, interval=2))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        client.close()


app=FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["http://127.0.0.1:5178"],allow_credentials=True,allow_methods=["GET","POST"],allow_headers=["*"])


async def fixture_owner():
    return {"id":"preview-owner"}


app.include_router(make_operational_balance_router(db,fixture_owner),prefix="/api")
