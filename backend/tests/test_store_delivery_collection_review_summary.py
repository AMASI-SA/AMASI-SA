"""A receipt's review state must never inflate cash custody or hide bank payments."""
import copy

import pytest
from mongomock_motor import AsyncMongoMockClient

from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_EARNINGS
from store_delivery_settlement_routes import SETTLEMENTS, _totals


@pytest.mark.asyncio
async def test_review_totals_keep_pending_approved_rejected_and_unknown_separate():
    db = AsyncMongoMockClient().review_summary
    rows = [
        {"payment_method": "cash", "amount": 500, "cod_custody_amount": 500},
        {"payment_method": "card_terminal", "amount": 200.25, "review_status": "pending"},
        {"payment_method": "card_terminal", "amount": 99.75, "review_status": "pending_accountant_review"},
        {"payment_method": "card_terminal", "amount": 80, "review_status": "approved"},
        {"payment_method": "card_terminal", "amount": 40, "review_status": "rejected"},
        {"payment_method": "bank_transfer", "amount": 125, "review_status": "pending_accountant_review"},
        {"payment_method": "bank_transfer", "amount": 250, "review_status": "approved"},
        {"payment_method": "bank_transfer", "amount": 30, "review_status": "rejected"},
        {"payment_method": "bank_transfer", "amount": 15},
    ]
    await db[DRIVER_COLLECTIONS].insert_many([
        {"user_id": "owner", "driver_id": "driver", "id": str(index), **row}
        for index, row in enumerate(rows)
    ])
    await db[DRIVER_EARNINGS].insert_one({"user_id": "owner", "driver_id": "driver", "amount": 20})
    await db[SETTLEMENTS].insert_one({
        "user_id": "owner", "driver_id": "driver", "amount": 100,
        "settlement_type": "cod_remittance", "status": "pending_driver_confirmation",
    })
    before = copy.deepcopy(await db[DRIVER_COLLECTIONS].find({}).to_list(length=100))
    summary = await _totals(db, "owner", "driver")
    assert summary["noncash_payment_reviews"] == {
        "card_terminal": {
            "pending": {"count": 2, "amount": 300.0},
            "approved": {"count": 1, "amount": 80.0},
            "rejected": {"count": 1, "amount": 40.0},
            "unknown": {"count": 0, "amount": 0.0},
        },
        "bank_transfer": {
            "pending": {"count": 1, "amount": 125.0},
            "approved": {"count": 1, "amount": 250.0},
            "rejected": {"count": 1, "amount": 30.0},
            "unknown": {"count": 1, "amount": 15.0},
        },
    }
    assert summary["cod_cash_custody"] == 500
    assert summary["delivery_earnings_due"] == 20
    assert summary["pending_cod_remittance"] == 100
    assert summary["ledger_cod_receivable"] is None
    assert summary["ledger_delivery_fee_payable"] is None
    assert summary["ledger_net_balance"] is None
    assert await db[DRIVER_COLLECTIONS].find({}).to_list(length=100) == before


@pytest.mark.asyncio
async def test_review_summary_does_not_mix_another_driver_or_merchant():
    db = AsyncMongoMockClient().review_summary
    await db[DRIVER_COLLECTIONS].insert_many([
        {"user_id": owner, "driver_id": driver, "payment_method": "bank_transfer", "amount": amount,
         "review_status": "pending"}
        for owner, driver, amount in [
            ("owner", "driver", 25), ("owner", "other-driver", 300), ("other-owner", "driver", 900),
        ]
    ])
    summary = await _totals(db, "owner", "driver")
    assert summary["noncash_payment_reviews"]["bank_transfer"]["pending"] == {"count": 1, "amount": 25.0}
    assert summary["cod_cash_custody"] == 0
    assert summary["noncash_payment_reviews"]["bank_transfer"]["approved"] == {"count": 0, "amount": 0.0}
