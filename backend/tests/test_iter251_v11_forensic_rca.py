"""Iter-251 v11 — HTTP integration tests for the new
GET /api/ad-spend-rca/forensic?date=YYYY-MM-DD&providers=... endpoint.

Comprehensive read-only forensic for ad-spend ledger gaps.
Scenarios validated:
  1. Endpoint shape — top-level keys (date, providers, counterparties,
     per_provider, general_ledger, ad_spend_idempotency_for_date,
     financial_movements_recent, currency_settings, diagnostics, note)
  2. counterparties[] expose required fields (id, name, ad_provider,
     external_account_id, currency, bank_fee_enabled, bank_fee_rate,
     platform_account_ids, is_active, last_sync_at)
  3. per_provider[provider].source_rows keyed by collection name
     with each value having rows, spend_sum, spend_native_sum,
     unique_account_ids, sample.
  4. Production-matching scenario: meta=510.27 SAR (6 rows),
     snapchat=2455.42 SAR / 654.78 USD (1 row), ZERO GL entries.
  5. ad_spend_idempotency record with date in key appears in
     ad_spend_idempotency_for_date.
  6. ads_currency_settings doc appears in currency_settings list.
  7. Read-only guarantee: snapshot count_documents on 6 collections
     before & after — must be identical.
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
FORENSIC_URL = f"{BASE_URL}/api/ad-spend-rca/forensic"
TARGET_DATE = "2026-06-23"
TEST_MARKER = f"iter251v11_{uuid.uuid4().hex[:8]}"


# --- session: register a fresh isolated user --------------------------

@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    email = f"test_forensic_{uuid.uuid4().hex[:8]}@hesab.app"
    password = "forensicTestPass123!"
    r = s.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "Forensic Test User", "email": email,
              "password": password},
        timeout=30,
    )
    assert r.status_code in (200, 201), \
        f"Register failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if not token:
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


# --- helpers ----------------------------------------------------------

def _snapshot(db, uid):
    return {
        "counterparties": db.counterparties.count_documents(
            {"user_id": uid}),
        "general_ledger": db.general_ledger.count_documents(
            {"user_id": uid}),
        "ad_spend_idempotency": db.ad_spend_idempotency.count_documents(
            {"user_id": uid}),
        "financial_movements": db.financial_movements.count_documents(
            {"user_id": uid}),
        "meta_ads_daily": db.meta_ads_daily.count_documents(
            {"user_id": uid}),
        "snapchat_account_daily":
            db.snapchat_account_daily.count_documents({"user_id": uid}),
        "ads_currency_settings":
            db.ads_currency_settings.count_documents({"user_id": uid}),
    }


def _cleanup(db, uid):
    for coll in ("counterparties", "meta_ads_daily",
                 "snapchat_account_daily", "snapchat_ads_daily",
                 "general_ledger", "ad_spend_idempotency",
                 "financial_movements", "ads_currency_settings"):
        db[coll].delete_many({"user_id": uid})


# --- seed: production-matching scenario -------------------------------

@pytest.fixture(scope="module")
def seeded(session, db):
    uid = session.uid
    _cleanup(db, uid)

    # 1 Meta counterparty + 1 Snapchat counterparty (production names)
    meta_cp = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "kind": "ad_account",
        "ad_provider": "meta",
        "external_account_id": "act_meta_prod",
        "name": "Meta Production",
        "name_lower": "meta production",
        "currency": "SAR",
        "bank_fee_enabled": True,
        "bank_fee_rate": 0.025,
        "platform_account_ids": ["act_meta_prod"],
        "is_active": True,
        "last_sync_at": "2026-06-23T08:00:00Z",
        "_test_marker": TEST_MARKER,
    }
    snap_cp = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "kind": "ad_account",
        "ad_provider": "snapchat",
        "external_account_id": "snap_prod_acct",
        "name": "Snapchat الرياض",
        "name_lower": "snapchat الرياض",
        "currency": "USD",
        "bank_fee_enabled": False,
        "bank_fee_rate": 0.0,
        "platform_account_ids": ["snap_prod_acct"],
        "is_active": True,
        "last_sync_at": "2026-06-23T08:00:00Z",
        "_test_marker": TEST_MARKER,
    }
    db.counterparties.insert_many([meta_cp, snap_cp])

    # 6 meta_ads_daily rows summing to 510.27 (matches production)
    meta_amounts = [85.05, 85.04, 85.05, 85.04, 85.04, 85.05]
    assert abs(sum(meta_amounts) - 510.27) < 0.01
    for i, amt in enumerate(meta_amounts):
        db.meta_ads_daily.insert_one({
            "user_id": uid, "date": TARGET_DATE,
            "account_id": "act_meta_prod",
            "campaign_id": f"camp_{i}",
            "spend": amt,
            "_test_marker": TEST_MARKER,
        })

    # 1 snapchat_account_daily row: spend=2455.42 SAR, spend_native=654.78 USD
    db.snapchat_account_daily.insert_one({
        "user_id": uid, "date": TARGET_DATE,
        "ad_account_id": "snap_prod_acct",
        "spend": 2455.42,
        "spend_native": 654.78,
        "fx_rate": 3.75,
        "native_currency": "USD",
        "_test_marker": TEST_MARKER,
    })

    # ZERO general_ledger ad_spend entries (matches production gap)

    # 1 ad_spend_idempotency record with key containing date
    db.ad_spend_idempotency.insert_one({
        "user_id": uid,
        "key": f"ad_spend:meta:act_meta_prod:{TARGET_DATE}:AM_00_12",
        "created_at": "2026-06-23T08:30:00Z",
        "spend_date": TARGET_DATE,
        "_test_marker": TEST_MARKER,
    })

    # ads_currency_settings doc
    db.ads_currency_settings.insert_one({
        "user_id": uid,
        "currency": "USD",
        "rate_to_sar": 3.80,
        "source": "manual",
        "effective_date": TARGET_DATE,
        "_test_marker": TEST_MARKER,
    })

    yield {"meta_cp": meta_cp, "snap_cp": snap_cp}

    _cleanup(db, uid)


# --- tests ------------------------------------------------------------

def test_forensic_endpoint_top_level_shape(session, seeded):
    r = session.get(FORENSIC_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    required = {
        "date", "providers", "counterparties", "per_provider",
        "general_ledger", "ad_spend_idempotency_for_date",
        "financial_movements_recent", "currency_settings",
        "diagnostics", "note",
    }
    missing = required - set(body.keys())
    assert not missing, f"missing top-level keys: {missing}"
    assert body["date"] == TARGET_DATE
    assert body["providers"] == ["meta", "snapchat"]
    # general_ledger sub-shape
    gl = body["general_ledger"]
    for k in ("recent_total", "for_target_date", "all_dates_seen",
              "raw_entries_sample"):
        assert k in gl, f"missing general_ledger.{k}"
    # diagnostics sub-shape
    diag = body["diagnostics"]
    for k in ("gl_entries_total_recent", "gl_entries_for_target_date",
              "gl_dates_present_in_recent", "idempotency_keys_for_date",
              "currency_settings_present", "questions"):
        assert k in diag, f"missing diagnostics.{k}"


def test_counterparties_required_fields(session, seeded):
    r = session.get(FORENSIC_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    body = r.json()
    cps = body["counterparties"]
    assert len(cps) == 2, f"expected 2 counterparties, got {len(cps)}"
    required = {
        "id", "name", "ad_provider", "external_account_id",
        "currency", "bank_fee_enabled", "bank_fee_rate",
        "platform_account_ids", "is_active", "last_sync_at",
    }
    for cp in cps:
        missing = required - set(cp.keys())
        assert not missing, \
            f"counterparty {cp.get('name')} missing: {missing}"
    # Verify values for meta counterparty
    meta_cp = next(c for c in cps if c["ad_provider"] == "meta")
    assert meta_cp["bank_fee_enabled"] is True
    assert meta_cp["bank_fee_rate"] == 0.025
    assert meta_cp["external_account_id"] == "act_meta_prod"
    # Verify snapchat 'الرياض' counterparty
    snap_cp = next(c for c in cps if c["ad_provider"] == "snapchat")
    assert "الرياض" in snap_cp["name"]
    assert snap_cp["external_account_id"] == "snap_prod_acct"
    assert snap_cp["currency"] == "USD"


def test_per_provider_source_rows_shape(session, seeded):
    r = session.get(FORENSIC_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    body = r.json()
    pp = body["per_provider"]
    assert "meta" in pp
    assert "snapchat" in pp
    # Meta block
    meta_block = pp["meta"]
    for k in ("source_rows", "raw_totals", "raw_native_totals"):
        assert k in meta_block, f"meta block missing {k}"
    assert "meta_ads_daily" in meta_block["source_rows"], \
        f"expected meta_ads_daily key, got {list(meta_block['source_rows'].keys())}"
    meta_src = meta_block["source_rows"]["meta_ads_daily"]
    for k in ("rows", "spend_sum", "spend_native_sum",
              "unique_account_ids", "sample"):
        assert k in meta_src, f"meta source missing {k}"
    assert meta_src["rows"] == 6, f"expected 6 rows, got {meta_src['rows']}"
    assert abs(meta_src["spend_sum"] - 510.27) < 0.01
    assert "act_meta_prod" in meta_src["unique_account_ids"]

    # Snapchat block
    snap_block = pp["snapchat"]
    assert "snapchat_account_daily" in snap_block["source_rows"]
    snap_src = snap_block["source_rows"]["snapchat_account_daily"]
    assert snap_src["rows"] == 1
    assert abs(snap_src["spend_sum"] - 2455.42) < 0.01
    assert abs(snap_src["spend_native_sum"] - 654.78) < 0.01


def test_production_matching_raw_totals(session, seeded):
    """Production scenario: meta=510.27 SAR, snap=2455.42 SAR / 654.78 USD,
    ZERO GL entries for the date."""
    r = session.get(FORENSIC_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    body = r.json()
    pp = body["per_provider"]
    # raw_totals = sum of `spend` field
    assert abs(pp["meta"]["raw_totals"] - 510.27) < 0.01, \
        f"expected meta.raw_totals=510.27, got {pp['meta']['raw_totals']}"
    assert abs(pp["snapchat"]["raw_totals"] - 2455.42) < 0.01, \
        f"expected snap.raw_totals=2455.42, got {pp['snapchat']['raw_totals']}"
    # ZERO GL entries for target date
    assert body["general_ledger"]["for_target_date"] == [], \
        f"expected empty GL for target date, got {len(body['general_ledger']['for_target_date'])} entries"
    assert body["diagnostics"]["gl_entries_for_target_date"] == 0


def test_idempotency_key_for_date_appears(session, seeded):
    r = session.get(FORENSIC_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    body = r.json()
    idemp = body["ad_spend_idempotency_for_date"]
    assert len(idemp) >= 1, \
        f"expected >=1 idempotency record for date, got {len(idemp)}"
    keys = [r.get("key") for r in idemp]
    assert any(TARGET_DATE in (k or "") for k in keys), \
        f"no key contains target date: {keys}"
    assert body["diagnostics"]["idempotency_keys_for_date"] >= 1


def test_currency_settings_appears(session, seeded):
    r = session.get(FORENSIC_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    body = r.json()
    cs = body["currency_settings"]
    assert len(cs) >= 1, "expected >=1 currency setting"
    usd = next((c for c in cs if c.get("currency") == "USD"), None)
    assert usd is not None, f"USD setting not found in {cs}"
    assert usd["rate_to_sar"] == 3.80
    assert usd["source"] == "manual"
    assert body["diagnostics"]["currency_settings_present"] >= 1


def test_forensic_read_only_guarantee(session, seeded, db):
    """Snapshot before/after — endpoint must NOT mutate any collection."""
    uid = session.uid
    before = _snapshot(db, uid)
    for params in (
        {"date": TARGET_DATE, "providers": "meta"},
        {"date": TARGET_DATE, "providers": "snapchat"},
        {"date": TARGET_DATE, "providers": "meta,snapchat"},
        {"date": TARGET_DATE, "providers": "meta,snapchat,tiktok,google"},
    ):
        r = session.get(FORENSIC_URL, params=params, timeout=30)
        assert r.status_code == 200, r.text
    after = _snapshot(db, uid)
    assert before == after, \
        f"DB mutated! before={before} after={after}"


def test_no_mongo_objectid_leak(session, seeded):
    """Verify no '_id' field anywhere in the response."""
    import json
    r = session.get(FORENSIC_URL,
                    params={"date": TARGET_DATE,
                            "providers": "meta,snapchat"},
                    timeout=30)
    assert r.status_code == 200
    body_text = json.dumps(r.json())
    # The string `"_id"` should not appear anywhere
    assert '"_id"' not in body_text, \
        "Mongo _id field leaked into response"
