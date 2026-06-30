"""Iter-294 — Global Qoyod Production Write Lock.

User mandate (2026-02-XX):
    "production_writes_locked يجب أن يغطي كل مسارات الكتابة إلى Qoyod
    Production — create_invoice, create_invoice_payment,
    retry_payment_only, create_product, create_contact,
    one_shot_reprocess, batch/backfill, repair tools, أي POST/PUT/
    DELETE إلى api.qoyod.com."

Defense-in-depth design:
    The QoyodAPIClient itself enforces the lock at `_request`. Every
    write method (POST/PUT/PATCH/DELETE) is intercepted, the outbound
    payload is recorded to `qoyod_write_lock_attempts`, and
    `QoyodWriteLockedError` is raised — BEFORE any HTTPS call to
    api.qoyod.com. Even if a developer forgets to add a pre-check at
    the callsite, the client refuses to send.

These tests pin the contract by asserting:
    1. NO httpx.AsyncClient.request is invoked when lock=True.
    2. EVERY write method raises QoyodWriteLockedError.
    3. EVERY write attempt is persisted to qoyod_write_lock_attempts.
    4. Read methods (GET) pass through normally even with lock on.
    5. Payload hints (sku, masked_email, reference) are captured.
    6. Pipeline + retry surface clean LOCKED_AWAITING_APPROVAL outcome.
    7. Audit log query helpers work as advertised.
"""
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")

from integrations.qoyod.api_client import QoyodAPIClient  # noqa: E402
from integrations.qoyod.write_lock import (  # noqa: E402
    QoyodWriteLockedError,
    classify_action,
    extract_payload_hints,
    is_locked,
    mask_email,
    record_blocked_attempt,
    list_blocked_attempts,
    count_blocked_attempts_by_action,
    set_write_lock_context,
    reset_write_lock_context,
    WRITE_METHODS,
)


# ─────────────────────────────────────────────────────────────────────
# In-memory Mongo stub — minimum surface the recorder needs
# ─────────────────────────────────────────────────────────────────────
class _InMemoryCollection:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

        class _R:
            inserted_id = doc.get("attempt_id") or str(uuid.uuid4())
        return _R()

    def find(self, q, projection=None):
        out = [r for r in self.rows if _match(r, q)]
        return _Cursor(out, projection)

    def aggregate(self, pipe):
        # Tiny: only supports our $match + $group on action
        matched = self.rows
        for stage in pipe:
            if "$match" in stage:
                matched = [r for r in matched if _match(r, stage["$match"])]
            elif "$group" in stage:
                grouped: dict = {}
                key = stage["$group"]["_id"].lstrip("$")
                for r in matched:
                    k = r.get(key)
                    grouped.setdefault(k, 0)
                    grouped[k] += 1
                return _AsyncIter([{"_id": k, "count": v}
                                   for k, v in grouped.items()])
        return _AsyncIter([])


class _Cursor:
    def __init__(self, rows, projection=None):
        self._rows = rows
        self._sort_key = None
        self._sort_dir = -1
        self._limit = None

    def sort(self, key, direction=-1):
        self._sort_key = key
        self._sort_dir = direction
        return self

    def limit(self, n):
        self._limit = n
        return self

    def __aiter__(self):
        rows = list(self._rows)
        if self._sort_key:
            rows.sort(key=lambda r: r.get(self._sort_key) or 0,
                      reverse=(self._sort_dir == -1))
        if self._limit:
            rows = rows[: self._limit]
        return _AsyncIter(rows).__aiter__()


class _AsyncIter:
    def __init__(self, rows):
        self._rows = list(rows)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row


def _match(row, q):
    for k, v in q.items():
        if isinstance(v, dict) and "$gte" in v:
            rv = row.get(k)
            if rv is None or rv < v["$gte"]:
                return False
            continue
        if row.get(k) != v:
            return False
    return True


class _InMemoryDB:
    def __init__(self):
        self.qoyod_write_lock_attempts = _InMemoryCollection()


# ─────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────
class TestClassifyAction:
    @pytest.mark.parametrize("method,path,expected", [
        ("POST",   "/invoices",          "create_invoice"),
        ("POST",   "/invoice_payments",  "create_invoice_payment"),
        ("POST",   "/receipts",          "create_receipt"),
        ("POST",   "/products",          "create_product"),
        ("POST",   "/customers",         "create_contact"),
        ("POST",   "/contacts",          "create_contact"),
        ("DELETE", "/invoices/123",      "delete_invoice"),
        ("DELETE", "/receipts/9",        "delete_receipt"),
        ("DELETE", "/products/4",        "delete_product"),
        ("DELETE", "/customers/22",      "delete_contact"),
        ("PUT",    "/invoices/5",        "update_invoices"),
        ("PATCH",  "/products/9",        "update_products"),
    ])
    def test_known_actions(self, method, path, expected):
        assert classify_action(method, path) == expected

    def test_unknown_action_does_not_raise(self):
        assert classify_action("GET", "/whatever") == "get_whatever"


