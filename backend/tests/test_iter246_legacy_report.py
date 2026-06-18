"""Iter-246 — Legacy usage report endpoint smoke tests.

Validates:
  • Endpoint returns expected shape.
  • Empty-data user → all zeros, all dead/empty.
  • Inserting a fresh row into `purchase_invoices` flips its
    `is_active` to True.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def auth():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter246lr-{suffix}@x.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": email,
                            "password": "pw1234567"})
    assert r.status_code == 200, r.text
    body = r.json()
    return {"token": body["access_token"],
            "uid": body["id"]}


def test_legacy_report_shape_empty_user(auth):
    h = _h(auth["token"])
    r = requests.get(f"{BASE_URL}/api/legacy-usage-report", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["iter"] == "iter246"
    assert isinstance(body["screens"], list) and len(body["screens"]) == 4
    names = [s["screen"] for s in body["screens"]]
    assert set(names) == {
        "purchase_invoices", "daily_costs",
        "operating_expenses", "financial_input_hub",
    }
    # Fresh user → all zeros, no actives.
    for s in body["screens"]:
        assert s["total_records"] == 0
        assert s["is_active"] is False
    assert body["summary"]["total_legacy_records"] == 0
    assert body["summary"]["active_screens"] == []
    assert body["summary"]["dead_screens"] == []


@pytest.mark.asyncio
async def test_legacy_report_detects_fresh_record(auth):
    """Insert a purchase_invoice for the user and confirm the report
    flips is_active to True."""
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DB_NAME]
        await db.purchase_invoices.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": auth["uid"],
            "invoice_date": "2026-02-01",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        client.close()

    h = _h(auth["token"])
    r = requests.get(f"{BASE_URL}/api/legacy-usage-report", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    pi = next(s for s in body["screens"]
              if s["screen"] == "purchase_invoices")
    assert pi["total_records"] == 1
    assert pi["is_active"] is True
    assert pi["last_7d"] == 1
    assert "purchase_invoices" in body["summary"]["active_screens"]
