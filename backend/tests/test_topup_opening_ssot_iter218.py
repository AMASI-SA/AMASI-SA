"""Iter-218 — Ad-account `PUT /topup/{ledger_id}` and `PUT /opening`
must keep `general_ledger` in sync.

Scenarios:
  (1) POST /topup creates a balanced SSOT group with metadata.legacy_ledger_id.
  (2) PUT /topup edits amount → original SSOT group reversed + new
      SSOT group posted with new amount. Net effect on `ad_account.balance`
      ledger = new_amount.
  (3) PUT /opening with opening_balance change → balanced SSOT entry
      booked (ad_account.balance vs equity.opening_balance) that
      reflects the delta. Net `ad_account.balance` matches counterparty.balance.
  (4) PUT /opening re-set replaces (not stacks) the prior opening
      SSOT group → ledger total equals the latest opening_balance,
      not the cumulative sum.
"""
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from ledger_core import compute_balance  # noqa: E402


async def _seed_user_and_bank(db):
    uid = str(uuid.uuid4())
    await db.users.insert_one({
        "id": uid, "name": "Iter-218",
        "email": f"{uid[:6]}@t.io", "role": "user",
    })
    bank_id = str(uuid.uuid4())
    await db.accounts.insert_one({
        "id": bank_id, "user_id": uid, "name": "Test Bank",
        "account_type": "bank", "status": "active",
        "current_balance": 50000.0,
    })
    cp_id = str(uuid.uuid4())
    await db.counterparties.insert_one({
        "id": cp_id, "user_id": uid, "name": "Test Ad Account",
        "name_lower": "test ad account",
        "kind": "ad_account", "ad_provider": "snapchat",
        "external_account_id": "act_xxx",
        "currency": "SAR", "balance": 0.0, "debt_mode": "auto",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    return uid, bank_id, cp_id


@pytest.mark.asyncio
async def test_iter218_topup_edit_keeps_ssot_in_sync():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid, bank_id, cp_id = await _seed_user_and_bank(db)
    from server import app  # noqa: WPS433

    # Stub auth — set a synthetic JWT cookie. The auth middleware in
    # server.py reads `Bearer` or session cookie; for the test we
    # bypass it by patching the dependency.
    # The route's `current_user` dependency is a closure that calls
    # `get_current_user_from_db(request, db)` — imported at module
    # load time into ad_account_routes. Patching the binding inside
    # that module bypasses auth for the duration of this test.
    import ad_account_routes as _ad_mod
    _orig_helper = _ad_mod.get_current_user_from_db
    async def _fake_helper(request, db):
        return {"id": uid, "name": "Iter-218",
                "email": f"{uid[:6]}@t.io"}
    _ad_mod.get_current_user_from_db = _fake_helper

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as cli2:
            # (1) Create the topup
            r = await cli2.post(
                f"/api/ad-accounts/{cp_id}/topup",
                json={"amount": 1000.0,
                      "paid_from_account_id": bank_id,
                      "transaction_date": "2026-05-20"},
            )
            assert r.status_code == 200, r.text
            ledger_doc = await db.ad_account_ledger.find_one(
                {"user_id": uid, "counterparty_id": cp_id,
                 "type": "topup"},
                {"_id": 0, "id": 1},
            )
            assert ledger_doc, "ledger row not created"
            ledger_id = ledger_doc["id"]

            bal_after_topup = await compute_balance(
                db, user_id=uid, entity_type="ad_account",
                entity_id=cp_id, sub_account="balance",
            )
            assert bal_after_topup["net_balance"] == 1000.0

            # (2) Edit the topup to 1500
            r = await cli2.put(
                f"/api/ad-accounts/{cp_id}/topup/{ledger_id}",
                json={"amount": 1500.0},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["amount"] == 1500.0
            assert body["previous_amount"] == 1000.0
            assert body["ssot_previous_group_id"] is not None
            assert body["ssot_new_group_id"] is not None

            # SSOT must mirror the legacy value: ad_account.balance
            # ledger sum should equal 1500.
            bal_after_edit = await compute_balance(
                db, user_id=uid, entity_type="ad_account",
                entity_id=cp_id, sub_account="balance",
            )
            assert bal_after_edit["net_balance"] == 1500.0, (
                f"after edit ledger has {bal_after_edit['net_balance']}"
            )

            # (3) PUT /opening — set opening_balance to 200
            r = await cli2.put(
                f"/api/ad-accounts/{cp_id}/opening",
                json={"opening_balance": 200.0},
            )
            assert r.status_code == 200, r.text
            # The previous edited topup (1500) is still active in
            # ad_account.balance ledger. PUT /opening overwrites the
            # legacy `counterparties.balance` to 200, BUT the SSOT
            # contribution from /opening is a delta, not an overwrite.
            # Delta = 200 - 1500 = -1300 → CREDIT ad_account.balance 1300.
            bal_after_open = await compute_balance(
                db, user_id=uid, entity_type="ad_account",
                entity_id=cp_id, sub_account="balance",
            )
            # 1500 + (-1300) = 200, matching counterparties.balance.
            assert bal_after_open["net_balance"] == 200.0

            # (4) PUT /opening again — set opening_balance to 500
            r = await cli2.put(
                f"/api/ad-accounts/{cp_id}/opening",
                json={"opening_balance": 500.0},
            )
            assert r.status_code == 200, r.text
            bal_after_open2 = await compute_balance(
                db, user_id=uid, entity_type="ad_account",
                entity_id=cp_id, sub_account="balance",
            )
            # The prior /opening entry (-1300) was reversed by the
            # new /opening's pre-pass, then a fresh delta from 1500→500
            # is booked = -1000. Net ledger = 1500 - 1000 = 500.
            assert bal_after_open2["net_balance"] == 500.0, (
                f"after second opening: {bal_after_open2['net_balance']}"
            )

    finally:
        _ad_mod.get_current_user_from_db = _orig_helper
        app.dependency_overrides.clear()
        await db.counterparties.delete_many({"user_id": uid})
        await db.accounts.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.account_transactions.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
        await db.users.delete_one({"id": uid})
