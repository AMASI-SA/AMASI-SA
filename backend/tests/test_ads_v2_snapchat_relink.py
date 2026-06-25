"""Ads V2 — Snapchat re-link safety tests.

Verifies the strict user-mandated safety rules:
  1. V1 token is NOT touched on /start, /manual, /compare, /discard.
  2. /approve appends a `legacy_versions[]` entry on V1 before swap.
  3. Pending tokens carry status pending → approved | discarded only.
  4. Comparison endpoint returns side-by-side org/account snapshots.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

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


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _v1_snapshot(user_id):
    async def _g():
        doc = await _db().snapchat_connections.find_one({"user_id": user_id})
        return doc
    return asyncio.run(_g())


def _get_user_id():
    async def _g():
        u = await _db().users.find_one({"email": LOGIN_EMAIL})
        return u["id"]
    return asyncio.run(_g())


# ─────────────────────────────────────────────────────────────────────
# 1. /start preserves V1 completely
# ─────────────────────────────────────────────────────────────────────
def test_relink_start_does_not_touch_v1(http_client, auth_headers):
    uid = _get_user_id()
    before = _v1_snapshot(uid)
    r = http_client.post(
        "/ads-v2/settings/snapchat/relink/start",
        headers=auth_headers,
    )
    if r.status_code == 400:
        pytest.skip("V1 Snapchat config missing on this env")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "pending_id" in d
    assert "oauth_url" in d
    # OAuth URL points at Snapchat's authorize endpoint
    assert d["oauth_url"].startswith(
        "https://accounts.snapchat.com/login/oauth2/authorize",
    )
    # V1 doc identical
    after = _v1_snapshot(uid)
    if before and after:
        for k in ("access_token", "refresh_token", "client_id",
                  "client_secret", "redirect_uri"):
            assert before.get(k) == after.get(k), \
                f"V1 field '{k}' changed during /start — FORBIDDEN"


# ─────────────────────────────────────────────────────────────────────
# 2. /pending returns the awaiting_callback row but never the secret
# ─────────────────────────────────────────────────────────────────────
def test_relink_pending_omits_secrets(http_client, auth_headers):
    r = http_client.get(
        "/ads-v2/settings/snapchat/relink/pending",
        headers=auth_headers,
    )
    assert r.status_code == 200
    for p in r.json()["data"]:
        # API must never return tokens in list responses
        assert "access_token" not in p
        assert "refresh_token" not in p


# ─────────────────────────────────────────────────────────────────────
# 3. Pending without access_token → can't be approved
# ─────────────────────────────────────────────────────────────────────
def test_approve_awaiting_callback_returns_404(http_client, auth_headers):
    """Approval requires a pending row in 'pending' status (i.e. OAuth
    or manual paste finished). awaiting_callback rows are NOT approvable
    until the OAuth round-trip stores tokens."""
    r = http_client.post(
        "/ads-v2/settings/snapchat/relink/start",
        headers=auth_headers,
    )
    if r.status_code == 400:
        pytest.skip("V1 Snapchat config missing on this env")
    pid = r.json()["pending_id"]
    a = http_client.post(
        f"/ads-v2/settings/snapchat/relink/{pid}/approve",
        headers=auth_headers,
    )
    assert a.status_code == 404


# ─────────────────────────────────────────────────────────────────────
# 4. /manual stores a pending token + probes; V1 untouched
# ─────────────────────────────────────────────────────────────────────
def test_relink_manual_stores_pending_v1_untouched(http_client, auth_headers):
    uid = _get_user_id()
    before = _v1_snapshot(uid)
    r = http_client.post(
        "/ads-v2/settings/snapchat/relink/manual",
        json={"access_token": "definitely_fake_token_xx_yyy",
              "refresh_token": "definitely_fake_refresh"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    pid = d["pending_id"]
    # The probe ran (will report unauthorized for the bogus token)
    assert "probe" in d
    assert d["probe"]["valid"] is False
    # V1 untouched
    after = _v1_snapshot(uid)
    if before and after:
        for k in ("access_token", "refresh_token"):
            assert before.get(k) == after.get(k)
    # Tear-down: discard the bogus pending
    dr = http_client.post(
        f"/ads-v2/settings/snapchat/relink/{pid}/discard",
        headers=auth_headers,
    )
    assert dr.status_code == 200


# ─────────────────────────────────────────────────────────────────────
# 5. /compare returns side-by-side snapshot + diff (even for bogus token)
# ─────────────────────────────────────────────────────────────────────
def test_relink_compare_side_by_side(http_client, auth_headers):
    # Create a manual pending row
    m = http_client.post(
        "/ads-v2/settings/snapchat/relink/manual",
        json={"access_token": "fake_for_compare_test_____"},
        headers=auth_headers,
    )
    pid = m.json()["pending_id"]
    r = http_client.post(
        f"/ads-v2/settings/snapchat/relink/{pid}/compare",
        headers=auth_headers,
    )
    assert r.status_code == 200
    d = r.json()
    for k in ("old", "new", "diff"):
        assert k in d
    for s in (d["old"], d["new"]):
        for k in ("valid", "organizations", "ad_accounts",
                  "can_access_self_service", "can_access_riyadh"):
            assert k in s
    for k in ("orgs_added", "orgs_removed",
              "accounts_added", "accounts_removed"):
        assert k in d["diff"]
    # Clean up
    http_client.post(
        f"/ads-v2/settings/snapchat/relink/{pid}/discard",
        headers=auth_headers,
    )


# ─────────────────────────────────────────────────────────────────────
# 6. /discard marks the row but doesn't actually delete it (audit)
# ─────────────────────────────────────────────────────────────────────
def test_relink_discard_is_soft(http_client, auth_headers):
    uid = _get_user_id()
    m = http_client.post(
        "/ads-v2/settings/snapchat/relink/manual",
        json={"access_token": "soft_discard_token"},
        headers=auth_headers,
    )
    pid = m.json()["pending_id"]
    http_client.post(
        f"/ads-v2/settings/snapchat/relink/{pid}/discard",
        headers=auth_headers,
    )
    async def _check():
        return await _db().ads_v2_pending_tokens.find_one(
            {"id": pid, "user_id": uid},
        )
    row = asyncio.run(_check())
    assert row is not None, "pending row must be retained for audit"
    assert row["status"] == "discarded"
    assert row.get("discarded_at") is not None


# ─────────────────────────────────────────────────────────────────────
# 7. Approve appends legacy_versions[] entry and updates V1 atomically
# ─────────────────────────────────────────────────────────────────────
def test_approve_appends_legacy_and_swaps(http_client, auth_headers):
    uid = _get_user_id()
    v1_before = _v1_snapshot(uid)
    if not v1_before or not v1_before.get("access_token"):
        pytest.skip("no live V1 Snapchat token to swap against")

    # Manually create an approve-ready pending row directly (we can't
    # run a real OAuth handshake from tests)
    pid = uuid.uuid4().hex
    fake_new_token = f"test_swap_{uuid.uuid4().hex[:12]}"
    fake_new_refresh = f"test_swap_refresh_{uuid.uuid4().hex[:8]}"

    async def _seed():
        from datetime import datetime, timezone
        await _db().ads_v2_pending_tokens.insert_one({
            "id": pid, "user_id": uid, "provider": "snapchat",
            "status": "pending",
            "access_token": fake_new_token,
            "refresh_token": fake_new_refresh,
            "access_token_expires_at": datetime.now(timezone.utc).isoformat(),
            "source": "test",
            "comparison_snapshot": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    asyncio.run(_seed())

    try:
        r = http_client.post(
            f"/ads-v2/settings/snapchat/relink/{pid}/approve",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True

        v1_after = _v1_snapshot(uid)
        # 1) V1 NOW has the new token
        assert v1_after["access_token"] == fake_new_token
        # 2) legacy_versions contains the previous token
        legacy = v1_after.get("legacy_versions") or []
        assert len(legacy) >= 1
        last_entry = legacy[-1]
        assert last_entry["access_token"] == v1_before["access_token"]
        assert last_entry["archived_by_relink_id"] == pid
        # 3) pending row marked approved
        async def _read():
            return await _db().ads_v2_pending_tokens.find_one({"id": pid})
        pend = asyncio.run(_read())
        assert pend["status"] == "approved"
        # 4) ads_sync_logs has the relink event
        async def _log_count():
            return await _db().ads_sync_logs.count_documents({
                "user_id": uid,
                "event": "account_relinked_v1",
                "details.pending_id": pid,
            })
        assert asyncio.run(_log_count()) >= 1

    finally:
        # ── Restore V1 to its original state so subsequent tests / app
        # are unaffected. This is critical because we just swapped a
        # real V1 token for a fake one. ──
        async def _restore():
            db = _db()
            await db.snapchat_connections.update_one(
                {"user_id": uid},
                {
                    "$set": {
                        "access_token":  v1_before["access_token"],
                        "refresh_token": v1_before.get("refresh_token"),
                        "access_token_expires_at":
                            v1_before.get("access_token_expires_at"),
                        "updated_at":    v1_before.get("updated_at"),
                    },
                    "$pop": {"legacy_versions": 1},  # remove the test entry
                },
            )
            # purge test pending
            await db.ads_v2_pending_tokens.delete_one({"id": pid})
            await db.ads_sync_logs.delete_many({
                "user_id": uid, "details.pending_id": pid,
            })
        asyncio.run(_restore())


# ─────────────────────────────────────────────────────────────────────
# 8. Compare must NOT require approve — read-only safety check
# ─────────────────────────────────────────────────────────────────────
def test_compare_does_not_modify_v1(http_client, auth_headers):
    uid = _get_user_id()
    before = _v1_snapshot(uid)
    if not before:
        pytest.skip("no V1 Snapchat doc")

    m = http_client.post(
        "/ads-v2/settings/snapchat/relink/manual",
        json={"access_token": "compare_readonly_test"},
        headers=auth_headers,
    )
    pid = m.json()["pending_id"]
    http_client.post(
        f"/ads-v2/settings/snapchat/relink/{pid}/compare",
        headers=auth_headers,
    )

    after = _v1_snapshot(uid)
    for k in ("access_token", "refresh_token"):
        assert before.get(k) == after.get(k), \
            f"V1.{k} mutated by /compare — FORBIDDEN"

    http_client.post(
        f"/ads-v2/settings/snapchat/relink/{pid}/discard",
        headers=auth_headers,
    )
