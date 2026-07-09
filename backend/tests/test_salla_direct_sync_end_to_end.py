"""E2E — Salla Direct sync writes orders into BOTH `unified_orders`
and `integration_inbox`, under the tenant namespace Plan B reads
from.

User report (2026-02):
    "Salla Direct Sync لا يثبت أنه يجلب أو يحدث طلبات سلة فعليًا…
     الطلبات التي كانت تظهر في Mezan غالبًا كانت تصل من Make."

Root cause discovered by this diagnostic pass:
    `webhook.py` writes rows to `integration_inbox` with
    `user_id="main"` (single-tenant MVP convention). Plan B reads
    from the same `"main"` namespace. BUT the previous Salla Direct
    bridge wrote rows under the LOGGED-IN user's UUID → invisible to
    Plan B. Fix: pin Salla-Direct inbox writes to `INBOX_TENANT="main"`
    to match. `unified_orders` still uses the real per-user id.

These tests exercise the full pull → parse → upsert → inbox path
with a mocked Salla API. They also validate the diagnostic counters
now emitted in the sync response (`inbox_created`, `inbox_updated`,
`inbox_failed`, `sample_order_numbers`) — the operator sees these in
the /sync/logs page and can prove Salla Direct is really the source.
"""
from __future__ import annotations

import os
os.environ.setdefault("QOYOD_WEBHOOK_TOKEN", "test-token")

from unittest.mock import AsyncMock, patch

import mongomock_motor  # noqa: F401
import pytest
import pytest_asyncio

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod_manual.pending import list_pending_orders
from salla_integration import sync as sync_mod


# In production, users["id"] is a UUID, distinct from the "main"
# tenant hardcoded in the webhook + qoyod_manual routes.
REAL_USER_ID = "user-uuid-12345"
INBOX_TENANT = "main"


# ── Fixtures ────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = mongomock_motor.AsyncMongoMockClient()
    _db = client["test_salla_direct_e2e"]
    await ensure_qoyod_indexes(_db)
    return _db


# ── Salla /orders payload builders ──────────────────────────────────
def _salla_order(order_id: str, *,
                 status_slug: str = "completed",
                 status_name: str = "تم التنفيذ",
                 total: str = "250.00",
                 tax: str = "32.61",
                 subtotal: str = "217.39",
                 date_str: str = "2026-07-10 10:00:00") -> dict:
    return {
        "id": f"oid-{order_id}",
        "reference_id": order_id,
        "date": {"date": date_str, "timezone": "Asia/Riyadh"},
        "status": {
            "slug": status_slug, "name": status_name,
            "customized": {"slug": status_slug, "name": status_name},
        },
        "customer": {"first_name": "Ali", "last_name": "K.",
                     "mobile": "+966501234567"},
        "amounts": {
            "sub_total": {"amount": subtotal, "currency": "SAR"},
            "tax":       {"amount": tax,      "currency": "SAR"},
            "shipping":  {"amount": "0.00",   "currency": "SAR"},
            "total":     {"amount": total,    "currency": "SAR"},
        },
        "items": [{
            "sku": "SKU-A", "name": "Item", "quantity": 1,
            "product": {"id": "p1", "sku": "SKU-A", "name": "Item"},
            "amounts": {
                "price_without_tax": {"amount": subtotal, "currency": "SAR"},
                "total": {"amount": subtotal, "currency": "SAR"},
                "tax": {"percent": 15,
                        "amount": {"amount": tax, "currency": "SAR"}},
            },
        }],
        "payment_method": "mada",
    }


def _mock_call_salla(pages: list[list[dict]]):
    """Build an AsyncMock for `call_salla` that returns `pages` in
    order. `pages` is a list of page bodies (each page is a list of
    Salla raw orders). Empty last page ends pagination."""
    call_log: list[dict] = []

    async def _side(_db, _uid, method, path, *, params=None, **kw):
        call_log.append({"method": method, "path": path,
                         "params": dict(params) if params else {}})
        idx = params.get("page", 1) - 1
        data = pages[idx] if idx < len(pages) else []
        return {"data": data,
                "pagination": {"totalPages": len(pages)}}

    return AsyncMock(side_effect=_side), call_log


# ── Tests ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sync_creates_orders_in_unified_and_inbox_under_main_tenant(db):
    """Sanity check: after run_orders_sync, both collections have the
    row, and inbox rows are under `user_id="main"` (visible to Plan B)."""
    order = _salla_order("300000001")
    mock, _ = _mock_call_salla([[order], []])

    with patch.object(sync_mod, "call_salla", new=mock):
        result = await sync_mod.run_orders_sync(
            db, REAL_USER_ID,
            from_date="2026-07-01", to_date="2026-07-31")

    # unified_orders under REAL user id
    uo = await db.unified_orders.find_one(
        {"user_id": REAL_USER_ID, "order_number": "300000001"})
    assert uo is not None, "unified_orders row not created"
    assert uo["source"] == "salla_direct"

    # integration_inbox under "main" tenant (the namespace Plan B reads)
    inbox_main = await db.integration_inbox.find_one(
        {"user_id": INBOX_TENANT,
         "connector_key": "salla_direct",
         "salla_order_number": "300000001"})
    assert inbox_main is not None, (
        "integration_inbox row missing under `user_id='main'` — Plan B "
        "would not see it. This is the bug the fix addresses.")
    assert inbox_main["pipeline_stage"] == "NORMALIZED"
    assert inbox_main["canonical_payload"] is not None

    # Nothing accidentally under the real user id (namespace hygiene).
    inbox_wrong = await db.integration_inbox.count_documents(
        {"user_id": REAL_USER_ID, "connector_key": "salla_direct"})
    assert inbox_wrong == 0, (
        "Salla Direct wrote inbox rows under the per-user id — Plan B "
        "reads from `main` and would miss them.")

    # Diagnostic counters — the operator sees these in sync log.
    assert result["created"] == 1
    assert result["inbox_created"] == 1
    assert result["inbox_updated"] == 0
    assert result["inbox_failed"] == 0
    assert result["sample_order_numbers"] == ["300000001"]


