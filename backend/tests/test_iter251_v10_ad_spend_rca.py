"""Iter-251 v10 — HTTP integration tests for the new
GET /api/ad-spend-rca?date=YYYY-MM-DD&providers=meta,snapchat endpoint.

Pure read-only diagnostic that surfaces, per ad-account:
  • raw API spend (native + SAR via FX)
  • previously stored spend (yesterday)
  • general-ledger legs posted for the date
  • 3-way reconciliation (api / gl / ui-balance) with deltas

Scenarios validated:
  1. Endpoint shape — keys: date, providers, timezone, accounts[], totals_per_provider, note
  2. Snapchat USD scenario — 770 USD spend, GL 2455.42 SAR → delta_api_vs_gl ≈ 432.08
  3. Meta SAR scenario — 726.22 SAR spend, GL 510.27 SAR → delta_api_vs_gl ≈ 215.95
  4. providers parameter parsing (meta / snapchat / both)
  5. Read-only guarantee via DB snapshot before/after
  6. Empty-state: 200 with empty accounts[] when no counterparty exists
  7. GL leg matching paths (entity_id / metadata.ad_account_id / metadata.account_id / metadata.counterparty_id)
  8. Snapchat: when only unscoped snapchat_ads_daily rows exist for a different account,
     scoped source must be empty (no cross-account aggregation)
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
RCA_URL = f"{BASE_URL}/api/ad-spend-rca"
TARGET_DATE = "2026-06-23"
PREV_DATE = "2026-06-22"

TEST_MARKER = f"iter251v10_{uuid.uuid4().hex[:8]}"


# --- session: register a fresh isolated user --------------------------

@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    email = f"test_rca_{uuid.uuid4().hex[:8]}@hesab.app"
    password = "rcaTestPass123!"
    r = s.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "RCA Test User", "email": email,
              "password": password},
        timeout=30,
    )
    assert r.status_code in (200, 201), f"Register failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        # Fall back to explicit login
        r2 = s.post(f"{BASE_URL}/api/auth/login",
                    json={"email": email, "password": password},
                    timeout=30)
        assert r2.status_code == 200, r2.text
        token = r2.json().get("access_token") or r2.json().get("token")
    assert token
    s.headers.update({"Authorization": f"Bearer {token}"})
    me = s.get(f"{BASE_URL}/api/auth/me", timeout=15).json()
    uid = me.get("id") or me.get("user", {}).get("id")
    assert uid, f"could not resolve user id: {me}"
    s.uid = uid
    s.email = email
    return s


@pytest.fixture(scope="module")
def db():
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


# --- helpers ---------------------------------------------------------

def _cp(uid, provider, ext_id, name, currency="SAR"):
    return {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "kind": "ad_account",
        "provider": provider,
        "external_id": ext_id,
        "name": name,
        "name_lower": (name or "").strip().lower(),  # required by cp_unique_name index
        "currency": currency,
        "_test_marker": TEST_MARKER,
    }


def _snapshot(db, uid):
    return {
        "counterparties": db.counterparties.count_documents({"user_id": uid}),
        "meta_ads_daily": db.meta_ads_daily.count_documents({"user_id": uid}),
        "snapchat_account_daily": db.snapchat_account_daily.count_documents(
            {"user_id": uid}),
        "snapchat_ads_daily": db.snapchat_ads_daily.count_documents(
            {"user_id": uid}),
        "general_ledger": db.general_ledger.count_documents({"user_id": uid}),
        "ads_currency_settings": db.ads_currency_settings.count_documents(
            {"user_id": uid}),
    }


def _cleanup(db, uid):
    for coll in ("counterparties", "meta_ads_daily",
                 "snapchat_account_daily", "snapchat_ads_daily",
                 "general_ledger", "ads_currency_settings"):
        db[coll].delete_many({"user_id": uid})


# --- seed fixture: builds the full RCA scenario -----------------------

@pytest.fixture(scope="module")
def seeded(session, db):
    uid = session.uid
    # ensure clean slate for this fresh test user
    _cleanup(db, uid)

    # Counterparties: 1 meta + 1 snapchat (scoped) + 1 snapchat with no
    # external_id (for the "unscoped only" edge case) — third uses a
    # different external_id, never matching the seeded scoped row.
    meta_cp = _cp(uid, "meta", "act_meta_111", "Meta Account",
                  currency="SAR")
    snap_cp = _cp(uid, "snapchat", "snap_acct_222", "Snap Account",
                  currency="USD")
    db.counterparties.insert_many([meta_cp, snap_cp])

    # Meta spend native = 726.22 SAR
    db.meta_ads_daily.insert_one({
        "user_id": uid, "date": TARGET_DATE,
        "account_id": "act_meta_111",
        "spend": 726.22,
        "_test_marker": TEST_MARKER,
    })
    # Previous-day meta (yesterday rollup)
    db.meta_ads_daily.insert_one({
        "user_id": uid, "date": PREV_DATE,
        "account_id": "act_meta_111",
        "spend": 100.00,
        "_test_marker": TEST_MARKER,
    })

    # Snapchat spend native = 770 USD via snapchat_account_daily
    db.snapchat_account_daily.insert_one({
        "user_id": uid, "date": TARGET_DATE,
        "ad_account_id": "snap_acct_222",
        "spend": 770.0,
        "_test_marker": TEST_MARKER,
    })
    # Edge case: an unscoped snapchat_ads_daily row for a DIFFERENT
    # account — must NOT leak into our scoped account.
    db.snapchat_ads_daily.insert_one({
        "user_id": uid, "date": TARGET_DATE,
        "ad_account_id": "snap_OTHER_999",  # different account
        "spend": 9999.99,
        "_test_marker": TEST_MARKER,
    })

    # GL entries — use 4 distinct match paths
    base_entry_no = int(uuid.uuid4().int % 1_000_000) + 9_900_000
    # Meta — match via entity_id == cp_id (path 1), debit ad_spend
    db.general_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "entry_no": base_entry_no + 1,
        "entry_type": "ad_spend", "status": "posted",
        "side": "debit", "amount": 510.27,
        "entity_type": "ad_spend", "entity_id": meta_cp["id"],
        "posted_at": f"{TARGET_DATE}T08:00:00Z",
        "metadata": {"spend_date": TARGET_DATE,
                      "period_key": "AM_00_12",
                      "spend_native": 510.27,
                      "spend_native_currency": "SAR",
                      "fx_rate": 1.0, "fx_source": "identity"},
        "_test_marker": TEST_MARKER,
    })
    # Snapchat — match via metadata.ad_account_id (path 2), debit
    db.general_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "entry_no": base_entry_no + 2,
        "entry_type": "ad_spend", "status": "posted",
        "side": "debit", "amount": 1200.00,
        "entity_type": "ad_spend", "entity_id": "different_id",
        "posted_at": f"{TARGET_DATE}T09:00:00Z",
        "metadata": {"spend_date": TARGET_DATE,
                      "ad_account_id": "snap_acct_222",
                      "period_key": "AM_00_12",
                      "spend_native": 320.00,
                      "spend_native_currency": "USD",
                      "fx_rate": 3.75, "fx_source": "default"},
        "_test_marker": TEST_MARKER,
    })
    # Snapchat — match via metadata.account_id (path 3), debit
    db.general_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "entry_no": base_entry_no + 3,
        "entry_type": "ad_spend", "status": "posted",
        "side": "debit", "amount": 800.00,
        "entity_type": "ad_spend", "entity_id": "another_id",
        "posted_at": f"{TARGET_DATE}T13:00:00Z",
        "metadata": {"spend_date": TARGET_DATE,
                      "account_id": "snap_acct_222",
                      "period_key": "PM_12_24",
                      "spend_native": 213.33,
                      "spend_native_currency": "USD",
                      "fx_rate": 3.75, "fx_source": "default"},
        "_test_marker": TEST_MARKER,
    })
    # Snapchat — match via metadata.counterparty_id (path 4), debit
    db.general_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "entry_no": base_entry_no + 4,
        "entry_type": "ad_spend", "status": "posted",
        "side": "debit", "amount": 455.42,
        "entity_type": "ad_spend", "entity_id": "yet_another",
        "posted_at": f"{TARGET_DATE}T14:00:00Z",
        "metadata": {"spend_date": TARGET_DATE,
                      "counterparty_id": snap_cp["id"],
                      "period_key": "PM_12_24_CORRECTION:1",
                      "spend_native": 121.45,
                      "spend_native_currency": "USD",
                      "fx_rate": 3.75, "fx_source": "default"},
        "_test_marker": TEST_MARKER,
    })

    # Total snapchat GL SAR = 1200 + 800 + 455.42 = 2455.42 ✓

    yield {"meta_cp": meta_cp, "snap_cp": snap_cp}

    _cleanup(db, uid)


# --- tests -----------------------------------------------------------

def test_endpoint_shape_and_top_level_keys(session, seeded):
    r = session.get(RCA_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("date", "providers", "timezone", "accounts",
              "totals_per_provider", "note"):
        assert k in body, f"missing top-level key: {k}"
    assert body["date"] == TARGET_DATE
    assert body["timezone"] == "Asia/Riyadh"
    assert body["providers"] == ["meta", "snapchat"]
    assert isinstance(body["accounts"], list)
    assert len(body["accounts"]) == 2


def test_account_record_keys(session, seeded):
    r = session.get(RCA_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    body = r.json()
    required = {
        "provider", "internal_account_id", "external_account_id",
        "account_name", "date_used", "timezone_used",
        "currency", "fx_rate_used", "fx_source",
        "api", "previous_stored", "general_ledger", "reconciliation",
    }
    for acc in body["accounts"]:
        missing = required - set(acc.keys())
        assert not missing, f"acc {acc.get('provider')} missing: {missing}"
        # nested keys
        for k in ("source_collection", "rows_found", "spend_native",
                  "spend_sar", "raw_documents"):
            assert k in acc["api"], f"api missing {k}"
        for k in ("date", "spend_native"):
            assert k in acc["previous_stored"]
        for k in ("legs_count", "total_sar", "period_keys",
                  "computation_mode", "legs"):
            assert k in acc["general_ledger"]
        for k in ("api_spend_sar", "gl_total_sar", "ui_balance_sar",
                  "delta_api_vs_gl", "delta_api_vs_ui", "delta_gl_vs_ui"):
            assert k in acc["reconciliation"]
        assert acc["timezone_used"] == "Asia/Riyadh"
        assert acc["date_used"] == TARGET_DATE


def test_meta_scenario_215_95(session, seeded):
    r = session.get(RCA_URL,
                    params={"date": TARGET_DATE, "providers": "meta"},
                    timeout=30)
    body = r.json()
    assert len(body["accounts"]) == 1
    acc = body["accounts"][0]
    assert acc["provider"] == "meta"
    assert acc["currency"] == "SAR"
    # native SAR; fx identity
    assert abs(acc["api"]["spend_native"] - 726.22) < 0.01
    assert abs(acc["api"]["spend_sar"] - 726.22) < 0.01
    assert acc["api"]["source_collection"] == "meta_ads_daily"
    assert acc["api"]["rows_found"] == 1
    assert abs(acc["general_ledger"]["total_sar"] - 510.27) < 0.01
    assert abs(acc["reconciliation"]["delta_api_vs_gl"] - 215.95) < 0.01, \
        f"expected ~215.95, got {acc['reconciliation']['delta_api_vs_gl']}"
    # previous stored = 100.0
    assert abs(acc["previous_stored"]["spend_native"] - 100.0) < 0.01
    assert acc["previous_stored"]["date"] == PREV_DATE


def test_snapchat_scenario_432_08(session, seeded):
    r = session.get(RCA_URL,
                    params={"date": TARGET_DATE,
                            "providers": "snapchat"},
                    timeout=30)
    body = r.json()
    assert len(body["accounts"]) == 1
    acc = body["accounts"][0]
    assert acc["provider"] == "snapchat"
    assert acc["currency"] == "USD"
    assert acc["fx_rate_used"] == 3.75
    assert acc["fx_source"] == "default"
    assert acc["api"]["source_collection"] == "snapchat_account_daily"
    assert abs(acc["api"]["spend_native"] - 770.0) < 0.01
    assert abs(acc["api"]["spend_sar"] - 2887.5) < 0.01, \
        f"expected 2887.50, got {acc['api']['spend_sar']}"
    assert abs(acc["general_ledger"]["total_sar"] - 2455.42) < 0.01
    assert abs(acc["reconciliation"]["delta_api_vs_gl"] - 432.08) < 0.01, \
        f"expected ~432.08, got {acc['reconciliation']['delta_api_vs_gl']}"
    # All 4 match paths should populate the legs (3 metadata paths
    # for snapchat: ad_account_id, account_id, counterparty_id)
    assert acc["general_ledger"]["legs_count"] == 3, \
        f"expected 3 GL legs (3 metadata match paths), got {acc['general_ledger']['legs_count']}"
    # Period keys should include AM/PM and a correction tag
    pks = acc["general_ledger"]["period_keys"]
    assert any("AM_" in p for p in pks)
    assert any("PM_" in p for p in pks)
    assert acc["general_ledger"]["computation_mode"].startswith("windowed")


def test_providers_parameter_filtering(session, seeded):
    r1 = session.get(RCA_URL,
                     params={"date": TARGET_DATE, "providers": "meta"},
                     timeout=30)
    assert r1.status_code == 200
    assert {a["provider"] for a in r1.json()["accounts"]} == {"meta"}
    assert list(r1.json()["totals_per_provider"].keys()) == ["meta"]

    r2 = session.get(RCA_URL,
                     params={"date": TARGET_DATE, "providers": "snapchat"},
                     timeout=30)
    assert r2.status_code == 200
    assert {a["provider"] for a in r2.json()["accounts"]} == {"snapchat"}

    r3 = session.get(RCA_URL,
                     params={"date": TARGET_DATE,
                             "providers": "meta,snapchat"},
                     timeout=30)
    assert r3.status_code == 200
    providers_found = {a["provider"] for a in r3.json()["accounts"]}
    assert providers_found == {"meta", "snapchat"}
    assert set(r3.json()["totals_per_provider"].keys()) == {"meta", "snapchat"}


def test_totals_per_provider_aggregation(session, seeded):
    r = session.get(RCA_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    totals = r.json()["totals_per_provider"]
    # Meta totals
    assert abs(totals["meta"]["api_spend_sar"] - 726.22) < 0.01
    assert abs(totals["meta"]["gl_total_sar"] - 510.27) < 0.01
    assert abs(totals["meta"]["delta_api_vs_gl"] - 215.95) < 0.01
    # Snapchat totals
    assert abs(totals["snapchat"]["api_spend_sar"] - 2887.50) < 0.01
    assert abs(totals["snapchat"]["gl_total_sar"] - 2455.42) < 0.01
    assert abs(totals["snapchat"]["delta_api_vs_gl"] - 432.08) < 0.01


def test_read_only_guarantee(session, seeded, db):
    """Snapshot pre & post — endpoint must NOT mutate any collection."""
    uid = session.uid
    before = _snapshot(db, uid)
    # Call endpoint a few times with different parameter combos
    for params in (
        {"date": TARGET_DATE, "providers": "meta"},
        {"date": TARGET_DATE, "providers": "snapchat"},
        {"date": TARGET_DATE, "providers": "meta,snapchat"},
    ):
        r = session.get(RCA_URL, params=params, timeout=30)
        assert r.status_code == 200, r.text
    after = _snapshot(db, uid)
    assert before == after, f"DB mutated! before={before} after={after}"


def test_empty_provider_returns_200_with_empty_accounts(session, db):
    """A provider with NO counterparties → empty accounts but 200."""
    # Use a brand-new isolated user with no seeded data
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    email = f"empty_{uuid.uuid4().hex[:8]}@hesab.app"
    r = s.post(f"{BASE_URL}/api/auth/register",
               json={"name": "Empty", "email": email,
                     "password": "emptyPass123!"}, timeout=30)
    assert r.status_code in (200, 201), r.text
    token = r.json().get("access_token") or r.json().get("token")
    s.headers.update({"Authorization": f"Bearer {token}"})

    r2 = s.get(RCA_URL,
               params={"date": TARGET_DATE,
                       "providers": "meta,snapchat"},
               timeout=30)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["accounts"] == []
    assert body["totals_per_provider"] == {}
    assert body["date"] == TARGET_DATE


def test_snapchat_no_cross_account_leak(session, seeded):
    """The unscoped snapchat_ads_daily row for snap_OTHER_999 must NOT
    pollute the snap_acct_222 account's API spend."""
    r = session.get(RCA_URL,
                    params={"date": TARGET_DATE,
                            "providers": "snapchat"},
                    timeout=30)
    acc = r.json()["accounts"][0]
    # Should be exactly 770 USD, not 770+9999.99
    assert abs(acc["api"]["spend_native"] - 770.0) < 0.01, \
        f"cross-account leak detected: {acc['api']['spend_native']}"
    assert acc["api"]["source_collection"] == "snapchat_account_daily"


