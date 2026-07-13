"""Tests for TEMP owner-promotion endpoint (2026-02)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import mongomock_motor  # noqa: F401
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from temporary_owner_promotion_routes import (
    make_owner_promotion_router, TARGET_ID, TARGET_EMAIL,
)

VALID_TOKEN = "diag-token-fixture-value-01234567"


@pytest_asyncio.fixture
async def db():
    return mongomock_motor.AsyncMongoMockClient()["test_owner_promo"]


@pytest_asyncio.fixture
async def http(db):
    app = FastAPI()
    app.include_router(make_owner_promotion_router(db), prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_env():
    prev = os.environ.pop("DIAGNOSTIC_TOKEN", None)
    yield
    if prev is not None:
        os.environ["DIAGNOSTIC_TOKEN"] = prev
    else:
        os.environ.pop("DIAGNOSTIC_TOKEN", None)


async def _seed_user(db, role: str = "user", *, extra: dict | None = None):
    doc = {
        "id":            TARGET_ID,
        "email":         TARGET_EMAIL,
        "role":          role,
        "password_hash": "SUPER_SECRET_HASH",
        "created_at":    datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        doc.update(extra)
    await db.users.insert_one(doc)


PATH = "/api/admin/promote-primary-owner-secure-temp"
HDR  = {"X-Diagnostic-Token": VALID_TOKEN}


# ── Guard tests ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_env_missing_returns_503(db, http):
    await _seed_user(db)
    r = await http.post(PATH, headers=HDR)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_missing_header_returns_403(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_user(db)
    r = await http.post(PATH)   # no header
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_wrong_token_returns_403(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_user(db)
    r = await http.post(PATH, headers={"X-Diagnostic-Token": "nope"})
    assert r.status_code == 403


# ── Conflict tests ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_conflict_id_matches_different_email(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await db.users.insert_one(
        {"id": TARGET_ID, "email": "wrong@example.com", "role": "user"})
    r = await http.post(PATH, headers=HDR)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_conflict_email_matches_different_id(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await db.users.insert_one(
        {"id": "different-id-xxxx", "email": TARGET_EMAIL, "role": "user"})
    r = await http.post(PATH, headers=HDR)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_user_not_found_returns_404(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    r = await http.post(PATH, headers=HDR)
    assert r.status_code == 404


# ── Happy path ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_happy_path_promotes_and_audits(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_user(db, role="user")

    r = await http.post(PATH, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "ok": True, "status": "promoted",
        "matched_count": 1, "modified_count": 1,
        "before_role": "user", "after_role": "owner",
    }

    # DB reflects change; other fields untouched.
    user = await db.users.find_one({"id": TARGET_ID})
    assert user["role"] == "owner"
    assert user["email"] == TARGET_EMAIL
    assert user["password_hash"] == "SUPER_SECRET_HASH"
    assert "is_owner" not in user   # derived field, never persisted

    # Audit row.
    audit = await db.owner_promotion_audit.find_one({})
    assert audit["action"]         == "promote_primary_owner"
    assert audit["target_user_id"] == TARGET_ID
    assert audit["target_email"]   == TARGET_EMAIL
    assert audit["before_role"]    == "user"
    assert audit["after_role"]     == "owner"
    assert audit["source"]         == "temporary_secure_endpoint"


# ── Idempotency ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_idempotent_when_already_owner(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_user(db, role="owner")

    r = await http.post(PATH, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["status"]         == "already_owner"
    assert body["modified_count"] == 0
    assert body["before_role"]    == "owner"
    assert body["after_role"]     == "owner"


# ── No other user is touched ───────────────────────────────────
@pytest.mark.asyncio
async def test_only_target_user_is_touched(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_user(db, role="user")
    await db.users.insert_one(
        {"id": "other-1", "email": "other1@x.com", "role": "user"})
    await db.users.insert_one(
        {"id": "other-2", "email": "other2@x.com", "role": "admin"})

    r = await http.post(PATH, headers=HDR)
    assert r.status_code == 200

    others = await db.users.find({"id": {"$ne": TARGET_ID}}).to_list(10)
    for u in others:
        assert u["role"] in ("user", "admin")


# ── No secrets leak in response ────────────────────────────────
@pytest.mark.asyncio
async def test_response_never_leaks_secrets(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_user(db, role="user",
                     extra={"access_token": "ACCESS_XYZ",
                            "refresh_token": "REFRESH_XYZ"})

    r = await http.post(PATH, headers=HDR)
    text = r.text
    for forbidden in ("SUPER_SECRET_HASH", "ACCESS_XYZ", "REFRESH_XYZ",
                       VALID_TOKEN, "password"):
        assert forbidden not in text, f"leaked: {forbidden}"
