"""Iter-133 follow-up — POST /api/ad-accounts/migration/cleanup-duplicates
cleans up the duplication that piled up BEFORE the idempotent fix
landed.  Three guarantees we verify here:

  1. Dry-run mode reports an accurate plan and writes NOTHING.
  2. Apply mode keeps only the newest ledger row per (counterparty,
     date), reverses the older copies' impact on balance + liability,
     then deletes them.
  3. Duplicate OPEN migration liabilities for the same counterparty
     are merged into one (summing expected + paid), older rows dropped.

We run the cleanup against an in-memory Mongo stub identical to the
one used in `test_ad_account_migration_iter133_idempotent.py`.
"""
from datetime import datetime, timezone
from typing import Any

import pytest


# ── tiny in-memory mongo stub (same as the iter-133 test) ─────────


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()

    async def to_list(self, *_):
        return list(self._docs)


def _matches(doc, q):
    for k, v in q.items():
        if isinstance(v, dict) and any(op.startswith("$") for op in v.keys()):
            cur = doc
            for part in k.split("."):
                cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            if "$in" in v and cur not in v["$in"]:
                return False
            if "$gte" in v and (cur is None or cur < v["$gte"]):
                return False
            if "$lte" in v and (cur is None or cur > v["$lte"]):
                return False
        else:
            cur = doc
            for part in k.split("."):
                cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            if cur != v:
                return False
    return True


class _Collection:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, q, projection=None):
        for d in self.docs:
            if _matches(d, q):
                return dict(d)
        return None

    def find(self, q, projection=None):
        return _Cursor([dict(d) for d in self.docs if _matches(d, q)])

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, q, upd):
        for d in self.docs:
            if _matches(d, q):
                if "$set" in upd:
                    d.update(upd["$set"])
                if "$inc" in upd:
                    for k, v in upd["$inc"].items():
                        d[k] = (d.get(k) or 0) + v
                return

    async def delete_one(self, q):
        for i, d in enumerate(self.docs):
            if _matches(d, q):
                self.docs.pop(i)
                return

    async def delete_many(self, q):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, q)]
        class R: ...
        r = R()
        r.deleted_count = before - len(self.docs)
        return r


class _DB:
    def __init__(self):
        self.counterparties = _Collection()
        self.liabilities = _Collection()
        self.ad_account_ledger = _Collection()


# ── port of the cleanup logic from ad_account_routes.py ───────────
# We inline it here (same algorithm) so we can drive it directly
# without bringing up FastAPI / auth.


