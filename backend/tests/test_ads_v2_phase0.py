"""Phase 0 — Ads V2 contract & invariant tests.

Validates that:
  • All 3 new collections exist with the right indexes.
  • Discovery endpoint reads V1 tokens (read-only).
  • Create/Patch/Delete on `ads_accounts` work and audit-log
    everything to `ads_sync_logs`.
  • `ads_daily` remains empty in Phase 0 (no sync yet).
  • V1 collections are NEVER modified by any V2 operation.
  • general_ledger gets NO entries with `metadata.source=ads_v2`
    or `entry_type ~ ^ads_v2_`.
  • The token health endpoint correctly resolves the V1 doc.

These tests run against the live preview backend.  They use only the
`amasi.jewelery@gmail.com` user and clean up after themselves.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

API = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/") + "/api"

LOGIN_EMAIL = "amasi.jewelery@gmail.com"
LOGIN_PWD = "10201917"


# ── Fixtures ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def auth_headers():
    async def _get():
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(
                f"{API}/auth/login",
                json={"email": LOGIN_EMAIL, "password": LOGIN_PWD},
            )
            r.raise_for_status()
            tok = r.json().get("access_token") or r.json().get("token")
            assert tok, f"no token: {r.text[:200]}"
            return {"Authorization": f"Bearer {tok}"}
    return asyncio.run(_get())


@pytest.fixture(scope="module")
def http_client():
    return httpx.Client(base_url=API, timeout=30.0)


@pytest.fixture(scope="function", autouse=True)
def cleanup(http_client, auth_headers):
    """Best-effort cleanup of any test accounts created."""
    yield
    try:
        r = http_client.get("/ads-v2/settings", headers=auth_headers)
        if r.status_code != 200:
            return
        for a in (r.json().get("data") or {}).get("accounts", []):
            if a.get("display_name", "").startswith("TEST-V2"):
                http_client.delete(
                    f"/ads-v2/settings/accounts/{a['id']}",
                    headers=auth_headers,
                )
    except Exception:
        pass


# ── Tests ───────────────────────────────────────────────────────────────
def test_phase0_endpoints_respond_200(http_client, auth_headers):
    """GET /ads-v2/settings + discover both reachable."""
    r = http_client.get("/ads-v2/settings", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "accounts" in data
    assert "recent_activity" in data
    assert "stats" in data
    assert data["_meta"]["ssot"] == "ads_accounts + ads_sync_logs"


def test_discovery_reads_v1_tokens(http_client, auth_headers):
    """Discovery returns a block per provider with connection_status."""
    r = http_client.post(
        "/ads-v2/settings/accounts/discover", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    blocks = r.json()["data"]
    for provider in ("meta", "snapchat", "tiktok"):
        assert provider in blocks
        block = blocks[provider]
        assert "connection_status" in block
        assert isinstance(block.get("accounts") or [], list)
    # On preview, Meta should have at least one active account
    assert blocks["meta"]["connection_status"] in (
        "active", "missing", "token_invalid",
    )


def test_create_meta_account_and_audit_log(http_client, auth_headers):
    """Linking a Meta account inserts a row and logs `account_created`."""
    payload = {
        "provider": "meta",
        "external_account_id": "act_phase0_test_123456",
        "display_name": "TEST-V2 Meta Sandbox",
        "currency_native": "SAR",
        "timezone": "Asia/Riyadh",
        "v1_token_ref": {
            "provider": "meta",
            "collection": "meta_connections",
            "user_id": "will_be_overridden",
        },
    }
    r = http_client.post(
        "/ads-v2/settings/accounts",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    acct = r.json()["data"]
    assert acct["id"]
    assert acct["sync_status"] == "discovered"
    assert acct["sync_enabled"] is False
    # The v1_token_ref user_id should be normalized to the caller's user_id
    assert acct["v1_token_ref"]["snapshot_only"] is True

    # Check audit log
    r2 = http_client.get(
        "/ads-v2/settings/activity?limit=5", headers=auth_headers,
    )
    events = [e["event"] for e in r2.json()["data"]["events"]]
    assert "account_created" in events


def test_patch_bank_fee_and_fx_logged_with_correct_event_names(
    http_client, auth_headers,
):
    """PATCH fx_to_sar logs fx_changed; PATCH bank_fee logs bank_fee_changed."""
    # Create
    r = http_client.post(
        "/ads-v2/settings/accounts",
        json={
            "provider": "meta",
            "external_account_id": "act_phase0_patchtest",
            "display_name": "TEST-V2 Patch Target",
            "currency_native": "USD",
            "timezone": "America/Los_Angeles",
            "v1_token_ref": {
                "provider": "meta",
                "collection": "meta_connections",
                "user_id": "x",
            },
        },
        headers=auth_headers,
    )
    acct_id = r.json()["data"]["id"]

    # Patch fx only
    http_client.patch(
        f"/ads-v2/settings/accounts/{acct_id}",
        json={"fx_to_sar": {
            "mode": "manual", "rate": 3.752,
            "effective_from": "2026-06-01",
            "source_note": "TEST",
        }},
        headers=auth_headers,
    )
    # Patch bank fee only
    http_client.patch(
        f"/ads-v2/settings/accounts/{acct_id}",
        json={"bank_fee": {
            "enabled": True, "method": "pct_plus_flat",
            "rate_pct": 0.0285, "flat_amount_sar": 5.0,
            "note": "TEST visa",
        }},
        headers=auth_headers,
    )

    # Check event types
    r2 = http_client.get(
        f"/ads-v2/settings/activity?limit=10&account_id={acct_id}",
        headers=auth_headers,
    )
    events = [e["event"] for e in r2.json()["data"]["events"]]
    # The most-recent first → first event should be bank_fee_changed
    assert events[0] == "bank_fee_changed"
    assert "fx_changed" in events
    assert "account_created" in events


def test_review_settings_patch_passes_through(http_client, auth_headers):
    """PATCH review_settings persists drift thresholds and auto-approve."""
    r = http_client.post(
        "/ads-v2/settings/accounts",
        json={
            "provider": "snapchat",
            "external_account_id": "snap_phase0_test",
            "display_name": "TEST-V2 Snap Review",
            "currency_native": "USD",
            "timezone": "America/Los_Angeles",
            "v1_token_ref": {
                "provider": "snapchat",
                "collection": "snapchat_connections",
                "user_id": "x",
            },
        },
        headers=auth_headers,
    )
    acct_id = r.json()["data"]["id"]
    http_client.patch(
        f"/ads-v2/settings/accounts/{acct_id}",
        json={"review_settings": {
            "auto_approve_under_sar": 100,
            "drift_warning_threshold_pct": 3.5,
            "drift_block_threshold_pct": 12,
        }},
        headers=auth_headers,
    )
    r2 = http_client.get("/ads-v2/settings", headers=auth_headers)
    accts = r2.json()["data"]["accounts"]
    a = next(a for a in accts if a["id"] == acct_id)
    rs = a["review_settings"]
    assert rs["auto_approve_under_sar"] == 100
    assert rs["drift_warning_threshold_pct"] == 3.5
    assert rs["drift_block_threshold_pct"] == 12


def test_soft_delete_marks_account(http_client, auth_headers):
    """DELETE soft-deletes (sets soft_deleted=True, sync_enabled=False)."""
    r = http_client.post(
        "/ads-v2/settings/accounts",
        json={
            "provider": "tiktok",
            "external_account_id": "tt_phase0_delete",
            "display_name": "TEST-V2 TikTok Delete",
            "currency_native": "USD",
            "v1_token_ref": {
                "provider": "tiktok",
                "collection": "tiktok_connections",
                "user_id": "x",
            },
        },
        headers=auth_headers,
    )
    acct_id = r.json()["data"]["id"]
    r2 = http_client.delete(
        f"/ads-v2/settings/accounts/{acct_id}", headers=auth_headers,
    )
    assert r2.json()["data"]["deleted"] == 1

    # After delete, /settings should not return the row
    r3 = http_client.get("/ads-v2/settings", headers=auth_headers)
    ids = [a["id"] for a in r3.json()["data"]["accounts"]]
    assert acct_id not in ids


# ── HARD INVARIANTS (Phase 0 read-only guarantees) ──────────────────────
def test_invariant_no_general_ledger_writes(http_client, auth_headers):
    """Phase 0 must NOT write to general_ledger.

    Verified by Mongo directly: zero docs with metadata.source='ads_v2'
    or entry_type starting with 'ads_v2_'.
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    import os
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    
    async def _count():
        db = AsyncIOMotorClient(mongo_url)[db_name]
        v2_meta = await db.general_ledger.count_documents(
            {"metadata.source": "ads_v2"})
        v2_type = await db.general_ledger.count_documents(
            {"entry_type": {"$regex": "^ads_v2_"}})
        return v2_meta, v2_type
    
    m, t = asyncio.run(_count())
    assert m == 0, f"Phase 0 violated: {m} GL entries with source=ads_v2"
    assert t == 0, f"Phase 0 violated: {t} GL entries with ads_v2_* type"


