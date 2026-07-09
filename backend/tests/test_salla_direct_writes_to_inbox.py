"""Salla-Direct → `integration_inbox` upsert (Plan B bridge).

User directive (2026-02-XX):
    The Salla API pull (`run_orders_sync`) must ALSO write each order
    into `integration_inbox` so Plan B Pending UI can source orders
    from the Salla Direct pipeline — no dependency on Make.com webhooks.

Acceptance criteria (per user, 2026-02):
    T1. First sync of an order creates exactly ONE inbox row with
        `connector_key="salla_direct"`, `pipeline_stage="NORMALIZED"`,
        canonical_payload populated, `manual_qoyod_invoice_id`/
        `qoyod_invoice_id` unset.
    T2. Re-running sync for the SAME order does NOT create a second
        inbox row (idempotency).
    T3. When the order's Salla status changes and sync is re-run,
        the SAME row is UPDATED (status refresh). No second row.
    T4. Cross-source dedup: an existing Make.com row with the same
        `salla_order_number` + a new Salla Direct row surfaces as a
        SINGLE entry in Plan B Pending (dedup via $group).
    T5. Existing invoice markers (`manual_qoyod_invoice_id` /
        `qoyod_invoice_id`) on a prior Plan B send are PRESERVED
        across a later Salla Direct sync.
    T6. NO Qoyod API calls happen during the sync path — the helper
        only touches `db.integration_inbox`.
    T7. Order without `reference_id`/`id` is skipped (defensive).
"""
from __future__ import annotations

from datetime import datetime, timezone

import mongomock_motor  # noqa: F401
import pytest
import pytest_asyncio

from salla_integration.sync import (
    upsert_salla_direct_to_inbox,
    SALLA_DIRECT_CONNECTOR_KEY,
    _salla_direct_idempotency_key,
)
from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod_manual.pending import list_pending_orders


TENANT = "main"


@pytest_asyncio.fixture
async def db():
    client = mongomock_motor.AsyncMongoMockClient()
    _db = client["test_salla_direct_inbox"]
    await ensure_qoyod_indexes(_db)
    return _db


# ── Payload builder ─────────────────────────────────────────────────
def _raw_salla_order(
    *,
    order_number: str = "999001",
    order_id: str | None = None,
    status_slug: str = "completed",
    status_name: str = "تم التنفيذ",
    total: float = 250.0,
    tax: float = 32.61,
    subtotal: float = 217.39,
    created_at: str = "2026-07-05T10:15:00+03:00",
) -> dict:
    """Build a Salla /orders raw payload row (matches production shape)."""
    oid = order_id or f"oid-{order_number}"
    return {
        "id": oid,
        "reference_id": order_number,
        "date": {"date": created_at, "timezone": "Asia/Riyadh"},
        "status": {
            "slug": status_slug,
            "name": status_name,
            "customized": {"name": status_name, "slug": status_slug},
        },
        "customer": {
            "first_name": "أحمد", "last_name": "الحسن",
            "full_name": "أحمد الحسن",
            "mobile": "+966501234567",
        },
        "amounts": {
            "sub_total": {"amount": subtotal, "currency": "SAR"},
            "tax":       {"amount": tax,      "currency": "SAR"},
            "shipping":  {"amount": 0.0,      "currency": "SAR"},
            "total":     {"amount": total,    "currency": "SAR"},
        },
        "items": [
            {
                "sku": "SKU-A",
                "name": "منتج تجريبي أ",
                "quantity": 2,
                "amounts": {
                    "price_without_tax": {"amount": 100.0, "currency": "SAR"},
                    "total":             {"amount": 217.39, "currency": "SAR"},
                    "tax": {"percent": 15, "amount": {"amount": tax, "currency": "SAR"}},
                },
                "product": {"id": "prod-A", "sku": "SKU-A", "name": "منتج تجريبي أ"},
            },
        ],
        "payment_method": "mada",
    }


