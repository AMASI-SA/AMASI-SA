"""Iter-251 · Phase 2A.5 — Provider Invoice Calendar tests.

Validates:
  • Extracting real Tamara invoice dates from `settlement_entries`.
  • Calendar feeds back into `_simulate_weekly` so Dry-Run reports
    the same invoice_date / period boundaries as the source data.
  • Manual upsert + delete.
  • Idempotent rebuild (re-running doesn't duplicate, but it updates
    period boundaries when new data arrives).
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from provider_invoice_calendar import (  # noqa: E402
    extract_calendar_from_settlement_entries,
    rebuild_calendar, get_calendar,
    upsert_manual_entry, delete_entry,
)


@pytest.fixture
def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


def _user(uid):
    return {"id": uid, "name": "Tester", "email": f"{uid}@t.local"}


async def _seed_tamara(db, uid, dates):
    """Insert one settlement_entry per (date, idx)."""
    docs = []
    for i, d in enumerate(dates):
        docs.append({
            "user_id": uid, "provider": "tamara",
            "settlement_reference": f"TAM-{d}-{i}",
            "settlement_date":  d,
            "actual_gross_amount": 100.0 + i,
            "actual_refund_amount": 0,
            "actual_payment_fee": 5.0,
            "actual_payment_vat": 0.75,
            "actual_net_amount": 94.25 + i,
            "event_type": "sale",
        })
    if docs:
        await db.settlement_entries.insert_many(docs)


@pytest.mark.asyncio
async def test_tamara_snaps_sunday_to_saturday(db):
    """When Tamara settlement_date is stored as Sunday (timezone
    drift from Saudi UTC+3 → UTC), we snap back to the previous
    Saturday and merge rows that collapse onto the same Saturday."""
    uid = f"u_{uuid.uuid4().hex[:6]}"
    # 2026-04-26 is Sunday; real Tamara invoice is Saturday 2026-04-25
    await _seed_tamara(db, uid, ["2026-04-26"])
    out = await extract_calendar_from_settlement_entries(
        db, uid, "tamara")
    assert len(out) == 1
    assert out[0]["invoice_date"]  == "2026-04-25"  # snapped to Sat
    assert out[0]["period_start"]  == "2026-04-25"
    assert out[0]["period_end"]    == "2026-05-01"  # Fri
    assert out[0]["snap_applied"]  is True
    # Original date preserved for audit
    assert "2026-04-26" in out[0]["settlement_dates"]


@pytest.mark.asyncio
async def test_tamara_snap_merges_split_rows(db):
    """If a Saturday invoice has some rows stamped Sat and some
    stamped Sun (mixed-timezone import), snapping merges them into
    a single Saturday invoice."""
    uid = f"u_{uuid.uuid4().hex[:6]}"
    await _seed_tamara(db, uid, ["2026-04-25", "2026-04-26"])
    out = await extract_calendar_from_settlement_entries(
        db, uid, "tamara")
    # Both rows collapse onto Sat 25/04
    assert len(out) == 1
    assert out[0]["invoice_date"] == "2026-04-25"
    assert out[0]["orders_count_hint"] == 2


@pytest.mark.asyncio
async def test_extract_real_invoice_dates_from_settlement_entries(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    # Real-world Tamara invoice dates from the user — all Saturdays
    real_dates = ["2026-05-23", "2026-05-30", "2026-06-06",
                  "2026-06-13", "2026-06-20"]
    await _seed_tamara(db, uid, real_dates)
    out = await extract_calendar_from_settlement_entries(
        db, uid, "tamara")
    invoice_dates = [r["invoice_date"] for r in out]
    assert invoice_dates == real_dates
    # Tamara layout = invoice_as_start (Saturday → Friday cycle).
    # First invoice 2026-05-23 (Sat):
    #     period_start = 2026-05-23 (Sat)
    #     period_end   = 2026-05-29 (Fri)
    assert out[0]["layout"] == "invoice_as_start"
    assert out[0]["period_start"] == "2026-05-23"
    assert out[0]["period_end"]   == "2026-05-29"
    # Second invoice 2026-05-30 (Sat):
    #     period_start = 2026-05-30
    #     period_end   = 2026-06-05
    assert out[1]["period_start"] == "2026-05-30"
    assert out[1]["period_end"]   == "2026-06-05"
    # Transfer offset for Tamara (invoice_as_start) = 9 days
    #     2026-05-23 + 9 = 2026-06-01 (Mon)
    assert out[0]["expected_transfer_date"] == "2026-06-01"


@pytest.mark.asyncio
async def test_rebuild_calendar_idempotent(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    await _seed_tamara(db, uid, ["2026-05-23", "2026-05-30"])
    r1 = await rebuild_calendar(db, uid, _user(uid), "tamara")
    assert r1["inserted"] == 2
    r2 = await rebuild_calendar(db, uid, _user(uid), "tamara")
    assert r2["inserted"] == 0
    assert r2["updated"] == 2  # boundaries refreshed in place
    cal = await get_calendar(db, uid, "tamara")
    assert len(cal) == 2


@pytest.mark.asyncio
async def test_manual_entry_protected_from_rebuild(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    # Add a forecasted future invoice manually
    await upsert_manual_entry(
        db, uid, _user(uid), "tamara",
        invoice_date="2026-07-04",
        period_start="2026-06-28", period_end="2026-07-04",
        expected_transfer_date="2026-07-06",
    )
    # Now seed a different historical invoice and rebuild
    await _seed_tamara(db, uid, ["2026-05-23"])
    r = await rebuild_calendar(db, uid, _user(uid), "tamara")
    assert r["inserted"] == 1
    assert r["skipped_manual"] == 0  # manual is for a different date
    cal = await get_calendar(db, uid, "tamara")
    assert len(cal) == 2
    # The manual one survives unchanged
    manual = [c for c in cal if c["source"] == "manual"][0]
    assert manual["invoice_date"] == "2026-07-04"


@pytest.mark.asyncio
async def test_dry_run_uses_calendar_when_present(db):
    """End-to-end: after rebuild, /dry-run-details for tamara must
    surface the EXACT real invoice dates — not ISO-week buckets.

    Tamara cycle (invoice_as_start): Sat → next Fri.
       Inv 2026-05-23: covers 2026-05-23 → 2026-05-29
       Inv 2026-05-30: covers 2026-05-30 → 2026-06-05
       Inv 2026-06-06: covers 2026-06-06 → 2026-06-12
    """
    from settlement_engine_routes import make_settlement_engine_router

    uid = f"u_{uuid.uuid4().hex[:6]}"
    await _seed_tamara(db, uid,
                       ["2026-05-23", "2026-05-30", "2026-06-06"])
    await rebuild_calendar(db, uid, _user(uid), "tamara")
    # Orders:
    #  • 2026-05-25 → falls in invoice 23/05 cycle
    #  • 2026-05-28 → falls in invoice 23/05 cycle
    #  • 2026-06-02 → falls in invoice 30/05 cycle
    await db.unified_orders.insert_many([
        {"user_id": uid, "payment_method": "تمارا",
         "order_number": f"O-{uid}-1",
         "order_date": "2026-05-25", "total_amount": 200.0,
         "refund_amount": 0},
        {"user_id": uid, "payment_method": "tamara",
         "order_number": f"O-{uid}-2",
         "order_date": "2026-05-28", "total_amount": 300.0,
         "refund_amount": 0},
        {"user_id": uid, "payment_method": "tamara",
         "order_number": f"O-{uid}-3",
         "order_date": "2026-06-02", "total_amount": 50.0,
         "refund_amount": 0},
    ])

    async def _dep():
        return _user(uid)
    router = make_settlement_engine_router(db, _dep)

    handler = None
    for r in router.routes:
        if r.path.endswith("/dry-run-details") and "GET" in (r.methods or set()):
            handler = r.endpoint
            break
    assert handler is not None
    out = await handler(provider="tamara", user=_user(uid))
    prov_block = out["providers"]["tamara"]
    inv_dates = [i["invoice_date"] for i in prov_block["invoices"]]
    assert inv_dates == ["2026-05-23", "2026-05-30", "2026-06-06"]
    # First invoice (23/05) contains BOTH 25 + 28 May orders.
    by_inv = {i["invoice_date"]: i for i in prov_block["invoices"]}
    assert by_inv["2026-05-23"]["orders_count"] == 2
    assert by_inv["2026-05-23"]["gross_sales"] == 500.0
    assert by_inv["2026-05-30"]["orders_count"] == 1
    assert by_inv["2026-05-30"]["gross_sales"] == 50.0
    assert by_inv["2026-06-06"]["orders_count"] == 0
    # Period boundaries match Tamara reality (Sat → Fri).
    assert by_inv["2026-05-23"]["period_from"] == "2026-05-23"
    assert by_inv["2026-05-23"]["period_to"]   == "2026-05-29"
    assert by_inv["2026-05-30"]["period_from"] == "2026-05-30"
    assert by_inv["2026-05-30"]["period_to"]   == "2026-06-05"
    assert prov_block["cycle"]["uses_calendar"] is True


@pytest.mark.asyncio
async def test_delete_calendar_entry(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    e = await upsert_manual_entry(
        db, uid, _user(uid), "tamara",
        invoice_date="2026-08-01",
        period_start="2026-07-26", period_end="2026-08-01",
        expected_transfer_date="2026-08-03",
    )
    assert await delete_entry(db, uid, e["id"]) is True
    cal = await get_calendar(db, uid, "tamara")
    assert all(c["id"] != e["id"] for c in cal)