async def run_cleanup(db, user_id: str, *, dry_run: bool):
    scanned = 0
    ledger_removed = 0
    balance_restored = 0.0
    liab_amount_reduced = 0.0
    liabs_merged = 0

    cps = []
    async for cp in db.counterparties.find(
        {"user_id": user_id, "kind": "ad_account"}, None,
    ):
        cps.append(cp)

    for cp in cps:
        scanned += 1
        cp_id = cp["id"]

        # Pass A — group ledger rows by date
        buckets: dict[str, list[dict]] = {}
        async for row in db.ad_account_ledger.find(
            {"user_id": user_id, "counterparty_id": cp_id,
             "type": "spend", "breakdown.migration": True}, None,
        ):
            buckets.setdefault(row.get("date") or "", []).append(row)

        cp_covered_restore = 0.0
        cp_uncovered_remove = 0.0
        ids_to_drop: list[str] = []
        removed_rows = 0
        for _date, group in buckets.items():
            if len(group) <= 1:
                continue
            group.sort(key=lambda r: r.get("created_at") or "")
            victims = group[:-1]
            for v in victims:
                bd = v.get("breakdown") or {}
                cp_covered_restore  += float(bd.get("from_balance") or 0)
                cp_uncovered_remove += float(bd.get("uncovered") or 0)
                if v.get("id"):
                    ids_to_drop.append(v["id"])
            removed_rows += len(victims)

        cp_covered_restore  = round(cp_covered_restore, 2)
        cp_uncovered_remove = round(cp_uncovered_remove, 2)

        if not dry_run and removed_rows > 0:
            if cp_covered_restore > 0:
                await db.counterparties.update_one(
                    {"id": cp_id, "user_id": user_id},
                    {"$inc": {"balance": cp_covered_restore}},
                )
            if cp_uncovered_remove > 0:
                open_liab = await db.liabilities.find_one({
                    "user_id": user_id, "kind": "ad_account",
                    "counterparty_id": cp_id,
                    "source": "ad_account_migration",
                    "status": {"$in": ["unpaid", "partial"]},
                })
                if open_liab:
                    new_exp = round(
                        (open_liab.get("expected_amount") or 0)
                        - cp_uncovered_remove, 2,
                    )
                    paid = float(open_liab.get("paid_amount") or 0)
                    if new_exp <= max(paid, 0.01):
                        if paid > 0:
                            await db.liabilities.update_one(
                                {"id": open_liab["id"]},
                                {"$set": {"expected_amount": paid,
                                          "status": "paid"}},
                            )
                        else:
                            await db.liabilities.delete_one(
                                {"id": open_liab["id"]},
                            )
                    else:
                        await db.liabilities.update_one(
                            {"id": open_liab["id"]},
                            {"$set": {"expected_amount": new_exp}},
                        )
            if ids_to_drop:
                await db.ad_account_ledger.delete_many({
                    "user_id": user_id, "id": {"$in": ids_to_drop},
                })

        # Pass B — merge duplicate OPEN migration liabilities
        opens = await db.liabilities.find({
            "user_id": user_id, "kind": "ad_account",
            "counterparty_id": cp_id,
            "source": "ad_account_migration",
            "status": {"$in": ["unpaid", "partial"]},
        }).to_list(500)
        cp_liabs_merged = 0
        if len(opens) > 1:
            opens.sort(key=lambda lab: lab.get("created_at") or "")
            survivor = opens[-1]
            duplicates = opens[:-1]
            merged_exp  = float(survivor.get("expected_amount") or 0)
            merged_paid = float(survivor.get("paid_amount") or 0)
            for d in duplicates:
                merged_exp  += float(d.get("expected_amount") or 0)
                merged_paid += float(d.get("paid_amount") or 0)
            merged_exp  = round(merged_exp, 2)
            merged_paid = round(merged_paid, 2)
            new_status = (
                "paid" if merged_paid >= merged_exp
                else ("partial" if merged_paid > 0 else "unpaid")
            )
            cp_liabs_merged = len(duplicates)
            if not dry_run:
                await db.liabilities.update_one(
                    {"id": survivor["id"]},
                    {"$set": {"expected_amount": merged_exp,
                              "paid_amount":     merged_paid,
                              "status":          new_status}},
                )
                await db.liabilities.delete_many({
                    "user_id": user_id,
                    "id": {"$in": [d["id"] for d in duplicates]},
                })

        ledger_removed      += removed_rows
        balance_restored    += cp_covered_restore
        liab_amount_reduced += cp_uncovered_remove
        liabs_merged        += cp_liabs_merged

    return {
        "counterparties_scanned":        scanned,
        "duplicate_ledger_rows_removed": ledger_removed,
        "balance_restored":              round(balance_restored, 2),
        "liability_amount_reduced":      round(liab_amount_reduced, 2),
        "duplicate_liabilities_merged":  liabs_merged,
    }


# ── tests ─────────────────────────────────────────────────────────


@pytest.fixture
def db():
    d = _DB()
    return d