# ─────────────────────────────────────────────────────────────────────
# T1 — First sync creates an inbox row at NORMALIZED
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_first_sync_creates_inbox_row_at_normalized(db):
    raw = _raw_salla_order(order_number="999001")

    res = await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw)

    assert res["ok"] is True
    assert res["created"] is True
    assert res["row_id"]

    rows = await db.integration_inbox.find(
        {"user_id": TENANT, "salla_order_number": "999001"}).to_list(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["connector_key"] == SALLA_DIRECT_CONNECTOR_KEY
    assert row["source"] == "salla_direct"
    assert row["pipeline_stage"] == "NORMALIZED"
    assert row["idempotency_key"] == _salla_direct_idempotency_key("999001")
    assert row["canonical_payload"] is not None
    assert row["canonical_payload"]["order_number"] == "999001"
    assert row["canonical_payload"]["order_status"] == "completed"
    assert row["salla_order_id"] == "oid-999001"
    # No invoice markers on a fresh sync.
    assert row.get("manual_qoyod_invoice_id") in (None,)
    assert row.get("qoyod_invoice_id") in (None,)
    # Stage history includes both the seed entry and the NORMALIZED step.
    assert len(row["stage_history"]) >= 2


# ─────────────────────────────────────────────────────────────────────
# T2 — Re-syncing the same order does NOT create a second row
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_second_sync_is_idempotent(db):
    raw = _raw_salla_order(order_number="999002")
    r1 = await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw)
    r2 = await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw)

    assert r1["created"] is True
    assert r2["created"] is False
    assert r2["row_id"] == r1["row_id"]

    count = await db.integration_inbox.count_documents(
        {"user_id": TENANT, "salla_order_number": "999002"})
    assert count == 1


# ─────────────────────────────────────────────────────────────────────
# T3 — Status change updates the SAME row
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_status_change_updates_same_row(db):
    raw_a = _raw_salla_order(
        order_number="999003",
        status_slug="completed", status_name="تم التنفيذ")
    await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw_a)

    raw_b = _raw_salla_order(
        order_number="999003",
        status_slug="delivered", status_name="تم التوصيل")
    r2 = await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw_b)

    assert r2["created"] is False

    rows = await db.integration_inbox.find(
        {"user_id": TENANT, "salla_order_number": "999003"}).to_list(10)
    assert len(rows) == 1
    row = rows[0]
    # Canonical reflects the NEW status (delivered).
    assert row["canonical_payload"]["order_status"] == "delivered"
    assert row["canonical_payload"]["order_status_native"] == "تم التوصيل"
    # stage_history grew with a refresh entry.
    refresh_entries = [
        h for h in row["stage_history"]
        if h.get("actor") == "salla_direct_sync"
        and (h.get("note") or "").startswith("Salla Direct sync refreshed")
    ]
    assert len(refresh_entries) >= 1


# ─────────────────────────────────────────────────────────────────────
# T4 — Cross-source dedup with Make: one Pending entry, not two
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cross_source_dedup_with_make(db):
    order_number = "999004"

    # (a) Salla Direct sync writes its row.
    raw = _raw_salla_order(order_number=order_number,
                           created_at="2026-07-10T09:00:00+03:00")
    await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw)

    # (b) Make.com webhook wrote a DIFFERENT row for the same order
    #     (different connector_key namespace + different idempotency
    #     key — the current production shape).
    await db.integration_inbox.insert_one({
        "id": "row-make-x",
        "user_id": TENANT,
        "connector_key": "make_com_qoyod",
        "source": "webhook",
        "salla_order_number": order_number,
        "salla_order_id": "oid-999004",
        "idempotency_key":
            f"salla:order:oid-999004:order.completed:{'completed'}",
        "pipeline_stage": "NORMALIZED",
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": "oid-999004",
            "order_number": order_number,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "order_date": "2026-07-10T09:00:00+00:00",
            "total_amount": 250.0,
            "currency": "SAR",
            "customer": {"name": "Make Copy", "phone": "+966501112222"},
            "items": [{"sku": "SKU-A", "name": "A", "quantity": 1,
                       "unit_price": 250, "tax_amount": 0,
                       "discount_amount": 0, "total": 250}],
        },
        "stage_history": [],
    })

    pending = await list_pending_orders(
        db, user_id=TENANT, days=365, limit=100, status="completed")
    assert pending["ok"] is True
    matches = [
        o for o in pending["orders"]
        if o["order_number"] == order_number
    ]
    # Exactly ONE Pending entry despite two inbox rows.
    assert len(matches) == 1


