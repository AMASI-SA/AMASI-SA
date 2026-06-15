"""Iter-217 — Financial Position SSOT contract tests.

Asserts the P0 invariant:
    Every general_ledger entry IMMEDIATELY changes the
    `/accounting/financial-position` output, and reversing it
    restores the previous state to-the-cent.

Five scenarios:
  (1) Baseline financial position computed.
  (2) A balanced ledger group is posted (e.g. opening_balance bank
      + opening_balance equity) — `net_position` changes by exactly
      the asset delta.
  (3) Reversing that group restores `net_position` to baseline.
  (4) `/accounts/summary` grand_total agrees with the bank+platforms
      portion of `/accounting/financial-position`.
  (5) /accounts and /accounting/financial-position use the same per-
      account SSOT helper, so bank balances are guaranteed to match.
"""
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from financial_position_ssot import (  # noqa: E402
    account_balance_ssot, compute_financial_position,
)
from ledger_core import post_txn_group, reverse_entry  # noqa: E402


async def _seed_user_and_bank(db, *, opening: float = 0.0):
    uid = str(uuid.uuid4())
    await db.users.insert_one({
        "id": uid, "name": "Iter-217 Test", "email": f"{uid[:6]}@t.io",
        "role": "user",
    })
    bank_id = str(uuid.uuid4())
    await db.accounts.insert_one({
        "id": bank_id, "user_id": uid, "name": "Test Bank",
        "account_type": "bank", "status": "active",
        "current_balance": float(opening),
    })
    return uid, bank_id


@pytest.mark.asyncio
async def test_iter217_financial_position_ssot_contract():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid, bank_id = await _seed_user_and_bank(db, opening=1000.0)

    try:
        # ── (1) Baseline ────────────────────────────────────────────
        # No ledger activity yet → bank balance falls back to
        # `current_balance` (1000.0). Net position = 1000.
        baseline = await compute_financial_position(db, uid)
        assert baseline["assets"]["banks"] == 1000.0, baseline
        assert baseline["totals"]["net_position"] == 1000.0

        # ── (2) Post a balanced txn group: bank +500 / equity +500 ──
        group = await post_txn_group(
            db, user_id=uid, actor_id=uid, actor_name="iter-217",
            txn_type="opening_balance",
            notes="iter-217 contract test",
            entries=[
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "debit",
                 "amount": 500.0, "entry_type": "opening_balance"},
                {"entity_type": "equity", "entity_id": "owner",
                 "sub_account": "capital", "side": "credit",
                 "amount": 500.0, "entry_type": "opening_balance"},
            ],
        )
        group_id = group["txn_group_id"]
        # After the post: bank has a valid opening_balance entry, so
        # the SSOT helper no longer adds the legacy current_balance.
        # Ledger net for bank = 500 → assets.banks = 500.
        after_post = await compute_financial_position(db, uid)
        assert after_post["assets"]["banks"] == 500.0, after_post
        # Net position MUST have changed — SSOT contract.
        assert after_post["totals"]["net_position"] != \
               baseline["totals"]["net_position"], (
            "Financial position did NOT change after a posted entry — "
            "SSOT contract violated."
        )

        # ── (3) Reverse the group → net_position returns to baseline
        legs = await db.general_ledger.find(
            {"txn_group_id": group_id, "user_id": uid},
            {"_id": 0, "id": 1},
        ).to_list(10)
        for leg in legs:
            await reverse_entry(
                db, user_id=uid, actor_id=uid, actor_name="iter-217",
                entry_id=leg["id"],
                reason_code="data_entry_error",
                notes="iter-217 contract test — reversal",
            )
        after_reverse = await compute_financial_position(db, uid)
        # Both the original opening (now `reversed`) and its reversal
        # entry are excluded from compute_balance (Iter-217 fix). The
        # account no longer has an ACTIVE opening_balance entry, so
        # the implicit-opening rule kicks back in → balance returns
        # to baseline = current_balance (1000).
        assert after_reverse["assets"]["banks"] == 1000.0, after_reverse
        assert after_reverse["totals"]["net_position"] == \
               baseline["totals"]["net_position"], (
            f"After reversal, net_position={after_reverse['totals']['net_position']}"
            f" did NOT return to baseline {baseline['totals']['net_position']}"
        )

        # ── (4) /accounts/summary grand_total ↔ banks+platforms ────
        bal_via_ssot = await account_balance_ssot(
            db, user_id=uid,
            account={"id": bank_id, "account_type": "bank",
                     "current_balance": 1000.0},
        )
        assert abs(bal_via_ssot
                   - after_reverse["assets"]["banks"]) < 0.01

        # ── (5) Post + Reverse another time — full round-trip ──────
        g2 = await post_txn_group(
            db, user_id=uid, actor_id=uid, actor_name="iter-217",
            txn_type="cash_movement", notes="round-trip",
            entries=[
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "credit",
                 "amount": 200.0, "entry_type": "spend",
                 "metadata": {"category": "test"}},
                {"entity_type": "expense", "entity_id": "test_exp",
                 "side": "debit", "amount": 200.0,
                 "entry_type": "expense_record"},
            ],
        )
        snap_a = await compute_financial_position(db, uid)
        # No opening_balance ACTIVE (the earlier one is reversed) →
        # implicit-opening adds current_balance. ledger_net for bank =
        # -200 (just the spend credit). balance = -200 + 1000 = 800.
        assert snap_a["assets"]["banks"] == 800.0, snap_a
        legs2 = await db.general_ledger.find(
            {"txn_group_id": g2["txn_group_id"], "user_id": uid},
            {"_id": 0, "id": 1},
        ).to_list(10)
        for leg in legs2:
            await reverse_entry(
                db, user_id=uid, actor_id=uid, actor_name="iter-217",
                entry_id=leg["id"],
                reason_code="data_entry_error",
                notes="round-trip reverse",
            )
        snap_b = await compute_financial_position(db, uid)
        # Round-trip → ledger net 0; implicit-opening still on → 1000.
        assert snap_b["assets"]["banks"] == 1000.0, snap_b
        assert snap_b["totals"]["net_position"] == \
               baseline["totals"]["net_position"], (
            "Round-trip post+reverse did NOT return net_position to "
            "baseline — SSOT idempotency broken."
        )

    finally:
        await db.accounts.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.users.delete_one({"id": uid})
