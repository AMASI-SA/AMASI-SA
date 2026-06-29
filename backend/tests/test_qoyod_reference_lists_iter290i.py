"""Iter-290i — Reference-Lists fetcher + cache.

The settings page used to ask operators to type Qoyod numeric ids by
hand (category_id=1, account_id=17, …). This module fetches every
list from قيود and caches it under `qoyod_reference_lists` so the
picker UI can render dropdowns of NAMES.

These tests pin:
  • Normalisation (id always str, name always non-empty)
  • Strict read-only behaviour (no POST/PUT/DELETE calls)
  • Graceful handling of partial failures
  • Cache round-trip via DB upsert
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from integrations.qoyod.reference_lists import (
    _normalise_list, _safe_name, _safe_id,
    refresh_reference_lists, get_reference_lists,
)


# ─── 1. Normalisers handle every Qoyod response shape ────────────────
def test_normalise_list_handles_nested_root_key():
    """قيود usually wraps lists: `{"product_categories": [...]}`."""
    payload = {"product_categories": [
        {"id": 1, "name": "Default"},
        {"id": 2, "name_ar": "إلكترونيات"},
    ]}
    out = _normalise_list(payload, root_key="product_category")
    assert out == [
        {"id": "1", "name": "Default"},
        {"id": "2", "name": "إلكترونيات"},
    ]


def test_normalise_list_handles_singular_root_key_variant():
    """Some Qoyod resources use the singular form (`{"account": [...]}`)."""
    payload = {"account": [{"id": 17, "name": "Cash"}]}
    out = _normalise_list(payload, root_key="account")
    assert out == [{"id": "17", "name": "Cash"}]


def test_normalise_list_handles_bare_array():
    payload = [{"id": 5, "name": "Box"}, {"id": 6, "name": "Piece"}]
    out = _normalise_list(payload, root_key="product_unit")
    assert {r["id"]: r["name"] for r in out} == {"5": "Box", "6": "Piece"}


def test_normalise_list_skips_items_with_no_id():
    payload = {"taxes": [
        {"name": "Orphan"},                    # no id — dropped
        {"id": "", "name": "Empty id"},        # empty id — dropped
        {"id": 15, "name": "VAT 15%"},         # kept
    ]}
    out = _normalise_list(payload, root_key="tax")
    assert out == [{"id": "15", "name": "VAT 15%"}]


def test_normalise_list_attaches_extra_fields():
    payload = {"customers": [
        {"id": 109, "name": "Mezan Test",
         "phone": "+966500000000", "email": "x@y.com",
         "internal_secret": "should-not-leak"},
    ]}
    out = _normalise_list(payload, root_key="customer",
                          extra_fields=["phone", "email"])
    assert out == [{
        "id": "109", "name": "Mezan Test",
        "phone": "+966500000000", "email": "x@y.com",
    }]


def test_safe_name_prefers_arabic_then_falls_back():
    assert _safe_name({"name_ar": "نقدي", "name": "Cash"}) == "نقدي"
    assert _safe_name({"name": "Cash"})                    == "Cash"
    assert _safe_name({"title": "X"})                      == "X"
    # Nested under attributes — some Qoyod resources do this.
    assert _safe_name({"attributes": {"name": "From attrs"}}) == "From attrs"
    # Empty / missing — falls back to `#<id>`.
    assert _safe_name({"id": 7}) == "#7"


def test_safe_id_coerces_int_to_str():
    """The picker compares against settings that may have stringified
    values — coerce on the fetcher side so the UI doesn't have to."""
    assert _safe_id({"id": 1})    == "1"
    assert _safe_id({"id": "1"})  == "1"
    assert _safe_id({"id": None}) == ""
    assert _safe_id({})           == ""


# ─── 2. End-to-end refresh — strictly read-only ──────────────────────
class _RecordingFakeClient:
    """In-memory Qoyod API stub. Records every call so the test can
    assert ONLY GET-equivalent list methods are invoked, and never
    any write."""
    WRITE_FORBIDDEN = (
        "create_contact", "create_product", "create_invoice",
        "create_receipt", "create_invoice_payment",
        "delete_invoice", "delete_receipt", "delete_product",
        "delete_customer", "update_invoice", "patch_invoice",
    )

    def __init__(self):
        self.calls: list[str] = []

    async def list_product_categories(self):
        self.calls.append("list_product_categories")
        return {"product_categories": [{"id": 1, "name": "Default"}]}

    async def list_product_units(self):
        self.calls.append("list_product_units")
        return {"product_units": [{"id": 6, "name": "قطعة"}]}

    async def list_inventories(self):
        self.calls.append("list_inventories")
        return {"inventories": [{"id": 1, "name": "Main"}]}

    async def list_accounts(self):
        self.calls.append("list_accounts")
        return {"accounts": [
            {"id": 17, "name": "Cash on hand",   "type": "asset"},
            {"id": 92, "name": "Cash account",   "type": "asset"},
        ]}

    async def list_taxes(self):
        self.calls.append("list_taxes")
        return {"taxes": [{"id": 15, "name": "VAT 15%", "percent": "15"}]}

    async def list_branches(self):
        self.calls.append("list_branches")
        return {"branches": [{"id": 1, "name": "Main"}]}

    async def list_contacts(self, *, page, limit):
        self.calls.append(f"list_contacts(page={page},limit={limit})")
        return {"customers": [
            {"id": 109, "name": "Mezan",
             "phone": "+966500000000", "email": "ops@mezan.example"},
        ]}

    def __getattr__(self, name):
        if name in self.WRITE_FORBIDDEN:
            raise AssertionError(
                f"refresh_reference_lists attempted a write call: {name!r}")
        raise AttributeError(name)


