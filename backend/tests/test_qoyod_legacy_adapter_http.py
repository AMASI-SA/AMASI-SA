"""HTTP integration tests for Legacy-Adapter pipeline branches.

End-to-end via the real /api/integrations/qoyod/webhook route, hitting
the test database. Covers three scenarios:

  1. Legacy payload WITH items[]    → pipeline progresses past RECEIVED
  2. Legacy payload WITHOUT items, toggle OFF (default)
        → DEAD_LETTER via FAILED_VALIDATION
        with code `missing_items_no_enricher`
  3. Legacy payload WITHOUT items, toggle ON
        → DEAD_LETTER via NEEDS_ENRICHMENT → FAILED_ENRICHMENT
        with code `enricher_not_implemented`
        and `enrichment_fallback_used=True` persisted

These tests share the live MongoDB but use unique idempotency keys
so they never collide with each other or with prior iterations.
"""
from __future__ import annotations

import os
import secrets
import uuid

import pytest
import pytest_asyncio
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


API_BASE = os.environ["REACT_APP_BACKEND_URL"] \
    if os.environ.get("REACT_APP_BACKEND_URL") \
    else "http://localhost:8001"

# Webhook tests do NOT need a JWT — only the X-Webhook-Token.
WEBHOOK_TOKEN_ENV = os.environ.get("QOYOD_WEBHOOK_TOKEN", "")


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_test_inbox_rows():
    """Wipe any inbox rows this test class created so we don't pollute
    other test files that share the same MongoDB. We key on the unique
    `TEST-` prefix in `salla_order_number` set by `_legacy_no_items()`."""
    yield
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        await client[os.environ["DB_NAME"]].integration_inbox.delete_many(
            {"salla_order_number": {"$regex": "^TEST-"}})
    finally:
        client.close()


def _legacy_no_items() -> dict:
    return {
        "event_type": "order_created",
        "order_number": f"TEST-{uuid.uuid4().hex[:8]}",
        "order_id":     f"id-{uuid.uuid4().hex[:8]}",
        "created_at":   "2026-06-26 07:00:16.000000",
        "total_amount": "139.51",
        "subtotal":     "105",
        "shipping_cost": "22.61",
        "payment_method": "mada",
        "currency":      "SAR",
        "customer_name": "عميل تجريبي",
        "customer_mobile": "+966500000000",
        "order_status":  "بإنتظار المراجعة",
        "order_status_slug": "under_review",
        "received_from": "make",
    }


def _legacy_with_items() -> dict:
    body = _legacy_no_items()
    body["order_status"]      = "تم التنفيذ"
    body["order_status_slug"] = "completed"
    body["items"] = [
        {"sku": "SKU-A", "name": "منتج 1", "quantity": 2,
         "price": {"amount": 50, "currency": "SAR"}},
    ]
    return body


async def _post_webhook(body: dict, token: str, idem_key: str) -> httpx.Response:
    headers = {
        "X-Webhook-Token":   token,
        "X-Idempotency-Key": idem_key,
        "Content-Type":      "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{API_BASE}/api/integrations/qoyod/webhook",
            json=body, headers=headers)


@pytest.mark.asyncio
async def test_skip_when_no_webhook_token_configured():
    """Smoke: when neither DB nor env token is set, the route returns 503."""
    if WEBHOOK_TOKEN_ENV:
        pytest.skip("env token configured — 503 path unreachable")


@pytest.mark.asyncio
async def test_legacy_payload_with_items_progresses_past_received(db):
    """The adapter detects legacy shape, populates items, and validate()
    accepts the canonical payload. With items[] present, the row does
    NOT land in DEAD_LETTER from the missing-items branch."""
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured in this env")
    idem = f"legacy-with-items-{secrets.token_hex(6)}"
    body = _legacy_with_items()
    resp = await _post_webhook(body, WEBHOOK_TOKEN_ENV, idem)
    assert resp.status_code == 200, resp.text
    j = resp.json()
    # The pipeline must NOT short-circuit via the missing-items branch.
    stage = j.get("pipeline_stage")
    assert stage not in ("FAILED_VALIDATION", "FAILED_ENRICHMENT", "NEEDS_ENRICHMENT"), \
        f"unexpected branch with items present: stage={stage} err={j.get('error')}"
    # Adapter metadata persisted
    row = await db.integration_inbox.find_one({"idempotency_key": idem})
    assert row is not None
    assert row["adapter_meta"]["adapter_applied"] is True
    assert row["adapter_meta"]["items_source"] == "items"


