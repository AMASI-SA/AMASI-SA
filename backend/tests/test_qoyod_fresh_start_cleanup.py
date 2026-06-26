"""Tests for Qoyod Fresh-Start Cleanup — Plan + Execute gating."""
from __future__ import annotations

import asyncio
import pytest

from integrations.qoyod.api_client import QoyodAPIError
from integrations.qoyod.fresh_start_cleanup import (
    CleanupRefused, EXPECTED_CONFIRM_TOKEN, PROTECTED_ENTITIES,
    _delete_batch, _extract_id,
    build_plan, execute_cleanup,
)


# ─── Fake DB & API client ──────────────────────────────────────────
class FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "id-1"})()

    async def find_one(self, query, projection=None, sort=None):
        # Trivial: AND across keys.
        matches = [d for d in self.docs
                   if all(d.get(k) == v for k, v in query.items())]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda d: d.get(key) or 0,
                         reverse=direction == -1)
        return matches[0] if matches else None

    async def update_one(self, query, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                for k, v in (update.get("$set") or {}).items():
                    # Support dotted keys
                    if "." in k:
                        head, _, tail = k.partition(".")
                        d.setdefault(head, {})[tail] = v
                    else:
                        d[k] = v
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


class FakeDB:
    def __init__(self):
        self.qoyod_fresh_start_cleanups = FakeCollection()


class FakeAPI:
    """Records every call. Configurable failure modes."""
    def __init__(self, *, fail_ids: set[str] | None = None,
                 not_found_ids: set[str] | None = None,
                 list_payloads: dict | None = None):
        self.deletes: list[tuple[str, str]] = []
        self.fail_ids = fail_ids or set()
        self.not_found_ids = not_found_ids or set()
        self.list_payloads = list_payloads or {}

    async def _maybe_raise(self, entity: str, item_id: str):
        if item_id in self.not_found_ids:
            raise QoyodAPIError(404, "qoyod_not_found", "gone",
                                endpoint=f"DELETE /{entity}/{item_id}")
        if item_id in self.fail_ids:
            raise QoyodAPIError(500, "qoyod_server_error", "boom",
                                endpoint=f"DELETE /{entity}/{item_id}")

    async def delete_invoice(self, i):
        self.deletes.append(("invoices", i))
        await self._maybe_raise("invoices", i)

    async def delete_receipt(self, i):
        self.deletes.append(("receipts", i))
        await self._maybe_raise("receipts", i)

    async def delete_product(self, i):
        self.deletes.append(("products", i))
        await self._maybe_raise("products", i)

    async def delete_customer(self, i):
        self.deletes.append(("customers", i))
        await self._maybe_raise("customers", i)

    # List endpoints used by build_plan
    async def list_invoices(self, *, page, limit):
        return self.list_payloads.get(("invoices", page), [])

    async def list_receipts(self, *, page, limit):
        return self.list_payloads.get(("receipts", page), [])

    async def list_products(self, *, page, limit):
        return self.list_payloads.get(("products", page), [])

    async def list_contacts(self, *, page, limit):
        return self.list_payloads.get(("customers", page), [])


# ─── Constants & protected list ────────────────────────────────────
def test_expected_token_is_exactly_DELETE_CONFIRM():
    assert EXPECTED_CONFIRM_TOKEN == "DELETE-CONFIRM"


def test_protected_entities_include_accounting_pillars():
    """Sanity: the cleanup MUST never touch these."""
    for must_have in ("chart_of_accounts", "branches", "taxes",
                      "settings", "users", "financial_accounts"):
        assert must_have in PROTECTED_ENTITIES


# ─── _extract_id ───────────────────────────────────────────────────
def test_extract_id_handles_id_underscore_and_contact():
    assert _extract_id({"id": 5}) == "5"
    assert _extract_id({"_id": "abc"}) == "abc"
    assert _extract_id({"contact_id": "C-1"}) == "C-1"
    assert _extract_id({"invoice_id": 9}) == "9"
    assert _extract_id({}) is None
    assert _extract_id("not-a-dict") is None


# ─── _delete_batch — happy path & error handling ──────────────────
@pytest.mark.asyncio
async def test_delete_batch_happy_path():
    api = FakeAPI()
    deleted, fails = await _delete_batch(
        api.delete_invoice, ["1", "2", "3"], "invoices", pause_ms=0)
    assert deleted == 3
    assert fails == []
    assert api.deletes == [("invoices", "1"), ("invoices", "2"), ("invoices", "3")]


@pytest.mark.asyncio
async def test_delete_batch_treats_404_as_success():
    api = FakeAPI(not_found_ids={"2"})
    deleted, fails = await _delete_batch(
        api.delete_invoice, ["1", "2", "3"], "invoices", pause_ms=0)
    assert deleted == 3
    assert fails == []


@pytest.mark.asyncio
async def test_delete_batch_continues_past_failures():
    api = FakeAPI(fail_ids={"2"})
    deleted, fails = await _delete_batch(
        api.delete_invoice, ["1", "2", "3"], "invoices", pause_ms=0)
    assert deleted == 2
    assert len(fails) == 1
    assert fails[0]["entity"] == "invoices"
    assert fails[0]["id"] == "2"
    assert fails[0]["error"]["status_code"] == 500


@pytest.mark.asyncio
async def test_delete_batch_aborts_on_405_method_not_allowed():
    class Stub(FakeAPI):
        async def delete_invoice(self, i):
            self.deletes.append(("invoices", i))
            raise QoyodAPIError(405, "qoyod_http_error",
                                "Method not allowed",
                                endpoint=f"DELETE /invoices/{i}")
    api = Stub()
    deleted, fails = await _delete_batch(
        api.delete_invoice, ["1", "2", "3"], "invoices", pause_ms=0)
    # First call attempted, then batch aborted.
    assert deleted == 0
    assert len(fails) == 2  # original 405 + batch_aborted marker
    assert any(f["id"] == "__batch_aborted__" for f in fails)
    # Should not have attempted the rest.
    assert len(api.deletes) == 1


# ─── execute_cleanup — gating ──────────────────────────────────────
@pytest.mark.asyncio
async def test_execute_refuses_wrong_confirm_token():
    db = FakeDB()
    api = FakeAPI()
    with pytest.raises(CleanupRefused):
        await execute_cleanup(db, user_id="u1", job_id="j1",
                              confirm_token="WRONG", api_client=api)
    # No DELETE calls made.
    assert api.deletes == []


@pytest.mark.asyncio
async def test_execute_refuses_token_with_whitespace():
    db = FakeDB()
    api = FakeAPI()
    with pytest.raises(CleanupRefused):
        await execute_cleanup(db, user_id="u1", job_id="j1",
                              confirm_token="DELETE-CONFIRM ", api_client=api)


@pytest.mark.asyncio
async def test_execute_refuses_token_case_mismatch():
    db = FakeDB()
    api = FakeAPI()
    with pytest.raises(CleanupRefused):
        await execute_cleanup(db, user_id="u1", job_id="j1",
                              confirm_token="delete-confirm", api_client=api)


@pytest.mark.asyncio
async def test_execute_refuses_missing_plan():
    db = FakeDB()
    api = FakeAPI()
    with pytest.raises(CleanupRefused):
        await execute_cleanup(db, user_id="u1", job_id="nonexistent",
                              confirm_token="DELETE-CONFIRM", api_client=api)


# ─── execute_cleanup — happy path ──────────────────────────────────
@pytest.mark.asyncio
async def test_execute_deletes_in_correct_order_receipts_then_invoices_then_products_then_customers():
    db = FakeDB()
    # Insert a planned job.
    from datetime import datetime, timezone
    await db.qoyod_fresh_start_cleanups.insert_one({
        "job_id":   "J1",
        "user_id":  "u1",
        "status":   "planned",
        "created_at": datetime.now(timezone.utc),
        "plan": {
            "invoice_ids":  ["i1", "i2"],
            "receipt_ids":  ["r1"],
            "product_ids":  ["p1", "p2"],
            "customer_ids": ["c1"],
            "totals": {"invoices": 2, "receipts": 1, "products": 2, "customers": 1},
        },
    })
    api = FakeAPI()
    result = await execute_cleanup(
        db, user_id="u1", job_id="J1",
        confirm_token="DELETE-CONFIRM", api_client=api, pause_ms=0)

    assert result["ok"] is True
    assert result["status"] == "executed"
    assert result["deleted"] == {
        "receipts": 1, "invoices": 2, "products": 2, "customers": 1}
    # Verify ORDER: receipts before invoices before products before customers.
    order = [entity for entity, _ in api.deletes]
    receipt_idx  = order.index("receipts")
    invoice_idx  = order.index("invoices")
    product_idx  = order.index("products")
    customer_idx = order.index("customers")
    assert receipt_idx < invoice_idx < product_idx < customer_idx


@pytest.mark.asyncio
async def test_execute_reports_partial_failures_without_aborting():
    db = FakeDB()
    from datetime import datetime, timezone
    await db.qoyod_fresh_start_cleanups.insert_one({
        "job_id":   "J2",
        "user_id":  "u1",
        "status":   "planned",
        "created_at": datetime.now(timezone.utc),
        "plan": {
            "invoice_ids":  ["i1", "i2", "i3"],
            "receipt_ids":  [],
            "product_ids":  [],
            "customer_ids": [],
            "totals": {"invoices": 3, "receipts": 0, "products": 0, "customers": 0},
        },
    })
    api = FakeAPI(fail_ids={"i2"})
    result = await execute_cleanup(
        db, user_id="u1", job_id="J2",
        confirm_token="DELETE-CONFIRM", api_client=api, pause_ms=0)

    assert result["ok"] is False
    assert result["status"] == "executed_with_errors"
    assert result["deleted"]["invoices"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == "i2"


@pytest.mark.asyncio
async def test_execute_persists_confirm_token_for_audit():
    db = FakeDB()
    from datetime import datetime, timezone
    await db.qoyod_fresh_start_cleanups.insert_one({
        "job_id":   "J3",
        "user_id":  "u1",
        "status":   "planned",
        "created_at": datetime.now(timezone.utc),
        "plan": {"invoice_ids": [], "receipt_ids": [],
                 "product_ids": [], "customer_ids": [],
                 "totals": {"invoices": 0, "receipts": 0,
                            "products": 0, "customers": 0}},
    })
    await execute_cleanup(db, user_id="u1", job_id="J3",
                          confirm_token="DELETE-CONFIRM",
                          api_client=FakeAPI(), pause_ms=0)
    persisted = await db.qoyod_fresh_start_cleanups.find_one({"job_id": "J3"})
    assert persisted["execute"]["confirm_token_used"] == "DELETE-CONFIRM"


# ─── build_plan ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_build_plan_collects_ids_from_all_four_entities():
    db = FakeDB()
    api = FakeAPI(list_payloads={
        ("invoices",  1): [{"id": "i1"}, {"id": "i2"}],
        ("receipts",  1): [{"id": "r1"}],
        ("products",  1): [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
        ("customers", 1): [{"id": "c1"}, {"contact_id": "c2"}],
    })
    plan_doc = await build_plan(
        db, user_id="u1", api_client=api, page_size=50, max_pages=2)
    assert plan_doc["plan"]["totals"] == {
        "invoices": 2, "receipts": 1, "products": 3, "customers": 2}
    assert plan_doc["plan"]["customer_ids"] == ["c1", "c2"]
    assert plan_doc["status"] == "planned"
    assert plan_doc["protected_entities"] == PROTECTED_ENTITIES