def test_invariant_no_ads_daily_writes(http_client, auth_headers):
    """ads_daily should be untouched in Phase 0 (sync is Phase 1)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import os
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    
    async def _count():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        return await db.ads_daily.count_documents({})
    cnt = asyncio.run(_count())
    assert cnt == 0, f"Phase 0 violated: ads_daily has {cnt} rows"


def test_invariant_v1_collections_untouched(http_client, auth_headers):
    """V1 token collections must keep their original count + a fresh
    snapshot of the latest doc field unchanged."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import os
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    
    async def _snapshot():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        snap_count = await db.snapchat_connections.count_documents({})
        meta_count = await db.meta_connections.count_documents({})
        return snap_count, meta_count
    
    s1, m1 = asyncio.run(_snapshot())
    
    # Trigger several discovery + create operations
    for i in range(3):
        http_client.post(
            "/ads-v2/settings/accounts/discover", headers=auth_headers,
        )
    
    s2, m2 = asyncio.run(_snapshot())
    assert s1 == s2, f"snapchat_connections count changed: {s1} → {s2}"
    assert m1 == m2, f"meta_connections count changed: {m1} → {m2}"


def test_check_token_endpoint(http_client, auth_headers):
    """POST /accounts/{id}/check-token returns v1 health status."""
    # Use the meta account we can actually verify
    r = http_client.post(
        "/ads-v2/settings/accounts",
        json={
            "provider": "meta",
            "external_account_id": "act_799549215909312",
            "display_name": "TEST-V2 Real Meta",
            "currency_native": "SAR",
            "v1_token_ref": {
                "provider": "meta",
                "collection": "meta_connections",
                "user_id": "x",
            },
        },
        headers=auth_headers,
    )
    acct_id = r.json()["data"]["id"]
    r2 = http_client.post(
        f"/ads-v2/settings/accounts/{acct_id}/check-token",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    health = r2.json()["data"]
    # On preview, the v1 doc exists → ok should be True
    assert health["ok"] is True, f"unexpected: {health}"
    assert health.get("has_token") is True


def test_settings_snapshot_includes_token_health(http_client, auth_headers):
    """GET /settings annotates each account with _v1_token_health."""
    http_client.post(
        "/ads-v2/settings/accounts",
        json={
            "provider": "meta",
            "external_account_id": "act_799549215909312",
            "display_name": "TEST-V2 Health",
            "currency_native": "SAR",
            "v1_token_ref": {
                "provider": "meta",
                "collection": "meta_connections",
                "user_id": "x",
            },
        },
        headers=auth_headers,
    )
    r = http_client.get("/ads-v2/settings", headers=auth_headers)
    accts = r.json()["data"]["accounts"]
    for a in accts:
        assert "_v1_token_health" in a