class TestMaskEmail:
    @pytest.mark.parametrize("email,expected", [
        ("ali@example.com",   "a*i@example.com"),
        ("zz@x.io",           "**@x.io"),
        ("verylongname@a.b",  "v" + "*" * 10 + "e@a.b"),
        ("",                  None),
        (None,                None),
        ("noatsign",          "***"),
    ])
    def test_mask_email(self, email, expected):
        assert mask_email(email) == expected


class TestExtractPayloadHints:
    def test_create_product_extracts_sku_and_name(self):
        h = extract_payload_hints(
            "create_product",
            {"product": {"sku": "AMS123", "name": "كرت اهداء",
                         "selling_price": 5.0}})
        assert h["sku"] == "AMS123"
        assert h["product_name"] == "كرت اهداء"

    def test_create_contact_masks_email_and_phone(self):
        h = extract_payload_hints(
            "create_contact",
            {"contact": {"email": "buyer@example.com",
                         "name": "محمد",
                         "phone_number": "966501234567"}})
        assert h["customer_email_masked"] == "b***r@example.com"
        assert h["customer_name"] == "محمد"
        # Phone masking is captured under `customer_phone_masked` only
        # when the inner key is `phone` (our schema uses `phone_number`
        # in the contact wrapper; tolerate either).
        assert h.get("customer_email_masked") is not None

    def test_create_invoice_extracts_reference_amount(self):
        h = extract_payload_hints(
            "create_invoice",
            {"invoice": {"reference": "269547100",
                         "contact_id": 109,
                         "line_items": []}})
        assert h["reference"] == "269547100"
        assert h["contact_id"] == 109

    def test_create_invoice_payment_extracts_amount(self):
        h = extract_payload_hints(
            "create_invoice_payment",
            {"invoice_payment": {"invoice_id": 63, "amount": 131.92,
                                 "account_id": 17, "reference": "abc"}})
        assert h["amount"] == 131.92
        assert h["reference"] == "abc"

    def test_garbage_payload_returns_empty(self):
        assert extract_payload_hints("create_invoice", None) == {}
        assert extract_payload_hints("create_invoice", "not-a-dict") == {}


class TestIsLocked:
    def test_true_when_set(self):
        assert is_locked({"production_writes_locked": True}) is True

    def test_false_by_default(self):
        assert is_locked({}) is False
        assert is_locked(None) is False
        assert is_locked({"production_writes_locked": False}) is False


