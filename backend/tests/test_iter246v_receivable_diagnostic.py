"""Iter-246v — Receivable composition diagnostic.

Smoke test: the endpoint returns a structured breakdown of the
Tamara receivable ledger balance with drift detection.

Strict READ-ONLY — no writes performed.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


@pytest_asyncio.fixture
async def db_cli():
    c = AsyncIOMotorClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest_asyncio.fixture
async def ctx(db_cli):
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "iter246v", "email": f"iter246v-{suf}@x.com",
              "password": "pw1234567"},
    )
    body = r.json()
    uid = body["id"]
    yield {"uid": uid, "token": body["access_token"]}
    await db_cli.general_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_receivable_breakdown_empty_account(ctx):
    """Empty Tamara receivable returns 0 and no drift."""
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-receivable-breakdown",
        params={"provider": "tamara"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["iter"] == "iter246v"
    assert body["provider"] == "tamara"
    assert body["net_receivable_balance"] == 0
    assert body["pre_cutoff_refund_drift"]["count"] == 0
    assert body["pre_cutoff_refund_drift"]["sum"] == 0


@pytest.mark.asyncio
async def test_receivable_breakdown_is_read_only(ctx, db_cli):
    uid = ctx["uid"]
    gl_before = await db_cli.general_ledger.count_documents({"user_id": uid})
    requests.get(
        f"{BASE_URL}/api/audit/tamara-receivable-breakdown",
        params={"provider": "tamara"},
        headers=_h(ctx["token"]),
    )
    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == gl_before
