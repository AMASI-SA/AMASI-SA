"""Iter-251 v8 — HTTP integration test for the calendar endpoints.

Validates the real-world UUID-entity + missing-period-metadata
scenario through the public API:

  1. Login as admin.
  2. Seed two ``general_ledger`` Tabby settlements directly in
     Mongo under the admin user_id (UUID entity_id, NO period_from,
     NO period_to, only metadata.provider + settlement_reference).
  3. POST /api/settlement-engine/calendar/rebuild?provider=tabby.
       → from_registered == 2
  4. GET  /api/settlement-engine/calendar?provider=tabby
       → contiguous Mon→Sun, no overlap.
  5. GET  /api/settlement-engine/calendar/audit?provider=tabby
       → match_type='by_reference' for both rows.
  6. GET  /api/settlement-engine/calendar/diagnose?provider=tabby
       → from_registered_extracted == 2 (despite UUID entity_id).
  7. Edge case: pre-filled period_from/period_to is preserved (no
     cross-row override).
  8. Cleanup all seeded docs.
"""
import os
import sys
import uuid

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..",
                         "frontend", ".env"))

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "admin@hesab.app"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    assert token, f"No token in login response: {data}"
    s.headers.update({"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"})
    me = s.get(f"{BASE_URL}/api/auth/me", timeout=15).json()
    s.admin_id = me.get("id") or me.get("user", {}).get("id")
    assert s.admin_id, f"Could not resolve admin id: {me}"
    return s


@pytest.fixture(scope="module")
def db():
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


# --- helpers ---------------------------------------------------------

def _seed_two_uuid_settlements(db, uid, marker):
    pg_uuid = str(uuid.uuid4())
    base = {
        "user_id":     uid,
        "entry_type":  "bnpl_settlement",
        "status":      "posted",
        "side":        "credit",
        "entity_type": "payment_gateway",
        "entity_id":   pg_uuid,
        "amount":      100.0,
        "_test_marker": marker,
    }
    db.general_ledger.insert_many([
        {**base, "id": str(uuid.uuid4()), "txn_group_id": f"{marker}-g1",
         "entry_no": 99001, "posted_at": "2026-06-08T10:00:00Z",
         "metadata": {
             "provider": "tabby",
             "settlement_reference": "TABBY-2026-06-08-AUTO",
             "settlement_date": "2026-06-16",
             "transferred_amount": 1000,
         }},
        {**base, "id": str(uuid.uuid4()), "txn_group_id": f"{marker}-g2",
         "entry_no": 99002, "posted_at": "2026-06-15T10:00:00Z",
         "metadata": {
             "provider": "tabby",
             "settlement_reference": "TABBY-2026-06-15-AUTO",
             "settlement_date": "2026-06-23",
             "transferred_amount": 1500,
         }},
    ])


def _seed_with_period_metadata(db, uid, marker):
    db.general_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "txn_group_id": f"{marker}-prefilled",
        "entry_no": 99003,
        "entry_type": "bnpl_settlement", "status": "posted",
        "side": "credit", "entity_type": "payment_gateway",
        "entity_id": str(uuid.uuid4()),
        "amount": 50, "posted_at": "2026-07-06T10:00:00Z",
        "_test_marker": marker,
        "metadata": {
            "provider": "tabby",
            "settlement_reference": "TABBY-PREFILLED",
            "settlement_date": "2026-07-13",
            "period_from": "2026-06-29",
            "period_to":   "2026-07-05",
            "transferred_amount": 50,
        },
    })


def _cleanup(db, uid, marker):
    db.general_ledger.delete_many({
        "user_id": uid, "_test_marker": marker,
    })
    db.provider_invoice_calendar.delete_many({
        "user_id": uid, "provider": "tabby",
    })


# --- tests -----------------------------------------------------------

def test_v8_http_uuid_entity_contiguous_periods(session, db):
    marker = f"iter251v8_{uuid.uuid4().hex[:8]}"
    uid = session.admin_id
    try:
        db.provider_invoice_calendar.delete_many(
            {"user_id": uid, "provider": "tabby"})
        _seed_two_uuid_settlements(db, uid, marker)

        # 1. Rebuild
        r = session.post(
            f"{BASE_URL}/api/settlement-engine/calendar/rebuild",
            json={"provider": "tabby", "dry_run": False},
            timeout=30,
        )
        assert r.status_code == 200, f"rebuild failed: {r.status_code} {r.text}"
        body = r.json()
        assert body["from_registered"] == 2, (
            f"from_registered != 2: {body}")

        # 2. GET /calendar
        r = session.get(
            f"{BASE_URL}/api/settlement-engine/calendar?provider=tabby",
            timeout=15,
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 2, items
        row0, row1 = items[0], items[1]
        assert row0["period_start"] == "2026-06-08", row0
        assert row0["period_end"]   == "2026-06-14", row0
        assert row1["period_start"] == "2026-06-15", row1
        assert row1["period_end"]   == "2026-06-21", row1
        assert row0["period_end"] < row1["period_start"]
        for r_ in items:
            assert r_["source"] == "registered_settlement", r_

        # 3. /calendar/audit — match_type=by_reference for both
        r = session.get(
            f"{BASE_URL}/api/settlement-engine/calendar/audit?provider=tabby",
            timeout=15,
        )
        assert r.status_code == 200, r.text
        audit = r.json()
        assert audit["calendar_rows"] == 2
        for row in audit["rows"]:
            assert row["match_type"] == "by_reference", row
            assert row["source"] == "registered_settlement", row

        # 4. /calendar/diagnose — from_registered_extracted == 2
        r = session.get(
            f"{BASE_URL}/api/settlement-engine/calendar/diagnose?provider=tabby",
            timeout=15,
        )
        assert r.status_code == 200
        diag = r.json()
        assert diag["from_registered_extracted"] == 2, diag
    finally:
        _cleanup(db, uid, marker)


def test_v8_http_prefilled_period_metadata_preserved(session, db):
    marker = f"iter251v8_pre_{uuid.uuid4().hex[:8]}"
    uid = session.admin_id
    try:
        db.provider_invoice_calendar.delete_many(
            {"user_id": uid, "provider": "tabby"})
        _seed_with_period_metadata(db, uid, marker)

        r = session.post(
            f"{BASE_URL}/api/settlement-engine/calendar/rebuild",
            json={"provider": "tabby", "dry_run": False},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json()["from_registered"] == 1

        r = session.get(
            f"{BASE_URL}/api/settlement-engine/calendar?provider=tabby",
            timeout=15,
        )
        items = r.json()["items"]
        assert len(items) == 1, items
        assert items[0]["period_start"] == "2026-06-29"
        assert items[0]["period_end"]   == "2026-07-05"
        assert items[0]["source"] == "registered_settlement"
    finally:
        _cleanup(db, uid, marker)