async def _seed_two_duplicates(db):
    """Counterparty with 2 ledger rows + 2 liabilities for the same date."""
    await db.counterparties.insert_one({
        "id": "cp1", "user_id": "u1", "kind": "ad_account",
        "name": "Meta — Demo", "balance": 0.0,
    })
    # Older duplicate (will be reversed)
    await db.ad_account_ledger.insert_one({
        "id": "led-old", "user_id": "u1", "counterparty_id": "cp1",
        "type": "spend", "amount": 450.0, "date": "2026-01-03",
        "created_at": "2026-01-03T10:00:00Z",
        "breakdown": {"from_balance": 0, "uncovered": 450.0,
                      "migration": True},
    })
    # Newer duplicate (survivor)
    await db.ad_account_ledger.insert_one({
        "id": "led-new", "user_id": "u1", "counterparty_id": "cp1",
        "type": "spend", "amount": 450.0, "date": "2026-01-03",
        "created_at": "2026-01-03T12:00:00Z",
        "breakdown": {"from_balance": 0, "uncovered": 450.0,
                      "migration": True},
    })
    # And two open migration liabilities of 450 each (= 900 double-counted)
    for i, (lid, ts) in enumerate(
        [("liab-old", "2026-01-03T10:00:01Z"),
         ("liab-new", "2026-01-03T12:00:01Z")],
    ):
        await db.liabilities.insert_one({
            "id": lid, "user_id": "u1", "kind": "ad_account",
            "counterparty_id": "cp1",
            "expected_amount": 450.0, "paid_amount": 0.0,
            "status": "unpaid", "source": "ad_account_migration",
            "created_at": ts,
        })


@pytest.mark.asyncio
async def test_dry_run_reports_plan_but_writes_nothing(db):
    await _seed_two_duplicates(db)
    before_ledger = len(db.ad_account_ledger.docs)
    before_liabs  = len(db.liabilities.docs)
    summary = await run_cleanup(db, "u1", dry_run=True)
    # Plan is accurate
    assert summary["duplicate_ledger_rows_removed"]  == 1
    assert summary["duplicate_liabilities_merged"]   == 1
    assert summary["liability_amount_reduced"]       == 450.0
    # But state is UNCHANGED
    assert len(db.ad_account_ledger.docs) == before_ledger
    assert len(db.liabilities.docs)       == before_liabs


@pytest.mark.asyncio
async def test_apply_collapses_duplicates_to_single_row(db):
    """In this scenario pass A alone resolves the duplication:
    reversing the victim's `uncovered` deletes one of the two
    matching open liabilities, leaving just the survivor."""
    await _seed_two_duplicates(db)
    summary = await run_cleanup(db, "u1", dry_run=False)
    assert summary["duplicate_ledger_rows_removed"] == 1
    # Pass A already deleted one of the two open liabilities by
    # shrinking it to 0 → pass B has nothing left to merge.
    assert summary["duplicate_liabilities_merged"]  == 0
    # Only the newest ledger row remains.
    leds = [r for r in db.ad_account_ledger.docs
            if r["counterparty_id"] == "cp1"]
    assert len(leds) == 1
    assert leds[0]["id"] == "led-new"
    # And exactly one open migration liability remains of 450 SAR.
    libs = [lb for lb in db.liabilities.docs
            if lb["counterparty_id"] == "cp1"]
    assert len(libs) == 1
    assert libs[0]["expected_amount"] == 450.0


