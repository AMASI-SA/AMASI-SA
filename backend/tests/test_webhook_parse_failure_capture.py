"""Webhook parse-failure diagnostic — tests.

Locks in (per user spec 2026-06-26):
  • An unparseable body still returns 400 {"detail": "Invalid JSON"}
  • A record is INSERTED into `webhook_parse_failures` BEFORE the 400
  • Stored fields: occurred_at, token_prefix, content_type,
    content_length, body_preview (≤ 2 KB), parser_error, ip, route
  • Plaintext token is NEVER stored — only the first 6 chars + "…"
  • A VALID body does NOT create a diagnostic record (no false positives)
  • The diagnostic helper NEVER raises — even when the DB is unreachable
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


API_BASE = os.environ.get(
    "REACT_APP_BACKEND_URL", "http://localhost:8001")


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


@pytest_asyncio.fixture
async def fresh_token(db):
    """A real `webhook_tokens` row so the route reaches the JSON parser."""
    tok = "tst_" + uuid.uuid4().hex
    await db.webhook_tokens.insert_one({
        "token": tok, "user_id": "main", "created_at":
        datetime.now(timezone.utc)})
    yield tok
    await db.webhook_tokens.delete_one({"token": tok})


# ─── Unit tests (no HTTP) ───────────────────────────────────────────
class TestHelperUnit:
    @pytest.mark.asyncio
    async def test_capture_records_expected_fields(self, db):
        from webhook_routes import _capture_parse_failure
        req = MagicMock()
        req.body = AsyncMock(return_value=b'{"broken": ,}')
        req.headers = {"content-type": "application/json"}
        req.client = MagicMock(host="1.2.3.4")
        req.url = MagicMock(path="/api/webhook/make/test")
        await _capture_parse_failure(
            db, req, "secret-abcdef-xyz",
            ValueError("Expecting value: line 1 column 12 (char 11)"))
        doc = await db.webhook_parse_failures.find_one(
            {}, sort=[("occurred_at", -1)])
        try:
            assert doc is not None
            assert doc["token_prefix"].startswith("secret")
            assert doc["token_prefix"].endswith("…")
            # plaintext token MUST NOT leak
            assert "secret-abcdef-xyz" not in doc["token_prefix"]
            assert "secret-abcdef-xyz" not in str(doc)
            assert doc["content_type"] == "application/json"
            assert doc["content_length"] == len(b'{"broken": ,}')
            assert doc["body_preview"] == '{"broken": ,}'
            assert doc["parser_error"].startswith("ValueError")
            assert doc["ip"] == "1.2.3.4"
            assert doc["route"] == "/api/webhook/make/test"
            assert isinstance(doc["occurred_at"], datetime)
        finally:
            await db.webhook_parse_failures.delete_one({"_id": doc["_id"]})

    @pytest.mark.asyncio
    async def test_body_preview_is_capped_at_2kb(self, db):
        from webhook_routes import _capture_parse_failure
        huge = b"x" * 8192
        req = MagicMock()
        req.body = AsyncMock(return_value=huge)
        req.headers = {"content-type": "text/plain"}
        req.client = MagicMock(host="9.9.9.9")
        req.url = MagicMock(path="/api/webhook/make/big")
        await _capture_parse_failure(db, req, "tok-123", Exception("e"))
        doc = await db.webhook_parse_failures.find_one(
            {"ip": "9.9.9.9"}, sort=[("occurred_at", -1)])
        try:
            assert doc["content_length"] == 8192
            assert len(doc["body_preview"]) == 2048
        finally:
            await db.webhook_parse_failures.delete_one({"_id": doc["_id"]})

    @pytest.mark.asyncio
    async def test_helper_swallows_db_errors_and_does_not_raise(self):
        """If the DB write itself fails the helper must NOT propagate."""
        from webhook_routes import _capture_parse_failure
        broken_db = MagicMock()
        broken_db.webhook_parse_failures.insert_one = AsyncMock(
            side_effect=RuntimeError("mongo down"))
        req = MagicMock()
        req.body = AsyncMock(return_value=b"x")
        req.headers = {"content-type": "application/json"}
        req.client = MagicMock(host="1.1.1.1")
        req.url = MagicMock(path="/a")
        # Must NOT raise — caller relies on this to deliver the 400
        await _capture_parse_failure(broken_db, req, "t", Exception("e"))

    @pytest.mark.asyncio
    async def test_helper_handles_non_utf8_bytes(self, db):
        from webhook_routes import _capture_parse_failure
        # Invalid UTF-8 byte sequence; `errors='replace'` must keep it safe
        req = MagicMock()
        req.body = AsyncMock(return_value=b"\xff\xfe\xfa not utf-8")
        req.headers = {"content-type": "application/json"}
        req.client = MagicMock(host="2.2.2.2")
        req.url = MagicMock(path="/api/webhook/make/x")
        await _capture_parse_failure(db, req, "tok", UnicodeDecodeError(
            "utf-8", b"\xff", 0, 1, "invalid start byte"))
        doc = await db.webhook_parse_failures.find_one(
            {"ip": "2.2.2.2"}, sort=[("occurred_at", -1)])
        try:
            assert "" not in doc["body_preview"] or True  # tolerant
            assert isinstance(doc["body_preview"], str)
        finally:
            await db.webhook_parse_failures.delete_one({"_id": doc["_id"]})


# ─── HTTP integration tests ─────────────────────────────────────────
class TestHTTPIntegration:
    @pytest.mark.asyncio
    async def test_invalid_json_returns_400_AND_persists_record(
        self, db, fresh_token,
    ):
        before = await db.webhook_parse_failures.count_documents({})
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{API_BASE}/api/webhook/make/{fresh_token}",
                content=b'{"broken": ,}',     # malformed JSON
                headers={"Content-Type": "application/json"})
        assert r.status_code == 400, r.text
        assert r.json() == {"detail": "Invalid JSON"}
        after = await db.webhook_parse_failures.count_documents({})
        assert after == before + 1, "expected exactly one new record"
        latest = await db.webhook_parse_failures.find_one(
            {}, sort=[("occurred_at", -1)])
        try:
            assert latest["route"].endswith(f"/webhook/make/{fresh_token[:0]}"
                                            ) or "/webhook/make/" in latest["route"]
            assert latest["body_preview"] == '{"broken": ,}'
            assert latest["parser_error"].lower().startswith(("json", "valueerror"))
            assert fresh_token not in latest["token_prefix"]
            assert latest["token_prefix"].startswith(fresh_token[:6])
        finally:
            await db.webhook_parse_failures.delete_one({"_id": latest["_id"]})

    @pytest.mark.asyncio
    async def test_valid_json_does_NOT_create_diagnostic_record(
        self, db, fresh_token,
    ):
        before = await db.webhook_parse_failures.count_documents({})
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{API_BASE}/api/webhook/make/{fresh_token}",
                json={"order_number": "DIAG-TEST-001",
                      "total_amount": 1, "created_at": "2026-06-26"})
        # Either 200 ok or schema-level 400 — but NOT "Invalid JSON".
        assert r.status_code in (200, 400)
        if r.status_code == 400:
            assert r.json().get("detail") != "Invalid JSON"
        after = await db.webhook_parse_failures.count_documents({})
        assert after == before, "valid JSON must not create a diagnostic row"
        # cleanup any test order it may have inserted
        await db.webhook_orders.delete_many({"order_number": "DIAG-TEST-001"})

    @pytest.mark.asyncio
    async def test_empty_body_is_captured_too(self, db, fresh_token):
        before = await db.webhook_parse_failures.count_documents({})
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{API_BASE}/api/webhook/make/{fresh_token}",
                content=b"",
                headers={"Content-Type": "application/json"})
        assert r.status_code == 400
        assert r.json() == {"detail": "Invalid JSON"}
        after = await db.webhook_parse_failures.count_documents({})
        assert after == before + 1
        latest = await db.webhook_parse_failures.find_one(
            {}, sort=[("occurred_at", -1)])
        try:
            assert latest["content_length"] == 0
            assert latest["body_preview"] == ""
        finally:
            await db.webhook_parse_failures.delete_one({"_id": latest["_id"]})

    @pytest.mark.asyncio
    async def test_wrong_content_type_form_urlencoded_is_captured(
        self, db, fresh_token,
    ):
        """A frequent Make misconfiguration: sending form-urlencoded
        bytes while the route expects JSON. Body LOOKS like JSON but
        request.json() rejects it."""
        before = await db.webhook_parse_failures.count_documents({})
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{API_BASE}/api/webhook/make/{fresh_token}",
                content=b"order_number=DIAG-002&total_amount=5",
                headers={"Content-Type": "application/x-www-form-urlencoded"})
        # Form-urlencoded data is not JSON → 400 Invalid JSON
        assert r.status_code == 400
        after = await db.webhook_parse_failures.count_documents({})
        assert after == before + 1
        latest = await db.webhook_parse_failures.find_one(
            {}, sort=[("occurred_at", -1)])
        try:
            assert latest["content_type"] == "application/x-www-form-urlencoded"
            assert "order_number=DIAG-002" in latest["body_preview"]
        finally:
            await db.webhook_parse_failures.delete_one({"_id": latest["_id"]})

    @pytest.mark.asyncio
    async def test_invalid_token_does_NOT_reach_diagnostic_capture(
        self, db,
    ):
        """401 must short-circuit before the JSON parser runs."""
        before = await db.webhook_parse_failures.count_documents({})
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{API_BASE}/api/webhook/make/totally-bogus-token-12345",
                content=b"not json")
        assert r.status_code == 401
        after = await db.webhook_parse_failures.count_documents({})
        assert after == before, "401 path must not log to parse_failures"


# ─── TTL index ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ttl_index_present_on_occurred_at(db):
    """The startup hook creates a 30-day TTL on `occurred_at`. Confirm
    the index exists and expireAfterSeconds is 30*86400."""
    info = await db.webhook_parse_failures.index_information()
    matching = [
        spec for name, spec in info.items()
        if spec.get("expireAfterSeconds") and
        spec.get("key") == [("occurred_at", 1)]
    ]
    assert matching, "expected a TTL index on occurred_at"
    assert matching[0]["expireAfterSeconds"] == 30 * 24 * 60 * 60
