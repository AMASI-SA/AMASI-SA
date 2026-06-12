"""Iter-151d — Regression tests for the data-hygiene endpoint
POST /api/liabilities/admin/cleanup-stale-partial.

The endpoint fixes legacy rows that have `status='partial'` but
`paid_amount >= expected_amount`. These rows block the pay-liability
dropdown by leaking into `openLiabilities` with `remaining=0`.
"""
import os
import uuid

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _mdb():
    load_dotenv("/app/backend/.env")
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"i151d-{suffix}@example.com"
    pwd = "T#151d"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I151d"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb()}


def _inject_stale(ctx, **overrides):
    """Insert a liability row directly with stale partial status."""
    row = {
        "id": str(uuid.uuid4()),
        "user_id": ctx["uid"],
        "kind": "salary",
        "employee_salary_id": "test-emp-" + uuid.uuid4().hex[:6],
        "period_key": "2024-" + uuid.uuid4().hex[:6],
        "expected_amount": 3000.0,
        "paid_amount": 3000.0,  # equal to expected → should be paid
        "status": "partial",  # but flagged partial → STALE
        "description": "stale",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    row.update(overrides)
    ctx["db"].liabilities.insert_one(row)
    return row["id"]


def test_dry_run_does_not_mutate(ctx):
    sid = _inject_stale(ctx)
    r = requests.post(
        f"{BASE_URL}/api/liabilities/admin/cleanup-stale-partial?dry_run=true",
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["candidates_found"] >= 1
    assert body["updated"] == 0
    # Row still partial in DB
    row = ctx["db"].liabilities.find_one({"id": sid, "user_id": ctx["uid"]})
    assert row["status"] == "partial"


def test_cleanup_fixes_stale_partial(ctx):
    sid1 = _inject_stale(ctx, expected_amount=3000.0, paid_amount=3000.0)
    sid2 = _inject_stale(ctx, expected_amount=4200.0, paid_amount=4200.01)  # rounding
    # Inject a non-stale row that should NOT be touched
    healthy = _inject_stale(ctx, expected_amount=5000.0, paid_amount=2000.0)

    r = requests.post(
        f"{BASE_URL}/api/liabilities/admin/cleanup-stale-partial",
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] >= 2  # both stale rows fixed
    assert body["candidates_found"] >= 2

    # Stale rows now `paid`
    assert ctx["db"].liabilities.find_one({"id": sid1})["status"] == "paid"
    assert ctx["db"].liabilities.find_one({"id": sid2})["status"] == "paid"
    # Healthy row UNTOUCHED
    assert ctx["db"].liabilities.find_one({"id": healthy})["status"] == "partial"


def test_cleanup_no_candidates(ctx):
    """When the merchant has zero stale rows, endpoint returns 0/0."""
    r = requests.post(
        f"{BASE_URL}/api/liabilities/admin/cleanup-stale-partial",
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["candidates_found"] == 0
    assert body["updated"] == 0


def test_cleanup_is_user_scoped(ctx):
    """Stale rows belonging to ANOTHER user must NOT be touched."""
    other_uid = "other-user-" + uuid.uuid4().hex[:6]
    foreign_id = str(uuid.uuid4())
    ctx["db"].liabilities.insert_one({
        "id": foreign_id, "user_id": other_uid, "kind": "salary",
        "expected_amount": 1000.0, "paid_amount": 1000.0,
        "status": "partial", "description": "foreign", "created_at": "x",
        "updated_at": "x",
    })
    # Run cleanup as ctx user — must NOT affect foreign row
    requests.post(
        f"{BASE_URL}/api/liabilities/admin/cleanup-stale-partial",
        headers=ctx["hdr"], timeout=10,
    )
    foreign = ctx["db"].liabilities.find_one({"id": foreign_id})
    assert foreign["status"] == "partial"
    # Cleanup leak prevention check
    ctx["db"].liabilities.delete_one({"id": foreign_id, "user_id": other_uid})
