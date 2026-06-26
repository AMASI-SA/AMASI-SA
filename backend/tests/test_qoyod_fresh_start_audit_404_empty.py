"""Regression: Qoyod returns 404 'We found nothing' on LIST endpoints
when the underlying collection is empty (e.g. after Fresh-Start
cleanup). The audit must treat this as a successful empty result,
NOT as a failure.

Bug report (2026-06-27, user):
  After cleanup, /invoices returned 404 + "found nothing" and the UI
  rendered a red error banner instead of "حساب قيود نظيف".
"""
from __future__ import annotations

import asyncio

import pytest

from integrations.qoyod.api_client import QoyodAPIError
from integrations.qoyod.fresh_start_audit import _paginate


# ─── Mock fetchers ─────────────────────────────────────────────────
def _raise_404(*_a, **_kw):
    async def _inner(*_args, **_kwargs):
        raise QoyodAPIError(
            404, "qoyod_not_found", "We found nothing",
            endpoint="GET /invoices")
    return _inner


def _raise_500(*_a, **_kw):
    async def _inner(*_args, **_kwargs):
        raise QoyodAPIError(
            500, "qoyod_server_error", "boom",
            endpoint="GET /invoices")
    return _inner


def _two_pages():
    """Page 1: 50 items, page 2: 404 (Qoyod way of saying end-of-list)."""
    async def _inner(*, page, limit):
        if page == 1:
            return {"invoices": [{"id": str(i)} for i in range(50)]}
        raise QoyodAPIError(
            404, "qoyod_not_found", "no more pages",
            endpoint=f"GET /invoices?page={page}")
    return _inner


# ─── Tests ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_paginate_treats_404_on_first_page_as_empty_collection():
    """The exact bug reported by the user: empty Qoyod account
    returns 404 on /invoices; the audit must NOT raise."""
    result = await _paginate(
        _raise_404(), page_size=50, max_pages=10,
        extract_keys=("invoices",))
    assert result == []


@pytest.mark.asyncio
async def test_paginate_treats_404_on_subsequent_page_as_end_of_pagination():
    """Some Qoyod tenants return 404 instead of an empty list when the
    cursor walks past the last page. We must still return what we have."""
    result = await _paginate(
        _two_pages(), page_size=50, max_pages=10,
        extract_keys=("invoices",))
    assert len(result) == 50
    assert result[0]["id"] == "0"
    assert result[-1]["id"] == "49"


@pytest.mark.asyncio
async def test_paginate_still_raises_on_non_404_errors():
    """500, 401, 403 must still bubble up — only 404 is graceful."""
    with pytest.raises(QoyodAPIError) as exc:
        await _paginate(
            _raise_500(), page_size=50, max_pages=10,
            extract_keys=("invoices",))
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_audit_returns_completed_status_when_all_endpoints_empty():
    """End-to-end: every LIST returns 404; audit completes with totals=0
    and zero flags (clean-account scenario)."""
    from integrations.qoyod.fresh_start_audit import run_fresh_start_audit

    class FakeColl:
        def __init__(self): self.docs = []
        async def insert_one(self, d):
            self.docs.append(dict(d))
            return type("R", (), {"inserted_id": "x"})()
        async def update_one(self, q, u):
            for d in self.docs:
                if all(d.get(k) == v for k, v in q.items()):
                    for k, v in (u.get("$set") or {}).items():
                        d[k] = v
                    return type("R", (), {"modified_count": 1})()
            return type("R", (), {"modified_count": 0})()

    class FakeDB:
        def __init__(self):
            self.qoyod_fresh_start_audits = FakeColl()

    class FakeAPI:
        async def list_invoices(self, *, page, limit):
            raise QoyodAPIError(404, "qoyod_not_found", "empty",
                                endpoint="GET /invoices")
        async def list_receipts(self, *, page, limit):
            raise QoyodAPIError(404, "qoyod_not_found", "empty",
                                endpoint="GET /receipts")
        async def list_products(self, *, page, limit):
            raise QoyodAPIError(404, "qoyod_not_found", "empty",
                                endpoint="GET /products")
        async def list_contacts(self, *, page, limit):
            raise QoyodAPIError(404, "qoyod_not_found", "empty",
                                endpoint="GET /customers")

    result = await run_fresh_start_audit(
        FakeDB(), user_id="u1", api_client=FakeAPI(),
        page_size=50, max_pages=2)

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["summary"]["totals"] == {
        "invoices": 0, "receipts": 0, "products": 0, "customers": 0}
    assert result["summary"]["flags"] == []
    assert result["error"] is None