class _FakeRefListsCol:
    def __init__(self):
        self.store: dict = {}

    async def update_one(self, query, update, upsert=False):
        self.store[query["user_id"]] = update["$set"]

    async def find_one(self, query, projection=None):
        return self.store.get(query["user_id"])


class _FakeDB:
    def __init__(self):
        self.qoyod_reference_lists = _FakeRefListsCol()


@pytest.fixture
def patched_creds(monkeypatch):
    from integrations.qoyod import reference_lists as rl

    async def _fake_get_api_key(db, user_id):
        return "test-key"

    monkeypatch.setattr(rl, "get_api_key", _fake_get_api_key)
    return rl


@pytest.mark.asyncio
async def test_refresh_calls_only_list_methods_and_persists_cache(
    patched_creds,
):
    db = _FakeDB()
    fake = _RecordingFakeClient()
    out = await refresh_reference_lists(
        db, user_id="tenant-a", client_factory=lambda _k: fake)
    assert out["ok"] is True
    assert out["fetch_errors"] in (None, {}), out
    # Exactly seven list calls — one per spec entry.
    assert sorted(fake.calls) == sorted([
        "list_product_categories", "list_product_units",
        "list_inventories", "list_accounts", "list_taxes",
        "list_branches", "list_contacts(page=1,limit=200)",
    ])
    lists = out["lists"]
    assert lists["categories"]  == [{"id": "1", "name": "Default"}]
    assert lists["unit_types"]  == [{"id": "6", "name": "قطعة"}]
    assert lists["inventories"] == [{"id": "1", "name": "Main"}]
    assert lists["taxes"] == [{"id": "15", "name": "VAT 15%", "percent": "15"}]
    assert lists["accounts"] == [
        {"id": "17", "name": "Cash on hand", "type": "asset"},
        {"id": "92", "name": "Cash account", "type": "asset"},
    ]
    assert lists["branches"]  == [{"id": "1", "name": "Main"}]
    assert lists["customers"] == [{
        "id": "109", "name": "Mezan",
        "phone": "+966500000000", "email": "ops@mezan.example"
    }]

    # Cached in DB.
    stored = db.qoyod_reference_lists.store["tenant-a"]
    assert stored["lists"]["categories"] == [{"id": "1", "name": "Default"}]
    # updated_at is a parseable ISO-8601 UTC string.
    parsed = datetime.fromisoformat(stored["updated_at"])
    assert parsed.tzinfo is not None


@pytest.mark.asyncio
async def test_refresh_partial_failure_still_returns_success_with_other_lists(
    patched_creds,
):
    """If one list endpoint throws, the others still land — the
    operator gets to see what worked and what didn't."""
    from integrations.qoyod.api_client import QoyodAPIError

    class _PartialFail(_RecordingFakeClient):
        async def list_taxes(self):
            self.calls.append("list_taxes")
            raise QoyodAPIError(
                code="server_error", message="boom",
                status_code=500, endpoint="GET /taxes")

    db = _FakeDB()
    out = await refresh_reference_lists(
        db, user_id="tenant-a", client_factory=lambda _k: _PartialFail())
    assert out["ok"] is True
    assert out["fetch_errors"] is not None
    assert "taxes" in out["fetch_errors"]
    assert out["fetch_errors"]["taxes"]["status_code"] == 500
    # Other lists still arrived.
    assert out["lists"]["categories"] == [{"id": "1", "name": "Default"}]
    assert out["lists"]["taxes"]      == []


@pytest.mark.asyncio
async def test_refresh_refuses_when_api_key_missing(monkeypatch):
    from integrations.qoyod import reference_lists as rl

    async def _no_key(db, user_id):
        return None

    monkeypatch.setattr(rl, "get_api_key", _no_key)
    out = await refresh_reference_lists(
        _FakeDB(), user_id="tenant-a", client_factory=lambda _k: object())
    assert out["ok"] is False
    assert out["code"] == "qoyod_api_key_missing"


# ─── 3. Read endpoint round-trip + empty-cache placeholder ───────────
@pytest.mark.asyncio
async def test_get_reference_lists_returns_empty_placeholder_when_cache_empty():
    db = _FakeDB()
    out = await get_reference_lists(db, user_id="tenant-a")
    assert out["ok"] is True
    assert out["cached"] is False
    assert out["updated_at"] is None
    # Every expected list key is present (empty) so the UI doesn't
    # need defensive optional chaining everywhere.
    for key in ("categories", "unit_types", "inventories", "accounts",
                "taxes", "branches", "customers"):
        assert out["lists"][key] == []


@pytest.mark.asyncio
async def test_get_reference_lists_returns_cached_doc_after_refresh(
    patched_creds,
):
    db = _FakeDB()
    fake = _RecordingFakeClient()
    await refresh_reference_lists(
        db, user_id="tenant-a", client_factory=lambda _k: fake)
    out = await get_reference_lists(db, user_id="tenant-a")
    assert out["ok"] is True
    assert out["cached"] is True
    assert out["lists"]["categories"] == [{"id": "1", "name": "Default"}]
