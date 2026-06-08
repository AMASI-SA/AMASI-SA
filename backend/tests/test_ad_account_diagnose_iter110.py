"""Iter-110 — `/ad-accounts/diagnose` endpoint.

Purpose: surface the exact data-source mismatch that prevents an
ad-account from syncing. Tests cover the three diagnosis paths:
  • account with NO external_account_id where the source HAS a scope
    field → flagged as needing the ID.
  • account whose external_account_id doesn't match any value present
    in the source collection → flagged with the available IDs listed
    so the merchant can fix.
  • account whose external_account_id MATCHES → marked healthy.
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
    email = f"diag110-{suffix}@example.com"
    pwd = "T#110a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "Dx"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb()}


def _make(ctx, name, provider="snapchat", external_id=None):
    payload = {"name": name, "ad_provider": provider, "force": True}
    if external_id:
        payload["external_account_id"] = external_id
    r = requests.post(f"{BASE_URL}/api/ad-accounts",
                      json=payload, headers=ctx["hdr"], timeout=10)
    return r.json()["id"]


def test_diagnose_flags_id_mismatch(ctx):
    """The merchant set external_account_id='wrong_id' on the
    counterparty, but the data has ad_account_id='right_id' — the
    diagnose endpoint must flag this and list the available IDs."""
    cp = _make(ctx, "Snap mismatch", external_id="wrong_id")
    ctx["db"].snapchat_account_daily.insert_one({
        "user_id": ctx["uid"], "ad_account_id": "right_id_abc",
        "date": "2026-06-01", "spend": 100.0,
    })
    r = requests.get(f"{BASE_URL}/api/ad-accounts/diagnose",
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    by_id = {a["id"]: a for a in r.json()["accounts"]}
    me = by_id[cp]
    assert me["healthy"] is False
    # The snapchat_account_daily source should be reported with the
    # actual available IDs.
    snap_src = next(s for s in me["per_source_status"]
                    if s["collection"] == "snapchat_account_daily")
    assert snap_src["your_external_id_matches"] is False
    assert "right_id_abc" in snap_src["available_ids"]
    # Diagnosis text must mention the wrong ID
    full_text = " ".join(me["diagnosis"])
    assert "wrong_id" in full_text


def test_diagnose_marks_matching_account_healthy(ctx):
    cp = _make(ctx, "Snap correct", external_id="acc_OK")
    ctx["db"].snapchat_account_daily.insert_one({
        "user_id": ctx["uid"], "ad_account_id": "acc_OK",
        "date": "2026-06-01", "spend": 50.0,
    })
    r = requests.get(f"{BASE_URL}/api/ad-accounts/diagnose",
                     headers=ctx["hdr"], timeout=10)
    me = next(a for a in r.json()["accounts"] if a["id"] == cp)
    assert me["healthy"] is True
    snap_src = next(s for s in me["per_source_status"]
                    if s["collection"] == "snapchat_account_daily")
    assert snap_src["your_external_id_matches"] is True


def test_diagnose_account_with_no_external_id_is_flagged(ctx):
    cp = _make(ctx, "Snap no ext", external_id=None)
    r = requests.get(f"{BASE_URL}/api/ad-accounts/diagnose",
                     headers=ctx["hdr"], timeout=10)
    me = next(a for a in r.json()["accounts"] if a["id"] == cp)
    assert me["healthy"] is False
    assert any("external_account_id" in d.lower() or "Ad Account ID" in d
               for d in me["diagnosis"])