@pytest.mark.asyncio
async def test_missing_items_toggle_off_dead_letters_via_failed_validation(db):
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured")
    # Force fallback OFF (the default — but be explicit)
    await db.qoyod_settings.update_one(
        {"user_id": "main"},
        {"$set": {"enrichment_fallback_enabled": False}},
        upsert=True)
    idem = f"missing-off-{secrets.token_hex(6)}"
    body = _legacy_no_items()
    resp = await _post_webhook(body, WEBHOOK_TOKEN_ENV, idem)
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["pipeline_stage"] == "DEAD_LETTER"
    assert j["error"]["code"] == "missing_items_no_enricher"
    row = await db.integration_inbox.find_one({"idempotency_key": idem})
    assert row["enrichment_fallback_used"] is False
    assert row["adapter_meta"]["items_source"] == "missing"
    # Stage history must include FAILED_VALIDATION before DEAD_LETTER
    stages = [h["to_stage"] for h in row.get("stage_history", [])]
    assert "FAILED_VALIDATION" in stages
    assert "DEAD_LETTER" in stages
    # NEEDS_ENRICHMENT must NOT appear when toggle is OFF
    assert "NEEDS_ENRICHMENT" not in stages


@pytest.mark.asyncio
async def test_missing_items_toggle_on_routes_via_needs_enrichment(db):
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured")
    await db.qoyod_settings.update_one(
        {"user_id": "main"},
        {"$set": {"enrichment_fallback_enabled": True}},
        upsert=True)
    try:
        idem = f"missing-on-{secrets.token_hex(6)}"
        body = _legacy_no_items()
        resp = await _post_webhook(body, WEBHOOK_TOKEN_ENV, idem)
        assert resp.status_code == 200, resp.text
        j = resp.json()
        # Terminal is DEAD_LETTER because the enricher stub is wired but
        # not yet implemented (intentional, per user spec).
        assert j["pipeline_stage"] == "DEAD_LETTER"
        assert j["error"]["code"] == "enricher_not_implemented"
        row = await db.integration_inbox.find_one({"idempotency_key": idem})
        assert row["enrichment_fallback_used"] is True
        stages = [h["to_stage"] for h in row.get("stage_history", [])]
        # The hallmark of the toggle-on path:
        assert "NEEDS_ENRICHMENT" in stages
        assert "FAILED_ENRICHMENT" in stages
        assert "DEAD_LETTER" in stages
        # FAILED_VALIDATION must NOT appear on this branch
        assert "FAILED_VALIDATION" not in stages
    finally:
        # Restore safe default so other tests aren't affected
        await db.qoyod_settings.update_one(
            {"user_id": "main"},
            {"$set": {"enrichment_fallback_enabled": False}})


@pytest.mark.asyncio
async def test_legacy_extras_persisted_for_audit(db):
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured")
    idem = f"extras-{secrets.token_hex(6)}"
    body = _legacy_no_items()
    body["utm_source"] = "snapchat"
    body["utm_campaign"] = "test-campaign-123"
    body["device"] = "mobile"
    await _post_webhook(body, WEBHOOK_TOKEN_ENV, idem)
    row = await db.integration_inbox.find_one({"idempotency_key": idem})
    extras = row["adapter_meta"]["legacy_extras"]
    assert extras.get("utm_source") == "snapchat"
    assert extras.get("utm_campaign") == "test-campaign-123"
    assert extras.get("device") == "mobile"


@pytest.mark.asyncio
async def test_put_settings_round_trip_enrichment_fallback_enabled():
    """Regression for iter-259 bug: SettingsPatch (extra='forbid') had
    been missing the `enrichment_fallback_enabled` field, so the toggle
    could only be read (via GET) and never set (PUT returned 422
    extra_forbidden). Verifies the field is now accepted both ways."""
    async with httpx.AsyncClient(timeout=20) as client:
        # Login
        r = await client.post(
            f"{API_BASE}/api/auth/login",
            json={"email": "admin@hesab.app", "password": "admin123"})
        assert r.status_code == 200, r.text
        token = r.json().get("access_token") or r.json().get("token")
        h = {"Authorization": f"Bearer {token}"}
        try:
            # Flip ON
            r1 = await client.put(
                f"{API_BASE}/api/integrations/qoyod/settings",
                headers=h, json={"enrichment_fallback_enabled": True})
            assert r1.status_code == 200, r1.text
            assert r1.json().get("enrichment_fallback_enabled") is True
            # Confirm via GET
            r2 = await client.get(
                f"{API_BASE}/api/integrations/qoyod/settings", headers=h)
            assert r2.json().get("enrichment_fallback_enabled") is True
        finally:
            # Restore default OFF so other tests are unaffected
            r3 = await client.put(
                f"{API_BASE}/api/integrations/qoyod/settings",
                headers=h, json={"enrichment_fallback_enabled": False})
            assert r3.status_code == 200
            assert r3.json().get("enrichment_fallback_enabled") is False
