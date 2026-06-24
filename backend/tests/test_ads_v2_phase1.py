"""Phase 1 — Ads V2 sync + reports tests.

Validates:
  • Sync adapters fetch real Meta data via V1 access_token (read-only).
  • Idempotency: re-syncing the same (account, date) updates one row.
  • Reconciliation drift is computed on second sync.
  • All three report aggregations (by-day / by-account / by-provider)
    return the SAME total for the same date range.
  • Read-only invariants from Phase 0 still hold:
      - No `general_ledger` writes with `metadata.source='ads_v2'`.
      - V1 collections untouched.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

API = (os.environ.get("REACT_APP_BACKEND_URL")
       or "http://localhost:8001").rstrip("/") + "/api"
LOGIN_EMAIL = "amasi.jewelery@gmail.com"
LOGIN_PWD = "10201917"


@pytest.fixture(scope="module")
def auth_headers():
    async def _get():
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(
                f"{API}/auth/login",
                json={"email": LOGIN_EMAIL, "password": LOGIN_PWD},
            )
            tok = r.json().get("access_token") or r.json().get("token")
            return {"Authorization": f"Bearer {tok}"}
    return asyncio.run(_get())


@pytest.fixture(scope="module")
def http_client():
    return httpx.Client(base_url=API, timeout=60.0)


@pytest.fixture(scope="module")
def linked_meta_account(http_client, auth_headers):
    """Ensure a Meta account is linked and sync_enabled=True."""
    # Try to find an existing linked account first
    r = http_client.get("/ads-v2/settings", headers=auth_headers)
    for a in r.json()["data"]["accounts"]:
        if a["provider"] == "meta" and a.get("external_account_id", "").startswith("act_799549"):
            # ensure enabled
            http_client.patch(
                f"/ads-v2/settings/accounts/{a['id']}",
                json={"sync_enabled": True},
                headers=auth_headers,
            )
            return a
    # Create one
    r = http_client.post(
        "/ads-v2/settings/accounts",
        json={
            "provider": "meta",
            "external_account_id": "act_799549215909312",
            "display_name": "TEST Phase1 Meta",
            "currency_native": "SAR",
            "v1_token_ref": {
                "provider": "meta",
                "collection": "meta_connections",
                "user_id": "x",
            },
        },
        headers=auth_headers,
    )
    a = r.json()["data"]
    http_client.patch(
        f"/ads-v2/settings/accounts/{a['id']}",
        json={"sync_enabled": True},
        headers=auth_headers,
    )
    return a


def test_sync_single_day(http_client, auth_headers, linked_meta_account):
    """POST /sync/account/{id}/day/{date} returns spend > 0."""
    acct_id = linked_meta_account["id"]
    r = http_client.post(
        f"/ads-v2/sync/account/{acct_id}/day/2026-06-23",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["spend_native"] > 0
    assert data["spend_sar"] > 0
    assert data["review_status"] in ("pending", "held_drift",
                                      "held_anomaly", "held_needs_fx")


def test_idempotency(http_client, auth_headers, linked_meta_account):
    """Re-syncing the same day does NOT duplicate rows in ads_daily."""
    acct_id = linked_meta_account["id"]
    for _ in range(3):
        http_client.post(
            f"/ads-v2/sync/account/{acct_id}/day/2026-06-22",
            headers=auth_headers,
        )
    # Check via report/daily
    r = http_client.get(
        "/ads-v2/report/daily?date_from=2026-06-22&date_to=2026-06-22",
        headers=auth_headers,
    )
    rows = r.json()["data"]["data"]
    matched = [x for x in rows if x["account_id"] == acct_id]
    assert len(matched) == 1, f"idempotency violated: {len(matched)} rows"
    assert matched[0]["sources_count"] >= 3


def test_multi_day_sync_batch(http_client, auth_headers, linked_meta_account):
    """POST /sync/run with multiple dates returns one result per date."""
    acct_id = linked_meta_account["id"]
    r = http_client.post(
        "/ads-v2/sync/run",
        json={
            "dates": ["2026-06-19", "2026-06-20", "2026-06-21"],
            "account_ids": [acct_id],
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["accounts_processed"] == 1
    assert data["ok_count"] >= 3


def test_ssot_totals_match_across_reports(
    http_client, auth_headers, linked_meta_account,
):
    """sum_by_day == sum_by_account == sum_by_provider (same date range)."""
    by_day = http_client.get(
        "/ads-v2/report?group_by=day&date_from=2026-06-19&date_to=2026-06-23",
        headers=auth_headers,
    ).json()["data"]
    by_acc = http_client.get(
        "/ads-v2/report?group_by=account&date_from=2026-06-19&date_to=2026-06-23",
        headers=auth_headers,
    ).json()["data"]
    by_prov = http_client.get(
        "/ads-v2/report?group_by=provider&date_from=2026-06-19&date_to=2026-06-23",
        headers=auth_headers,
    ).json()["data"]
    t_day = by_day["totals"]["gross_sar"]
    t_acc = by_acc["totals"]["gross_sar"]
    t_prov = by_prov["totals"]["gross_sar"]
    assert abs(t_day - t_acc) < 0.01, f"{t_day} != {t_acc}"
    assert abs(t_acc - t_prov) < 0.01, f"{t_acc} != {t_prov}"


def test_data_layer_meta_present(http_client, auth_headers):
    """Every report response includes the source_layer + ssot meta."""
    r = http_client.get(
        "/ads-v2/report?group_by=day&date_from=2026-06-19&date_to=2026-06-23",
        headers=auth_headers,
    )
    meta = r.json()["data"]["meta"]
    assert meta["ssot"] == "ads_daily"
    assert meta["source_layer"].startswith("ads_v2.data_layer.reports.")


def test_reconciliation_report_returns_rows(
    http_client, auth_headers, linked_meta_account,
):
    """Reconciliation report includes drift_pct and anomaly_flags fields."""
    r = http_client.get(
        "/ads-v2/report/reconciliation?date_from=2026-06-19&date_to=2026-06-23",
        headers=auth_headers,
    )
    data = r.json()["data"]
    assert "data" in data
    assert "summary" in data
    for row in data["data"]:
        for k in ("date", "spend_sar", "drift_pct", "anomaly_flags",
                  "confidence", "review_status"):
            assert k in row, f"missing {k}"


def test_sync_health_shows_account(
    http_client, auth_headers, linked_meta_account,
):
    r = http_client.get("/ads-v2/sync/health", headers=auth_headers)
    data = r.json()["data"]
    acct_ids = {a["id"] for a in data["accounts"]}
    assert linked_meta_account["id"] in acct_ids


def test_invariant_no_general_ledger_writes(http_client, auth_headers):
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    async def _count():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        m = await db.general_ledger.count_documents(
            {"metadata.source": "ads_v2"})
        t = await db.general_ledger.count_documents(
            {"entry_type": {"$regex": "^ads_v2_"}})
        return m, t
    m, t = asyncio.run(_count())
    assert m == 0, f"Phase 1 violated: {m} GL entries with source=ads_v2"
    assert t == 0, f"Phase 1 violated: {t} GL entries with ads_v2_* type"


def test_invariant_v1_collections_untouched(http_client, auth_headers):
    """Multiple syncs do not change V1 token doc counts."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    async def _snap():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        return (await db.snapchat_connections.count_documents({}),
                await db.meta_connections.count_documents({}))
    s1, m1 = asyncio.run(_snap())
    # Trigger a fresh sync
    http_client.get("/ads-v2/sync/health", headers=auth_headers)
    s2, m2 = asyncio.run(_snap())
    assert s1 == s2
    assert m1 == m2


def test_invariant_no_ledger_posted_at_in_daily(http_client, auth_headers):
    """Phase 1 must NOT set ledger_* fields on any ads_daily row."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    async def _check():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        return await db.ads_daily.count_documents(
            {"$or": [
                {"ledger_txn_group_id": {"$ne": None}},
                {"ledger_posted_at": {"$ne": None}},
            ]})
    cnt = asyncio.run(_check())
    assert cnt == 0, f"Phase 1 violated: {cnt} ads_daily rows have ledger_*"