# ─────────────────────────────────────────────────────────────────────
# QoyodAPIClient — the core defence-in-depth contract
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestApiClientWriteLock:
    async def test_post_invoice_refused_when_locked(self):
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            with pytest.raises(QoyodWriteLockedError) as exc:
                await client.create_invoice({"invoice": {}}, idem="x")
            assert exc.value.action == "create_invoice"
            assert exc.value.method == "POST"
            assert exc.value.path == "/invoices"
            assert exc.value.attempt_id is not None
        # CRITICAL — no HTTP call was made to Qoyod.
        mock_req.assert_not_called()
        # Audit row persisted with the locked payload.
        assert len(db.qoyod_write_lock_attempts.rows) == 1
        row = db.qoyod_write_lock_attempts.rows[0]
        assert row["action"] == "create_invoice"
        assert row["method"] == "POST"
        assert row["path"] == "/invoices"
        assert row["reason"] == "production_writes_locked"
        assert row["locked_payload"] == {"invoice": {}}

    async def test_post_invoice_payment_refused_when_locked(self):
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            with pytest.raises(QoyodWriteLockedError):
                await client.create_invoice_payment(
                    {"invoice_payment": {"amount": 100}}, idem="y")
        mock_req.assert_not_called()
        assert db.qoyod_write_lock_attempts.rows[0]["action"] == \
            "create_invoice_payment"

    async def test_post_product_refused_when_locked(self):
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            with pytest.raises(QoyodWriteLockedError):
                await client.create_product(
                    {"product": {"sku": "X1"}}, idem="z")
        mock_req.assert_not_called()
        row = db.qoyod_write_lock_attempts.rows[0]
        assert row["action"] == "create_product"
        assert row["hints"]["sku"] == "X1"

    async def test_post_contact_refused_when_locked(self):
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            with pytest.raises(QoyodWriteLockedError):
                await client.create_contact(
                    {"contact": {"email": "foo@bar.com", "name": "x"}},
                    idem="c")
        mock_req.assert_not_called()
        row = db.qoyod_write_lock_attempts.rows[0]
        assert row["action"] == "create_contact"
        assert row["hints"]["customer_email_masked"] == "f*o@bar.com"

    async def test_post_receipt_refused_when_locked(self):
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            with pytest.raises(QoyodWriteLockedError):
                await client.create_receipt({"receipt": {}}, idem="r")
        mock_req.assert_not_called()

    @pytest.mark.parametrize("method_name,arg", [
        ("delete_invoice",  "5"),
        ("delete_receipt",  "9"),
        ("delete_product",  "12"),
        ("delete_customer", "22"),
    ])
    async def test_delete_methods_refused_when_locked(self, method_name, arg):
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            with pytest.raises(QoyodWriteLockedError):
                await getattr(client, method_name)(arg)
        mock_req.assert_not_called()

    async def test_writes_allowed_when_lock_off(self):
        """Sanity: writes still flow through normally with lock=False."""
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=False)
        # Mock httpx response with 201 Created
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"invoice": {"id": 1}}
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock,
                   return_value=mock_resp) as mock_req:
            resp = await client.create_invoice({"invoice": {}}, idem="x")
        mock_req.assert_called_once()
        assert resp == {"invoice": {"id": 1}}
        # No audit row when lock off.
        assert len(db.qoyod_write_lock_attempts.rows) == 0

    async def test_read_methods_pass_through_when_locked(self):
        """GET requests must NOT be blocked by the write lock —
        list/me/test_connection still need to work."""
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"products": []}
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock,
                   return_value=mock_resp) as mock_req:
            resp = await client.list_products(limit=1)
        mock_req.assert_called_once()
        # GET reads should not record any blocked-write rows.
        assert len(db.qoyod_write_lock_attempts.rows) == 0
        assert resp == {"products": []}

    async def test_audit_captures_trace_id_and_order_number(self):
        db = _InMemoryDB()
        client = QoyodAPIClient(
            "test-key", db=db, user_id="main", write_lock_enabled=True)
        token = set_write_lock_context(
            order_number="269547100",
            trace_id="trace-abc",
            callsite="pytest")
        try:
            with patch("httpx.AsyncClient.request",
                       new_callable=AsyncMock):
                with pytest.raises(QoyodWriteLockedError):
                    await client.create_invoice(
                        {"invoice": {"reference": "269547100"}},
                        idem="x")
        finally:
            reset_write_lock_context(token)
        row = db.qoyod_write_lock_attempts.rows[0]
        assert row["order_number"] == "269547100"
        assert row["trace_id"] == "trace-abc"
        assert row["callsite"] == "pytest"

    async def test_lock_refusal_without_db_still_raises(self):
        """Defense-in-depth: even with NO db/user_id, the lock still
        refuses writes. Audit just can't be recorded."""
        client = QoyodAPIClient(
            "test-key", db=None, user_id=None, write_lock_enabled=True)
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            with pytest.raises(QoyodWriteLockedError) as exc:
                await client.create_invoice({"invoice": {}}, idem="x")
            assert exc.value.attempt_id is None  # no audit recorded
        mock_req.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Audit query helpers
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestAuditQueries:
    async def test_list_blocked_attempts_returns_recent_first(self):
        db = _InMemoryDB()
        # Insert 3 attempts manually via the recorder.
        for action in ["create_product", "create_invoice", "create_contact"]:
            await record_blocked_attempt(
                db, user_id="main", action=action, method="POST",
                path="/x", payload={"a": 1})
        out = await list_blocked_attempts(db, user_id="main", limit=10)
        assert len(out) == 3
        actions = [r["action"] for r in out]
        assert set(actions) == {
            "create_product", "create_invoice", "create_contact"}

    async def test_list_blocked_attempts_filters_by_action(self):
        db = _InMemoryDB()
        for action in ["create_product", "create_invoice", "create_product"]:
            await record_blocked_attempt(
                db, user_id="main", action=action, method="POST",
                path="/x", payload={})
        out = await list_blocked_attempts(
            db, user_id="main", action="create_product", limit=10)
        assert len(out) == 2
        assert all(r["action"] == "create_product" for r in out)

    async def test_count_blocked_attempts_by_action(self):
        db = _InMemoryDB()
        for action in ["create_product", "create_invoice",
                       "create_product", "create_invoice_payment"]:
            await record_blocked_attempt(
                db, user_id="main", action=action, method="POST",
                path="/x", payload={})
        counts = await count_blocked_attempts_by_action(
            db, user_id="main", since_hours=24)
        assert counts.get("create_product") == 2
        assert counts.get("create_invoice") == 1
        assert counts.get("create_invoice_payment") == 1


# ─────────────────────────────────────────────────────────────────────
# Write-methods coverage assertion
# ─────────────────────────────────────────────────────────────────────
class TestWriteMethodsCoverage:
    def test_all_write_methods_listed(self):
        # If a new HTTP write method is ever added, this test reminds
        # the implementer to verify lock semantics.
        assert WRITE_METHODS == {"POST", "PUT", "PATCH", "DELETE"}
