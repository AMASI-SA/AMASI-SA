"""Webhook Token Store — unit + integration tests.

Locks in (per user spec 2026-06-26):
  • Plaintext returned EXACTLY ONCE (by `generate_token` + `save_*`).
  • Subsequent reads expose fingerprint ONLY — never the plaintext.
  • DB-stored token takes precedence over env var.
  • Env var still works as a fallback (preview/CI safety net).
  • Rotation: a second `save_*` replaces the previous token immediately.
  • Revoke flag hides the token from verification AND from /webhook-token
    metadata responses.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from integrations.qoyod.webhook_token_store import (
    generate_token, save_webhook_token, get_webhook_token,
    get_webhook_token_meta, revoke_webhook_token,
    verify_provided_token, _short_fingerprint,
)


# ─── Fixtures ────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    name = f"wtok_test_{uuid.uuid4().hex[:8]}"
    yield client[name]
    await client.drop_database(name)
    client.close()


# ─── Token generator ────────────────────────────────────────────────
class TestGenerateToken:
    def test_token_has_prefix_and_strong_length(self):
        t = generate_token()
        assert t.startswith("mzn_qoyod_prod_")
        # 48 raw bytes → 64 chars urlsafe + 15-char prefix = 79.
        assert len(t) == 79

    def test_token_is_unique_each_call(self):
        seen = {generate_token() for _ in range(50)}
        assert len(seen) == 50  # cryptographic-grade uniqueness

    def test_fingerprint_is_short_and_deterministic(self):
        t = generate_token()
        fp1 = _short_fingerprint(t)
        fp2 = _short_fingerprint(t)
        assert fp1 == fp2
        assert "…" in fp1
        # 4 chars + ellipsis + 4 chars = 9 chars
        assert len(fp1) == 9


# ─── Save / get / fingerprint ───────────────────────────────────────
@pytest.mark.asyncio
async def test_save_returns_metadata_only_no_plaintext(db):
    t = generate_token()
    meta = await save_webhook_token(db, "main", t)
    assert "token" not in meta
    assert "token_enc" not in meta
    assert meta["fingerprint"]
    assert "…" in meta["fingerprint"]


@pytest.mark.asyncio
async def test_save_then_get_round_trip(db):
    t = generate_token()
    await save_webhook_token(db, "main", t)
    got = await get_webhook_token(db, "main")
    assert got == t


@pytest.mark.asyncio
async def test_get_meta_never_exposes_plaintext(db):
    t = generate_token()
    await save_webhook_token(db, "main", t)
    meta = await get_webhook_token_meta(db, "main")
    # Convert to str and assert raw token cannot leak through any field.
    blob = repr(meta)
    assert t not in blob
    assert "token_enc" not in meta
    assert meta["configured"] is True


@pytest.mark.asyncio
async def test_meta_returns_none_when_not_configured(db):
    meta = await get_webhook_token_meta(db, "main")
    assert meta is None


@pytest.mark.asyncio
async def test_rotate_replaces_previous_token(db):
    t1 = generate_token()
    await save_webhook_token(db, "main", t1)
    t2 = generate_token()
    await save_webhook_token(db, "main", t2)
    assert t1 != t2
    got = await get_webhook_token(db, "main")
    assert got == t2
    # Only one row per tenant
    count = await db.qoyod_webhook_tokens.count_documents(
        {"user_id": "main"})
    assert count == 1


@pytest.mark.asyncio
async def test_revoke_hides_token_from_get_and_verify(db):
    t = generate_token()
    await save_webhook_token(db, "main", t)
    assert await get_webhook_token(db, "main") == t
    revoked = await revoke_webhook_token(db, "main")
    assert revoked is True
    # get_webhook_token must return None after revocation
    assert await get_webhook_token(db, "main") is None
    # meta still exists but `configured=False`
    meta = await get_webhook_token_meta(db, "main")
    assert meta["configured"] is False
    # Verification must FAIL for the revoked token
    assert await verify_provided_token(
        db, provided=t, user_id="main", env_fallback=None) is False


# ─── Verify provided token ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_verify_db_token_succeeds(db):
    t = generate_token()
    await save_webhook_token(db, "main", t)
    assert await verify_provided_token(
        db, provided=t, user_id="main", env_fallback=None) is True


@pytest.mark.asyncio
async def test_verify_wrong_token_fails(db):
    t = generate_token()
    await save_webhook_token(db, "main", t)
    assert await verify_provided_token(
        db, provided="wrong", user_id="main",
        env_fallback=None) is False


@pytest.mark.asyncio
async def test_verify_falls_back_to_env_when_no_db_token(db):
    """Preview/CI behaviour: env var still works when DB is empty."""
    assert await verify_provided_token(
        db, provided="env-secret", user_id="main",
        env_fallback="env-secret") is True


@pytest.mark.asyncio
async def test_db_token_takes_precedence_over_env(db):
    """When DB has a token, env fallback is IGNORED."""
    db_token = generate_token()
    await save_webhook_token(db, "main", db_token)
    # An attacker who knows the old env value cannot bypass.
    assert await verify_provided_token(
        db, provided="env-secret", user_id="main",
        env_fallback="env-secret") is False
    # But the DB token still works.
    assert await verify_provided_token(
        db, provided=db_token, user_id="main",
        env_fallback="env-secret") is True


@pytest.mark.asyncio
async def test_verify_returns_false_on_empty_provided(db):
    assert await verify_provided_token(
        db, provided="", user_id="main", env_fallback="x") is False


@pytest.mark.asyncio
async def test_verify_records_last_verified_at(db):
    t = generate_token()
    await save_webhook_token(db, "main", t)
    meta_before = await get_webhook_token_meta(db, "main")
    assert meta_before["last_verified_at"] is None
    await verify_provided_token(db, provided=t, user_id="main")
    meta_after = await get_webhook_token_meta(db, "main")
    assert meta_after["last_verified_at"] is not None


@pytest.mark.asyncio
async def test_revoke_returns_false_when_already_revoked(db):
    """Idempotency on the revoke action."""
    t = generate_token()
    await save_webhook_token(db, "main", t)
    assert await revoke_webhook_token(db, "main") is True
    # Second revoke is a no-op
    assert await revoke_webhook_token(db, "main") is False


# ─── End-to-end through the webhook verifier closure ────────────────
@pytest.mark.asyncio
async def test_webhook_verifier_uses_db_token_when_present(db, monkeypatch):
    """The closure factory in webhook.py must consult the DB FIRST."""
    from fastapi import HTTPException
    from integrations.qoyod.webhook import _make_verify_token

    monkeypatch.setenv("QOYOD_WEBHOOK_TOKEN", "env-only")
    t = generate_token()
    await save_webhook_token(db, "main", t)
    verifier = _make_verify_token(db)
    # Correct DB token → pass
    assert await verifier(x_webhook_token=t) is True
    # env-only value MUST NOT pass once DB is configured
    with pytest.raises(HTTPException) as exc:
        await verifier(x_webhook_token="env-only")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_webhook_verifier_falls_back_to_env_when_db_empty(
    db, monkeypatch,
):
    from integrations.qoyod.webhook import _make_verify_token
    monkeypatch.setenv("QOYOD_WEBHOOK_TOKEN", "env-secret-xyz")
    verifier = _make_verify_token(db)
    assert await verifier(x_webhook_token="env-secret-xyz") is True


@pytest.mark.asyncio
async def test_webhook_verifier_503_when_nothing_configured(
    db, monkeypatch,
):
    from fastapi import HTTPException
    from integrations.qoyod.webhook import _make_verify_token
    monkeypatch.delenv("QOYOD_WEBHOOK_TOKEN", raising=False)
    verifier = _make_verify_token(db)
    with pytest.raises(HTTPException) as exc:
        await verifier(x_webhook_token="anything")
    assert exc.value.status_code == 503