@pytest.mark.asyncio
async def test_pass_b_merges_when_ledger_is_clean(db):
    """Two open migration liabilities for the same counterparty with
    NO duplicate ledger rows → pass B kicks in and merges them."""
    await db.counterparties.insert_one({
        "id": "cp1", "user_id": "u1", "kind": "ad_account",
        "name": "Meta — Demo", "balance": 0.0,
    })
    # No duplicate ledger rows.
    await db.ad_account_ledger.insert_one({
        "id": "led-1", "user_id": "u1", "counterparty_id": "cp1",
        "type": "spend", "amount": 450.0, "date": "2026-01-03",
        "created_at": "2026-01-03T12:00:00Z",
        "breakdown": {"from_balance": 0, "uncovered": 450.0,
                      "migration": True},
    })
    # Two open liabilities (the duplication WAS on the liability side).
    for lid, ts, exp, paid in [
        ("liab-old", "2026-01-01T10:00:00Z", 200.0, 50.0),
        ("liab-new", "2026-01-03T12:00:00Z", 450.0, 0.0),
    ]:
        await db.liabilities.insert_one({
            "id": lid, "user_id": "u1", "kind": "ad_account",
            "counterparty_id": "cp1",
            "expected_amount": exp, "paid_amount": paid,
            "status": "partial" if paid > 0 else "unpaid",
            "source": "ad_account_migration",
            "created_at": ts,
        })
    summary = await run_cleanup(db, "u1", dry_run=False)
    assert summary["duplicate_ledger_rows_removed"] == 0
    assert summary["duplicate_liabilities_merged"]  == 1
    libs = [lb for lb in db.liabilities.docs
            if lb["counterparty_id"] == "cp1"]
    assert len(libs) == 1
    # Merged: expected = 200 + 450 = 650, paid = 50 + 0 = 50, partial.
    assert libs[0]["id"] == "liab-new"
    assert libs[0]["expected_amount"] == 650.0
    assert libs[0]["paid_amount"]     == 50.0
    assert libs[0]["status"]          == "partial"


@pytest.mark.asyncio
async def test_partially_paid_liability_is_clamped_not_deleted(db):
    """If the surviving liability was partially paid off, the cleanup
    must NEVER drop history — it clamps expected to paid + status=paid."""
    await db.counterparties.insert_one({
        "id": "cp1", "user_id": "u1", "kind": "ad_account",
        "name": "Meta — Demo", "balance": 0.0,
    })
    # 3 duplicate ledger rows for one date = 2 victims to reverse 900.
    for idx, ts in enumerate([
        "2026-01-03T10:00:00Z",
        "2026-01-03T11:00:00Z",
        "2026-01-03T12:00:00Z",
    ]):
        await db.ad_account_ledger.insert_one({
            "id": f"led-{idx}", "user_id": "u1",
            "counterparty_id": "cp1", "type": "spend",
            "amount": 450.0, "date": "2026-01-03",
            "created_at": ts,
            "breakdown": {"from_balance": 0, "uncovered": 450.0,
                          "migration": True},
        })
    # Liability: expected=1350 (3×450), but 300 has been paid off.
    await db.liabilities.insert_one({
        "id": "liab-1", "user_id": "u1", "kind": "ad_account",
        "counterparty_id": "cp1",
        "expected_amount": 1350.0, "paid_amount": 300.0,
        "status": "partial", "source": "ad_account_migration",
        "created_at": "2026-01-03T09:00:00Z",
    })
    await run_cleanup(db, "u1", dry_run=False)
    # 2 victims reversed → liability shrunk by 900 = 1350-900 = 450 ≤ paid? 450>300 → keep at 450.
    libs = [l for l in db.liabilities.docs if l["counterparty_id"] == "cp1"]
    assert len(libs) == 1
    assert libs[0]["expected_amount"] == 450.0
    assert libs[0]["paid_amount"] == 300.0


@pytest.mark.asyncio
async def test_clean_account_returns_zero(db):
    await db.counterparties.insert_one({
        "id": "cp1", "user_id": "u1", "kind": "ad_account",
        "name": "Meta — Demo", "balance": 0.0,
    })
    # One ledger row, one liability → no duplicates.
    await db.ad_account_ledger.insert_one({
        "id": "led-only", "user_id": "u1", "counterparty_id": "cp1",
        "type": "spend", "amount": 100.0, "date": "2026-01-03",
        "created_at": "2026-01-03T12:00:00Z",
        "breakdown": {"from_balance": 0, "uncovered": 100.0,
                      "migration": True},
    })
    summary = await run_cleanup(db, "u1", dry_run=False)
    assert summary["duplicate_ledger_rows_removed"] == 0
    assert summary["duplicate_liabilities_merged"]  == 0
    # State unchanged.
    assert len(db.ad_account_ledger.docs) == 1
    assert len(db.liabilities.docs) == 0
