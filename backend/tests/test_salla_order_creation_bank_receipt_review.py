"""Synthetic receipt review store: same reference cannot fund two orders."""

import asyncio
from datetime import datetime, timezone

import pytest

from salla_order_creation.bank_receipt_review import (
    BankReviewDenied, save_preliminary_bank_receipt,
    ensure_pending_bank_review,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


class Rows:
    def __init__(self):
        self.rows = []

    async def insert_one(self, value):
        if any(row["_id"] == value["_id"] for row in self.rows):
            raise ValueError("synthetic unique Mongo _id")
        self.rows.append(dict(value))

    async def find_one(self, query):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None


class DB:
    def __init__(self):
        self.salla_ai_bank_receipt_claims = Rows()
        self.salla_ai_bank_receipt_preflights = Rows()
        self.salla_ai_bank_receipt_reviews = Rows()


def assessment(**changes):
    out = {
        "state": "preliminary_pass_for_human_review",
        "visual_only": True,
        "human_review_required": True,
        "bank_credit_confirmed": False,
        "store_bank_id": 123,
        "claimed_amount_sar": "200.00",
        "claimed_transfer_date": "2026-11-27",
        "reference_sha256": "a" * 64,
        "image_sha256": "b" * 64,
    }
    out.update(changes)
    return out


def save(db, intent="intent-0001", value=None):
    return asyncio.run(save_preliminary_bank_receipt(
        db, owner_id="owner", intent_id=intent,
        account_id=1, inbox_id=2, conversation_id=3,
        contact_id=77, source_message_id=501, attachment_id=901,
        assessment=value or assessment(), now=NOW,
    ))


def attach(db, intent="intent-0001", order_id=6001):
    return asyncio.run(ensure_pending_bank_review(
        db, owner_id="owner", intent_id=intent,
        order_id=order_id, reference_id=8021, now=NOW,
    ))


def test_preliminary_receipt_is_never_settled_and_has_no_media_bytes():
    db = DB()
    result = save(db)
    assert result["state"] == "preliminary_pass_for_human_review"
    assert result["bank_credit_confirmed"] is False
    assert result["receipt_ref"].startswith("receipt.")
    row = db.salla_ai_bank_receipt_preflights.rows[0]
    assert row["bank_credit_confirmed"] is False
    assert "receipt_raw_data" not in row
    assert "iban" not in str(row)
    assert "image_url" not in str(row)
    assert "bank_reference" not in str(row)


def test_same_receipt_event_replay_is_immutable():
    db = DB()
    first = save(db)
    second = save(db)
    assert first == second
    assert len(db.salla_ai_bank_receipt_preflights.rows) == 1
    assert len(db.salla_ai_bank_receipt_claims.rows) == 1


def test_one_bank_transfer_reference_cannot_fund_another_assisted_order():
    db = DB()
    save(db)
    with pytest.raises(BankReviewDenied, match="reused"):
        save(db, intent="intent-0002")
    assert len(db.salla_ai_bank_receipt_preflights.rows) == 1


def test_same_intent_different_receipt_or_bank_denied():
    db = DB()
    save(db)
    with pytest.raises(BankReviewDenied, match="conflict"):
        save(db, value=assessment(image_sha256="c" * 64))


def test_created_salla_order_is_held_for_human_bank_review():
    db = DB()
    save(db)
    response = attach(db)
    assert response == {
        "state": "awaiting_human_bank_review",
        "salla_order_id": 6001,
        "reference_id": 8021,
        "bank_credit_confirmed": False,
        "fulfillment_released": False,
    }
    record = db.salla_ai_bank_receipt_reviews.rows[0]
    assert record["payment_marked_paid"] is False
    assert record["gl_posted"] is False
    assert record["fulfillment_released"] is False
    assert record["attachment_id"] == 901


def test_review_attach_replay_never_posts_or_duplicates():
    db = DB()
    save(db)
    first = attach(db)
    second = attach(db)
    assert first == second
    assert len(db.salla_ai_bank_receipt_reviews.rows) == 1


def test_another_salla_order_cannot_replace_existing_bank_review():
    db = DB()
    save(db)
    attach(db)
    with pytest.raises(BankReviewDenied, match="different_salla_order"):
        attach(db, order_id=9999)


def test_bad_vision_claim_cannot_create_bank_review():
    db = DB()
    with pytest.raises(BankReviewDenied, match="verified_private_visual"):
        save(db, value=assessment(bank_credit_confirmed=True))
    assert db.salla_ai_bank_receipt_reviews.rows == []


def test_missing_receipt_blocks_review_ledger():
    db = DB()
    with pytest.raises(BankReviewDenied, match="receipt_preflight_not_verified"):
        attach(db)


def test_wrong_bank_source_account_not_model_owned():
    db = DB()
    save(db)
    db.salla_ai_bank_receipt_preflights.rows[0]["state"] = "model_claimed_paid"
    with pytest.raises(BankReviewDenied):
        attach(db)
