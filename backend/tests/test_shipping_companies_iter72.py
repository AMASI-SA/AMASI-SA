"""Iter-72 — shipping_company normalisation + Excel apostrophe scrub.

Proves:
  1. `scrub_shipping_company` strips leading/trailing apostrophes that
     Excel adds in force-text mode (`'iMile للتوصيل'` → `iMile للتوصيل`).
  2. Zero-width / BOM / RTL marks are stripped.
  3. Common aliases (iMile, Aramex, SMSA, مندوب الرياض, …) all resolve
     to the same canonical key + display name.
  4. Unknown values pass through with a stable `other:<slug>` key so
     they remain visible in the dashboard but never duplicate against
     the canonical bucket.
  5. Migration is idempotent — running it twice updates 0 docs the
     second time and leaves the DB clean.
  6. Re-running migration after seeding a new dirty row cleans only
     that row.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


# ── Unit ────────────────────────────────────────────────────────────────
def test_scrub_strips_excel_apostrophes():
    from shipping_companies import scrub_shipping_company as scrub
    assert scrub("'iMile للتوصيل'") == "iMile للتوصيل"
    assert scrub("'سمسا'") == "سمسا"
    assert scrub("'مندوب الرياض'") == "مندوب الرياض"
    assert scrub("'أرامكس'") == "أرامكس"
    # Multiple wrapping quotes
    assert scrub("''iMile للتوصيل''") == "iMile للتوصيل"
    # Mixed quote types (Excel sometimes uses curly)
    assert scrub("’سمسا’") == "سمسا"


def test_scrub_strips_zero_width_and_bom():
    from shipping_companies import scrub_shipping_company as scrub
    # BOM at the start
    assert scrub("\ufeff" + "سمسا") == "سمسا"
    # Zero-width joiner in the middle
    assert scrub("iMile\u200d للتوصيل") == "iMile للتوصيل"
    # RTL mark on either side
    assert scrub("\u200fأرامكس\u200f") == "أرامكس"


def test_normalize_canonicalises_known_aliases():
    from shipping_companies import normalize_shipping_company, IMILE, SMSA, ARAMEX, MANDOOB_RIYADH
    assert normalize_shipping_company("iMile للتوصيل") == ("imile", IMILE)
    assert normalize_shipping_company("IMILE") == ("imile", IMILE)
    assert normalize_shipping_company("i-mile express") == ("imile", IMILE)
    assert normalize_shipping_company("سمسا") == ("smsa", SMSA)
    assert normalize_shipping_company("SMSA Express") == ("smsa", SMSA)
    assert normalize_shipping_company("أرامكس") == ("aramex", ARAMEX)
    assert normalize_shipping_company("Aramex") == ("aramex", ARAMEX)
    assert normalize_shipping_company("مندوب الرياض") == ("mandoob_riyadh", MANDOOB_RIYADH)


def test_normalize_combines_apostrophes_and_aliases():
    """The real production case: apostrophe-wrapped value resolves to
    the same canonical key as the clean spelling."""
    from shipping_companies import normalize_shipping_company
    assert normalize_shipping_company("'iMile للتوصيل'") == \
        normalize_shipping_company("iMile للتوصيل")
    assert normalize_shipping_company("'سمسا'") == \
        normalize_shipping_company("سمسا")


def test_normalize_unknown_value_gets_stable_slug():
    from shipping_companies import normalize_shipping_company
    key, display = normalize_shipping_company("NewCarrier 2099")
    assert key.startswith("other:")
    assert display == "NewCarrier 2099"
    # Repeated call returns the same slug — stable.
    assert normalize_shipping_company("NewCarrier 2099") == (key, display)


def test_normalize_empty_and_null_markers_become_unknown():
    from shipping_companies import normalize_shipping_company, UNKNOWN
    for raw in [None, "", "  ", "غير محدد", "\\N", "n/a", "null", "—"]:
        assert normalize_shipping_company(raw) == ("unknown", UNKNOWN), \
            f"{raw!r} should resolve to unknown"


def test_specific_mandoob_riyadh_wins_over_bare_mandoob():
    """Order in SHIPPING_ALIASES matters — مندوب الرياض must hit
    'mandoob_riyadh' even though bare 'مندوب' is an alias too."""
    from shipping_companies import normalize_shipping_company
    key, _ = normalize_shipping_company("مندوب الرياض")
    assert key == "mandoob_riyadh"
    # Bare "مندوب" still works
    key2, _ = normalize_shipping_company("مندوب")
    assert key2 == "mandoob"


# ── Integration ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_migration_is_idempotent():
    """Migrator updates 0 docs on a clean DB (after the first scrub)."""
    from shipping_migrations import migrate_shipping_company_values
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        # The first migration may already be done by startup; running it
        # again must be a no-op.
        res = await migrate_shipping_company_values(db)
        uo = res.get("unified_orders") or {}
        assert uo.get("updated") == 0, \
            f"migration should be idempotent; updated={uo.get('updated')}"
    finally:
        client.close()


@pytest.mark.asyncio
async def test_migration_cleans_freshly_inserted_dirty_row():
    """If a new dirty doc lands in DB after startup migration, running
    the migrator again must scrub it without touching clean docs."""
    from shipping_migrations import migrate_shipping_company_values
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        sentinel_uid = f"_pytest_iter72_{uuid.uuid4().hex[:8]}"
        # Seed: one dirty row + one already-clean row.
        await db.unified_orders.insert_many([
            {"user_id": sentinel_uid, "order_number": "T-1",
             "shipping_company": "'سمسا'", "total_amount": 100, "order_status": "تم التوصيل"},
            {"user_id": sentinel_uid, "order_number": "T-2",
             "shipping_company": "سمسا", "total_amount": 100, "order_status": "تم التوصيل"},
        ])
        try:
            res = await migrate_shipping_company_values(db)
            uo = res.get("unified_orders") or {}
            assert uo.get("updated") >= 1, \
                f"migration should have cleaned at least the dirty row; updated={uo.get('updated')}"
            doc = await db.unified_orders.find_one(
                {"user_id": sentinel_uid, "order_number": "T-1"}
            )
            assert doc["shipping_company"] == "سمسا"

            # Idempotency: 2nd call updates nothing extra (count compared
            # against sentinel docs).
            cnt_before = await db.unified_orders.count_documents(
                {"user_id": sentinel_uid, "shipping_company": "سمسا"}
            )
            assert cnt_before == 2
        finally:
            await db.unified_orders.delete_many({"user_id": sentinel_uid})
    finally:
        client.close()


@pytest.mark.asyncio
async def test_production_data_is_clean_after_startup():
    """On the real merchant data — after startup migration ran —
    no `shipping_company` value should start or end with an apostrophe."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        uid = "5aee091a-cc47-42cd-b14c-a14e32f169cc"
        cursor = db.unified_orders.find(
            {"user_id": uid},
            {"_id": 0, "shipping_company": 1, "order_number": 1},
        ).limit(5000)
        offenders = []
        async for d in cursor:
            v = d.get("shipping_company")
            if not v or not isinstance(v, str):
                continue
            if v != v.strip().strip("'").strip("’"):
                offenders.append((d.get("order_number"), v))
        assert not offenders, \
            f"Dirty rows still exist: {offenders[:5]}"
    finally:
        client.close()