@pytest.mark.asyncio
async def test_sync_second_run_updates_existing_inbox_row(db):
    """Re-syncing the SAME order must UPDATE the existing inbox row,
    not create a second one. `inbox_updated` counter tracks that."""
    order = _salla_order("300000002")
    mock, _ = _mock_call_salla([[order], []])
    with patch.object(sync_mod, "call_salla", new=mock):
        r1 = await sync_mod.run_orders_sync(db, REAL_USER_ID)

    # Re-run with the same order.
    mock2, _ = _mock_call_salla([[order], []])
    with patch.object(sync_mod, "call_salla", new=mock2):
        r2 = await sync_mod.run_orders_sync(db, REAL_USER_ID)

    assert r1["inbox_created"] == 1
    assert r1["inbox_updated"] == 0
    assert r2["inbox_created"] == 0
    assert r2["inbox_updated"] == 1

    count = await db.integration_inbox.count_documents(
        {"user_id": INBOX_TENANT, "salla_order_number": "300000002"})
    assert count == 1, "Duplicate inbox row on re-sync"


@pytest.mark.asyncio
async def test_sync_makes_order_visible_in_plan_b_pending(db):
    """The whole point of the bridge: after a Salla Direct sync,
    Plan B Pending shows the order — without any Make webhook."""
    order = _salla_order("300000003", status_slug="completed",
                         status_name="تم التنفيذ")
    mock, _ = _mock_call_salla([[order], []])
    with patch.object(sync_mod, "call_salla", new=mock):
        await sync_mod.run_orders_sync(db, REAL_USER_ID)

    pending = await list_pending_orders(
        db, user_id=INBOX_TENANT, days=365, status="completed")
    assert pending["ok"] is True
    numbers = [o["order_number"] for o in pending["orders"]]
    assert "300000003" in numbers, (
        f"Order NOT in Plan B Pending. Pending: {numbers}, "
        f"scanned: {pending['counts']['scanned_inbox_rows']}")


@pytest.mark.asyncio
async def test_sync_records_diagnostic_counters_in_sync_log(db):
    """Operator visibility: the sync log persisted to `salla_sync_logs`
    carries the same counters returned by `run_orders_sync` — so the
    UI's /sync/logs page can prove Salla Direct is doing its job."""
    orders = [_salla_order(str(300000010 + i)) for i in range(3)]
    mock, _ = _mock_call_salla([orders, []])
    with patch.object(sync_mod, "call_salla", new=mock):
        result = await sync_mod.run_orders_sync(db, REAL_USER_ID)

    log = await db.salla_sync_logs.find_one({"id": result["log_id"]})
    assert log["status"] == "completed"
    assert log["created"] == 3
    assert log["inbox_created"] == 3
    assert log["inbox_updated"] == 0
    assert log["inbox_failed"]  == 0
    assert log["sample_order_numbers"] == ["300000010",
                                            "300000011",
                                            "300000012"]


@pytest.mark.asyncio
async def test_sync_status_change_updates_inbox_canonical_status(db):
    """When Salla returns an order with a NEW status, the same inbox
    row is refreshed with the new canonical_payload — proving Salla
    Direct is a real update source, not just insertion."""
    order_num = "300000020"
    order_v1 = _salla_order(order_num, status_slug="under_review",
                            status_name="بانتظار المراجعة")
    mock, _ = _mock_call_salla([[order_v1], []])
    with patch.object(sync_mod, "call_salla", new=mock):
        await sync_mod.run_orders_sync(db, REAL_USER_ID)

    row1 = await db.integration_inbox.find_one(
        {"user_id": INBOX_TENANT, "salla_order_number": order_num})
    # normalizer canonicalises Salla's `under_review` → `in_review`
    assert row1["canonical_payload"]["order_status"] == "in_review"

    order_v2 = _salla_order(order_num, status_slug="completed",
                            status_name="تم التنفيذ")
    mock2, _ = _mock_call_salla([[order_v2], []])
    with patch.object(sync_mod, "call_salla", new=mock2):
        await sync_mod.run_orders_sync(db, REAL_USER_ID)

    row2 = await db.integration_inbox.find_one(
        {"user_id": INBOX_TENANT, "salla_order_number": order_num})
    assert row2["canonical_payload"]["order_status"] == "completed"
    # Same row (single _id), not a new one.
    assert row1["id"] == row2["id"]


@pytest.mark.asyncio
async def test_sync_paginates_and_stops_when_empty(db):
    """Sanity check on pagination — the params must not include
    `expanded`, and the loop must stop when Salla returns an empty
    page (regression coverage against a runaway pull)."""
    p1 = [_salla_order(str(300000030 + i)) for i in range(2)]
    mock, call_log = _mock_call_salla([p1, []])
    with patch.object(sync_mod, "call_salla", new=mock):
        await sync_mod.run_orders_sync(
            db, REAL_USER_ID, from_date="2026-07-01")

    # sync loop breaks when the returned page is not full (2 < 50)
    # → only one call is issued. Params on that call must NOT contain
    # `expanded` and MUST carry the from_date.
    assert len(call_log) == 1
    for c in call_log:
        assert "expanded" not in c["params"]
        assert c["params"].get("from_date") == "2026-07-01"