def test_ads_currency_settings_fx_override(session, seeded, db):
    """If ads_currency_settings has a custom USD→SAR rate, it must be
    used in preference to the SAMA default."""
    uid = session.uid
    custom_rate = 3.80
    db.ads_currency_settings.insert_one({
        "user_id": uid, "currency": "USD",
        "rate_to_sar": custom_rate, "source": "manual_test",
        "effective_date": TARGET_DATE,
        "_test_marker": TEST_MARKER,
    })
    try:
        r = session.get(RCA_URL,
                        params={"date": TARGET_DATE,
                                "providers": "snapchat"},
                        timeout=30)
        acc = r.json()["accounts"][0]
        assert acc["fx_rate_used"] == custom_rate
        assert "ads_currency_settings" in acc["fx_source"]
        expected_sar = round(770.0 * custom_rate, 2)
        assert abs(acc["api"]["spend_sar"] - expected_sar) < 0.01
    finally:
        db.ads_currency_settings.delete_many(
            {"user_id": uid, "_test_marker": TEST_MARKER})


def test_gl_leg_match_paths_present(session, seeded):
    """The endpoint must surface legs matched by each of the 4 paths.
    Meta uses entity_id; Snapchat uses 3 metadata paths."""
    r = session.get(RCA_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    body = r.json()
    meta_acc = next(a for a in body["accounts"] if a["provider"] == "meta")
    snap_acc = next(a for a in body["accounts"] if a["provider"] == "snapchat")

    # Meta: leg matched via entity_id == cp_id
    assert meta_acc["general_ledger"]["legs_count"] == 1
    assert meta_acc["general_ledger"]["legs"][0]["entity_id"] == \
        seeded["meta_cp"]["id"]

    # Snapchat: 3 legs from the 3 metadata paths
    snap_legs = snap_acc["general_ledger"]["legs"]
    assert len(snap_legs) == 3
    metas = [lg.get("metadata") or {} for lg in snap_legs]
    paths_found = {
        "ad_account_id":  any("ad_account_id"  in m for m in metas),
        "account_id":     any("account_id"     in m and "ad_account_id" not in m for m in metas),
        "counterparty_id": any("counterparty_id" in m for m in metas),
    }
    assert all(paths_found.values()), \
        f"missing GL match paths: {paths_found}"