# ─────────────────────────────────────────────────────────────────────
# T5 — Existing invoice markers on inbox row are preserved
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_invoice_markers_preserved_across_resync(db):
    raw = _raw_salla_order(order_number="999005")
    await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw)

    # Plan-B send.py stamps invoice markers + advances stage
    # (COMPLETED). Simulate that.
    await db.integration_inbox.update_one(
        {"user_id": TENANT, "salla_order_number": "999005"},
        {"$set": {
            "manual_qoyod_invoice_id": "789456",
            "qoyod_invoice_id":         "789456",
            "pipeline_stage":           "COMPLETED",
        }},
    )

    # Now Salla Direct pulls the order again (e.g. status changed).
    raw2 = _raw_salla_order(order_number="999005",
                            status_slug="delivered",
                            status_name="تم التوصيل")
    await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw2)

    row = await db.integration_inbox.find_one(
        {"user_id": TENANT, "salla_order_number": "999005"})
    # Markers survived.
    assert row["manual_qoyod_invoice_id"] == "789456"
    assert row["qoyod_invoice_id"] == "789456"
    # Stage was NOT regressed.
    assert row["pipeline_stage"] == "COMPLETED"
    # But the canonical was still refreshed.
    assert row["canonical_payload"]["order_status"] == "delivered"


# ─────────────────────────────────────────────────────────────────────
# T6 — No Qoyod HTTP calls happen during the upsert
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_qoyod_http_calls_during_upsert(db, monkeypatch):
    calls: list[str] = []

    def _fail(*a, **kw):
        calls.append(str(a[:1]))
        raise AssertionError("Qoyod HTTP call MUST NOT happen from sync path")

    # Patch every possible outbound helper the manual-send module uses.
    import integrations.qoyod_manual.send as send_mod
    for name in dir(send_mod):
        if name.startswith("_"):
            continue
        obj = getattr(send_mod, name, None)
        if callable(obj) and getattr(obj, "__module__", "").startswith(
                "integrations.qoyod"):
            # Only patch names that look network-y (call_qoyod, post_,
            # create_invoice, create_payment, …). Be conservative.
            if any(t in name.lower() for t in
                   ("call_qoyod", "post_", "create_invoice",
                    "create_payment")):
                monkeypatch.setattr(send_mod, name, _fail, raising=False)

    # Also stub the low-level HTTP client so ANY leaked call raises.
    try:
        import integrations.qoyod.client as qc
        for name in ("get_json", "post_json", "put_json",
                     "delete_json", "call_qoyod"):
            if hasattr(qc, name):
                monkeypatch.setattr(qc, name, _fail, raising=False)
    except Exception:  # module may not exist in this snapshot
        pass

    raw = _raw_salla_order(order_number="999006")
    res = await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw)

    assert res["ok"] is True
    assert calls == []


# ─────────────────────────────────────────────────────────────────────
# T7 — Payload without reference_id / id is skipped
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_missing_order_number_is_skipped(db):
    raw = {"amounts": {"total": {"amount": 100, "currency": "SAR"}}}
    res = await upsert_salla_direct_to_inbox(
        db, user_id=TENANT, raw_salla_order=raw)

    assert res["ok"] is False
    assert res["reason"] == "missing_order_number"
    count = await db.integration_inbox.count_documents({"user_id": TENANT})
    assert count == 0
