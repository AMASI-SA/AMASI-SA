"""Real Mongo driver responsibility and two-stage POS settlement contracts."""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport

from test_mz2_shipping_native import db, bank, OWNER, CUT, AT, rate, source, report, bind, movement, settlement
from accounting_shipping_native import recognize_cod, accrue_fee, settle, _rows, _balance
from accounting_shipping_native_setup import save_setup
from accounting_shipping_native_contract import EVENTS, EVIDENCE, INBOX
from accounting_shipping_native_observer import observe_driver_delivery
from accounting_driver_payment_review import DriverReviewInput, PosBankInput, review_driver_payment, settle_pos_to_bank
import accounting_driver_payment_port as port
import accounting_driver_payment_review as review_module
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router


async def driver_delivery(db, method, number="1"):
    await source(db, number)
    assignment = "assignment-" + number
    await db.store_delivery_assignments.insert_one({"user_id": OWNER, "id": assignment, "driver_id": "driver-f",
        "order_id": "salla-" + number, "order_number": number, "status": "delivered", "delivered_at": AT})
    await db.store_delivery_collections.insert_one({"user_id": OWNER, "id": "collection-" + number,
        "assignment_id": assignment, "driver_id": "driver-f", "order_id": "salla-" + number, "order_number": number,
        "payment_method": method, "amount": "500.00", "cod_custody_amount": "500.00" if method == "cash" else "0.00",
        "accounting_status": "operational_only", "review_status": "not_required" if method == "cash" else "pending_accountant_review",
        "delivery_proof_reference": "proof-" + number, "receipt_reference": "receipt-" + number})
    await db.store_delivery_delivery_proofs.insert_one({"user_id": OWNER, "driver_id": "driver-f",
        "token": "proof-" + number, "status": "bound", "bound_assignment_id": assignment})
    if method != "cash":
        await db.store_delivery_payment_reviews.insert_one({"user_id": OWNER, "id": "review-" + number,
            "assignment_id": assignment, "driver_id": "driver-f", "order_id": "salla-" + number, "order_number": number,
            "status": "pending", "revision": 1, "payment_method": method, "amount": "500.00",
            "receipt_reference": "receipt-" + number, "bank_account_id": "legacy-display-only"})
        content = ("verified receipt " + number).encode()
        await db.store_delivery_receipts.insert_one({"user_id": OWNER, "driver_id": "driver-f", "assignment_id": assignment,
            "token": "receipt-" + number, "status": "bound", "content": content, "sha256": hashlib.sha256(content).hexdigest()})
        if method == "bank_transfer":
            await movement(db, "verified-payment-" + number, "500", "in")
    return assignment


def accept(method, reference="verified-payment-1"):
    return DriverReviewInput(decision="approved", note="Verified exact transaction",
        destination_financial_id="bank-f" if method == "bank_transfer" else "pos-f", settlement_reference=reference)


async def review(db, assignment, payload):
    return await review_driver_payment(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment, payload=payload)


async def balance(db):
    return (await report(db, "store_driver", "driver-f"))["cod_receivable"]


async def totals(db):
    rows = await _rows(db, OWNER)
    return {"bank": _balance(rows, ("bank", "bank-f", "main")),
            "pos": _balance(rows, ("payment_provider", "pos-f", "receivable")),
            "revenue": -sum((Decimal(r["amount"]) * (1 if r["side"] == "debit" else -1)
                for r in rows if r["entity_type"] == "revenue"), Decimal(0))}


@pytest.fixture
def destination(monkeypatch):
    # Explicit final-integration test seam, not a live provider/bank bypass.
    async def resolver(db, owner, **facts):
        pos = facts["payment_method"] == "card_terminal"
        return {"user_id": owner, "financial_account_id": facts["financial_account_id"],
            "destination_kind": "pos_receivable" if pos else "bank", "entity_type": "payment_provider" if pos else "bank",
            "entity_id": "pos-f" if pos else "bank-f", "sub_account": "receivable" if pos else "main",
            "currency": "SAR", "amount": facts["amount"], "status": "verified", "source_revision": "verified-fixture-v1",
            "evidence_reference": facts["evidence_reference"], "receipt_hash": facts["receipt_hash"],
            "source_namespace": "processor" if pos else "native_bank_movement", "source_record_id": facts["evidence_reference"],
            "bank_movement_id": None if pos else facts["evidence_reference"],
            "verification": "pos_transaction_successful" if pos else "bank_arrival_confirmed"}
    monkeypatch.setattr(port, "require_driver_payment_destination", resolver)
    return resolver


async def pos_opening(db):
    state = (await db.settings.find_one({"user_id": OWNER}))["mezan2_financial_cutover"]
    await db.settings.update_one({"user_id": OWNER}, {"$push": {"mezan2_financial_cutover.opening_balance_zero_accounts": {
        "entity_type": "payment_provider", "entity_id": "pos-f", "sub_account": "receivable",
        "accounting_at": CUT, "evidence_ref": "synthetic-approved-pos-identity", "opening_balance_txn_group_id": state["opening_active_txn_group_id"]}}})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["cash", "bank_transfer", "card_terminal"])
async def test_full_responsibility_at_delivery_independent_of_cash_custody(db, method):
    assignment = await driver_delivery(db, method)
    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
    result = await observe_driver_delivery(db, owner=OWNER, assignment_id=assignment)
    assert result["state"] == "posted"
    assert await balance(db) == "500.00"
    assert (await report(db, "store_driver", "driver-f"))["payable"] == "20.00"
    assert (await totals(db))["bank"] == Decimal("1000")
    assert await db[EVENTS].count_documents({"kind": "driver_payment_review"}) == 0
    if method != "cash":
        assert (await db.store_delivery_payment_reviews.find_one({"assignment_id": assignment}))["status"] == "pending"
    assert (await observe_driver_delivery(db, owner=OWNER, assignment_id=assignment))["state"] == "already_posted"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal"])
async def test_accept_exact_destination_once_no_fee_netting(db, destination, method):
    assignment = await driver_delivery(db, method)
    await pos_opening(db)
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
    await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    before = await totals(db)
    app = FastAPI()
    async def actor(): return {"id": OWNER, "role": "owner"}
    app.include_router(make_store_delivery_payment_review_router(db, actor))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://synthetic") as client:
        response = await client.post('/store-delivery/payment-review/' + assignment, json=accept(method).model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert await balance(db) == "0.00"
    assert (await report(db, "store_driver", "driver-f"))["payable"] == "20.00"
    after = await totals(db)
    assert after["revenue"] == before["revenue"]
    assert after["bank"] == (Decimal("1500") if method == "bank_transfer" else Decimal("1000"))
    assert after["pos"] == (Decimal("500") if method == "card_terminal" else Decimal(0))
    assert (await review(db, assignment, accept(method)))["state"] == "already_posted"
    assert await db[EVENTS].count_documents({"kind": "driver_payment_review"}) == 1
    with pytest.raises(HTTPException, match="driver_payment_reversal_contract_required"):
        await review(db, assignment, DriverReviewInput(decision="rejected"))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal"])
async def test_reject_has_no_journal_and_keeps_full_responsibility(db, method):
    assignment = await driver_delivery(db, method)
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    before = await _rows(db, OWNER)
    result = await review(db, assignment, DriverReviewInput(decision="rejected", note="Invalid proof"))
    assert result["txn_group_id"] is None
    assert await balance(db) == "500.00"
    assert await _rows(db, OWNER) == before
    assert (await review(db, assignment, DriverReviewInput(decision="rejected", note="Invalid proof")))["state"] == "already_rejected"
    with pytest.raises(HTTPException, match="driver_payment_review_idempotency_conflict"):
        await review(db, assignment, accept(method))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal"])
async def test_absent_verified_evidence_leaves_pending_and_full_balance(db, method):
    if method == "bank_transfer":
        await db.mz2_financial_accounts.insert_one({"user_id": OWNER, "id": "bank-f",
            "account_type": "bank", "currency": "SAR", "status": "active"})
    assignment = await driver_delivery(db, method)
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    with pytest.raises(HTTPException, match=("native_bank_statement_evidence_required" if method == "bank_transfer"
            else "mz2_driver_payment_destination_not_integrated")):
        await review(db, assignment, accept(method))
    assert await balance(db) == "500.00"
    assert (await db.store_delivery_payment_reviews.find_one({"assignment_id": assignment}))["status"] == "pending"
    assert await db[EVENTS].count_documents({"kind": "driver_payment_review"}) == 0


@pytest.mark.asyncio
async def test_concurrent_accept_one_journal(db, destination):
    assignment = await driver_delivery(db, "bank_transfer")
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    results = await asyncio.gather(*[review(db, assignment, accept("bank_transfer")) for _ in range(4)])
    assert sum(r["state"] == "posted" for r in results) == 1
    assert await balance(db) == "0.00"
    assert (await totals(db))["bank"] == Decimal("1500")


@pytest.mark.asyncio
async def test_pos_cannot_be_substituted_with_bank(db, destination, monkeypatch):
    assignment = await driver_delivery(db, "card_terminal")
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    async def wrong(db, owner, **facts):
        result = await destination(db, owner, **facts)
        return {**result, "entity_type": "bank"}
    monkeypatch.setattr(port, "require_driver_payment_destination", wrong)
    with pytest.raises(HTTPException, match="driver_pos_receivable_identity_required"):
        await review(db, assignment, accept("card_terminal"))
    assert await balance(db) == "500.00"


@pytest.mark.asyncio
async def test_financial_failure_rolls_back_approval_and_journal(db, destination, monkeypatch):
    assignment = await driver_delivery(db, "bank_transfer")
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    original = review_module._post
    async def failed(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic after-journal interruption")
    monkeypatch.setattr(review_module, "_post", failed)
    with pytest.raises(RuntimeError, match="interruption"):
        await review(db, assignment, accept("bank_transfer"))
    assert await balance(db) == "500.00"
    assert (await totals(db))["bank"] == Decimal("1000")
    assert (await db.store_delivery_payment_reviews.find_one({"assignment_id": assignment}))["status"] == "pending"


@pytest.mark.asyncio
async def test_paused_accept_423_and_generic_receive_cannot_bypass_review(db, destination, bank):
    assignment = await driver_delivery(db, "bank_transfer")
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    await bind(db, "store_driver", "driver-f")
    await movement(db, "not-approved", "500", "in")
    with pytest.raises(HTTPException, match="driver_non_cash_approved_review_required"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("not-approved", kind="store_driver", identity="driver-f"))
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    with pytest.raises(HTTPException) as error:
        await review(db, assignment, accept("bank_transfer"))
    assert error.value.status_code == 423
    assert await balance(db) == "500.00"


@pytest.mark.asyncio
async def test_pos_later_bank_arrival_separate_journal_and_retry(db, destination, bank):
    assignment = await driver_delivery(db, "card_terminal")
    await pos_opening(db)
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    await review(db, assignment, accept("card_terminal"))
    assert await totals(db) == {"bank": Decimal("1000"), "pos": Decimal("500"), "revenue": Decimal("434.78")}
    await movement(db, "pos-arrival", "500", "in")
    payload = PosBankInput(request_id="pos-bank-1", movement_id="pos-arrival", note="Verified bank arrival")
    result = await settle_pos_to_bank(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment, payload=payload)
    assert result["state"] == "posted"
    assert await balance(db) == "0.00"
    assert await totals(db) == {"bank": Decimal("1500"), "pos": Decimal(0), "revenue": Decimal("434.78")}
    assert (await settle_pos_to_bank(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment, payload=payload))["state"] == "already_posted"


@pytest.mark.asyncio
async def test_cash_partial_full_receive_keeps_driver_fee_separate(db, bank):
    assignment = await driver_delivery(db, "cash")
    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
    await observe_driver_delivery(db, owner=OWNER, assignment_id=assignment)
    await bind(db, "store_driver", "driver-f")
    for key, value, expected in (("cash-300", "300", "200.00"), ("cash-200", "200", "0.00")):
        await movement(db, key, value, "in")
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement(key, kind="store_driver", identity="driver-f"))
        assert await balance(db) == expected
        assert (await report(db, "store_driver", "driver-f"))["payable"] == "20.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code", [
    ("missing_receipt", "driver_payment_receipt_required"),
    ("tampered_receipt", "driver_payment_receipt_integrity_failure"),
    ("foreign_receipt", "driver_payment_receipt_required"),
    ("wrong_amount", "driver_payment_review_source_conflict"),
    ("missing_arrival", "driver_verified_bank_arrival_required"),
    ("denied", "accountant_permission_required"),
])
async def test_approval_requires_bound_proof_arrival_scope_and_fresh_authority(db, destination, change, code):
    assignment = await driver_delivery(db, "bank_transfer")
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    if change == "missing_receipt":
        await db.store_delivery_receipts.delete_many({})
    elif change == "tampered_receipt":
        await db.store_delivery_receipts.update_one({}, {"$set": {"content": b"changed"}})
    elif change == "foreign_receipt":
        await db.store_delivery_receipts.update_one({}, {"$set": {"user_id": "other-owner"}})
    elif change == "wrong_amount":
        await db.store_delivery_payment_reviews.update_one({}, {"$set": {"amount": "400"}})
    elif change == "missing_arrival":
        await db.mz2_daily_movements.delete_many({})
    else:
        await db.users.update_one({"id": OWNER}, {"$set": {"denied_permissions": ["store_delivery.payments.review"]}})
    with pytest.raises(HTTPException, match=code):
        await review(db, assignment, accept("bank_transfer"))
    assert await balance(db) == "500.00"
    assert (await db.store_delivery_payment_reviews.find_one({"assignment_id": assignment}))["status"] == "pending"


@pytest.mark.asyncio
async def test_pos_proof_cannot_be_reused_by_second_order(db, destination):
    await pos_opening(db)
    for number in ("1", "2"):
        assignment = await driver_delivery(db, "card_terminal", number)
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    await review(db, "assignment-1", accept("card_terminal"))
    with pytest.raises(HTTPException, match="driver_payment_reference_already_consumed"):
        await review(db, "assignment-2", accept("card_terminal"))
    assert await balance(db) == "500.00"


@pytest.mark.asyncio
async def test_pos_bank_requires_actual_movement_and_bank_port_and_prevents_excess(db, destination, bank, monkeypatch):
    import accounting_shipping_bank_port as bank_port
    assignment = await driver_delivery(db, "card_terminal")
    await pos_opening(db)
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    await review(db, assignment, accept("card_terminal"))
    payload = PosBankInput(request_id="pos-bank-missing", movement_id="not-arrived", note="Arrival classification")
    with pytest.raises(HTTPException, match="driver_pos_bank_movement_required"):
        await settle_pos_to_bank(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment, payload=payload)
    await movement(db, "not-arrived", "501", "in")
    with pytest.raises(HTTPException, match="driver_pos_bank_over_settlement"):
        await settle_pos_to_bank(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment, payload=payload)
    await db.mz2_daily_movements.update_one({"id": "not-arrived"}, {"$set": {"amount": "500"}})
    async def closed(*args): raise HTTPException(503, detail={"code": "mz2_shipping_bank_port_not_integrated"})
    monkeypatch.setattr(bank_port, "require_shipping_bank_identity", closed)
    with pytest.raises(HTTPException, match="mz2_shipping_bank_port_not_integrated"):
        await settle_pos_to_bank(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment, payload=payload)
    assert (await totals(db))["pos"] == Decimal("500")
    assert (await totals(db))["bank"] == Decimal("1000")


@pytest.mark.asyncio
async def test_driver_delivery_does_not_need_salla_cod_method_or_cash_custody(db):
    assignment = await driver_delivery(db, "bank_transfer")
    # Operational delivery + bound proof is the driver event. An eventual Salla
    # payment update cannot turn the snapshot responsibility into zero.
    await source(db, payment_method="bank_transfer", remaining_amount="0", paid_amount="500",
                 status={"slug": "processing"})
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    assert await balance(db) == "500.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("observer_mode", ["posted", "paused", "failed"])
async def test_operational_delivery_reaches_native_observer_once_after_bound_evidence(db, monkeypatch, observer_mode):
    """Real delivery route preserves its response while handing off to Track F."""
    from unittest.mock import AsyncMock
    import accounting_shipping_native_observer as observer
    import store_delivery_driver_app_routes as driver_routes

    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
    await db.store_drivers.update_one({"id": "driver-f"}, {"$set": {"account_user_id": "driver-user"}})
    await db.unified_orders.update_one({"user_id": OWNER, "order_number": "1"}, {"$set": {"remaining_amount": "500.00"}})
    await db.store_delivery_assignments.insert_one({"id": "route-assignment", "user_id": OWNER,
        "driver_id": "driver-f", "order_id": "salla-1", "order_number": "1", "active": True,
        "status": "out_for_delivery", "delivery_fee_snapshot": "20.00"})
    for collection, token in ((driver_routes.DELIVERY_PROOFS, "route-proof"),
                              (driver_routes.CUSTOMER_CONVERSATION_EVIDENCE, "route-conversation")):
        await db[collection].insert_one({"user_id": OWNER, "driver_id": "driver-f",
            "assignment_id": "route-assignment", "token": token, "status": "uploaded"})
    if observer_mode == "paused":
        await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    salla = AsyncMock(return_value={"slug": "delivered", "verified_slug": "delivered"})
    monkeypatch.setattr(driver_routes, "_push_salla_delivery_status", salla)
    original = observer.observe_driver_delivery
    calls = []

    async def observed(database, *, owner, assignment_id, actor_id=None):
        assignment = await database.store_delivery_assignments.find_one({"id": assignment_id})
        collection = await database.store_delivery_collections.find_one({"assignment_id": assignment_id})
        proof = await database[driver_routes.DELIVERY_PROOFS].find_one({"token": "route-proof"})
        assert assignment["status"] == "delivered"
        assert collection["accounting_status"] == "operational_only"
        assert proof["status"] == "bound" and proof["bound_assignment_id"] == assignment_id
        calls.append((owner, assignment_id))
        if observer_mode == "failed":
            raise RuntimeError("synthetic observer infrastructure failure")
        return await original(database, owner=owner, assignment_id=assignment_id, actor_id=actor_id)

    monkeypatch.setattr(observer, "observe_driver_delivery", observed)
    async def user():
        return {"id": "driver-user", "role": "store_driver", "created_by": OWNER}
    app = FastAPI()
    app.include_router(driver_routes.make_store_delivery_driver_app_router(db, user))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/store-delivery/app/deliveries/status", json={
            "barcode": "1", "target_status": "delivered", "payment_method": "cash",
            "delivery_proof_reference": "route-proof", "conversation_evidence_reference": "route-conversation"})
    assert response.status_code == 200, response.text
    assert calls == [(OWNER, "route-assignment")]
    salla.assert_awaited_once()
    result = response.json()
    assert result["status"] == "delivered"
    assert result["earning_amount"] == 20
    assert result["authoritative_outstanding_amount"] == result["collection"]["amount"] == 500
    assert result["delivery_status_evidence_reference"] == "route-conversation"
    assert (await db[driver_routes.CUSTOMER_CONVERSATION_EVIDENCE].find_one({"token": "route-conversation"}))["status"] == "bound"
    assert await db[EVENTS].count_documents({"kind": "driver_payment_review"}) == 0
    if observer_mode == "posted":
        assert await balance(db) == "500.00"
        assert (await report(db, "store_driver", "driver-f"))["payable"] == "20.00"
        assert await db[INBOX].count_documents({"state": "processed"}) == 1
    else:
        assert await db[EVIDENCE].count_documents({}) == 0
        if observer_mode == "paused":
            pending = await db[INBOX].find_one({"state": "pending"})
            assert pending["result"]["code"] == "mz2_writes_paused"


@pytest.mark.asyncio
@pytest.mark.parametrize("case,changes", [
    ("canonical", {}),
    ("legacy_only", None),
    ("foreign", {"user_id": "another-owner"}),
    ("inactive", {"status": "inactive"}),
    ("archived", {"archived": True}),
    ("deleted", {"is_deleted": True}),
    ("disabled", {"is_active": False}),
    ("cash", {"account_type": "cash"}),
    ("foreign_currency", {"currency": "USD"}),
    ("missing_currency", {"currency": None}),
])
async def test_driver_bank_selection_uses_canonical_owner_active_sar_bank_without_approving_payment(db, monkeypatch, case, changes):
    from unittest.mock import AsyncMock
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    import store_delivery_driver_app_routes as driver_routes

    # Separate seed client: the fixture listener must see zero Legacy access
    # from the actual selector/delivery/review paths, even when Legacy exists.
    seed_client = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
    try:
        await seed_client[db.name].accounts.insert_one({"id": "legacy-other" if case == "canonical" else "bank-f", "user_id": OWNER,
            "account_type": "bank", "status": "active", "name": "Legacy must not authorize",
            "provider": "legacy-provider", "iban": "LEGACY-ONLY", "account_number": "LEGACY-ONLY"})
    finally:
        seed_client.close()
    if changes is not None:
        bank_row = {"id": "bank-f", "user_id": OWNER, "account_type": "bank", "currency": "SAR",
                    "status": "active", "name": "Canonical SAR bank", **changes}
        if bank_row.get("currency") is None:
            bank_row.pop("currency")
        await db.mz2_financial_accounts.insert_one(bank_row)
    await db.store_drivers.update_one({"id": "driver-f"}, {"$set": {"account_user_id": "driver-user"}})
    await db.unified_orders.update_one({"user_id": OWNER, "order_number": "1"}, {"$set": {"remaining_amount": "500.00"}})
    await db.store_delivery_assignments.insert_one({"id": "bank-selection-assignment", "user_id": OWNER,
        "driver_id": "driver-f", "order_id": "salla-1", "order_number": "1", "active": True,
        "status": "out_for_delivery", "delivery_fee_snapshot": "20.00"})
    await db[driver_routes.DELIVERY_PROOFS].insert_one({"user_id": OWNER, "driver_id": "driver-f",
        "assignment_id": "bank-selection-assignment", "token": "bank-selection-proof", "status": "uploaded"})
    receipt = b"synthetic receipt is not bank arrival proof"
    await db[driver_routes.RECEIPTS].insert_one({"user_id": OWNER, "driver_id": "driver-f",
        "assignment_id": "bank-selection-assignment", "token": "bank-selection-receipt", "status": "uploaded",
        "content": receipt, "sha256": hashlib.sha256(receipt).hexdigest()})
    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
    salla = AsyncMock(return_value={"slug": "delivered", "verified_slug": "delivered"})
    monkeypatch.setattr(driver_routes, "_push_salla_delivery_status", salla)
    async def driver_user():
        return {"id": "driver-user", "role": "store_driver", "created_by": OWNER}
    async def accountant():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(driver_routes.make_store_delivery_driver_app_router(db, driver_user))
    app.include_router(make_store_delivery_payment_review_router(db, accountant))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        for path in ("/store-delivery/app/bank-accounts", "/store-delivery/payment-review/bank-accounts"):
            response = await client.get(path)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["source"] == "mz2_financial_accounts"
            expected = [{"id": "bank-f", "name": "Canonical SAR bank", "account_type": "bank",
                         "currency": "SAR", "status": "active"}] if case == "canonical" else []
            assert body["items"] == expected
            assert body["total"] == len(expected)
        response = await client.post("/store-delivery/app/deliveries/status", json={
            "barcode": "1", "target_status": "delivered", "payment_method": "bank_transfer",
            "bank_account_id": "bank-f", "receipt_reference": "bank-selection-receipt",
            "delivery_proof_reference": "bank-selection-proof"})
        if case != "canonical":
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "business_bank_account_invalid"
            salla.assert_not_awaited()
            assert await db.store_delivery_collections.count_documents({}) == 0
            assert await db[EVIDENCE].count_documents({}) == 0
            assert (await db.store_delivery_assignments.find_one({"id": "bank-selection-assignment"}))["status"] == "out_for_delivery"
            return
        assert response.status_code == 200, response.text
        salla.assert_awaited_once()
        collection = await db.store_delivery_collections.find_one({"assignment_id": "bank-selection-assignment"})
        assert collection["bank_account_id"] == "bank-f"
        assert collection["bank_name_snapshot"] == "Canonical SAR bank"
        assert collection["review_status"] == "pending_accountant_review" and collection["cod_custody_amount"] == 0
        assert await balance(db) == "500.00"
        assert (await report(db, "store_driver", "driver-f"))["collections"] == "0.00"
        response = await client.post("/store-delivery/payment-review/bank-selection-assignment", json={
            "decision": "approved", "destination_financial_id": "bank-f", "settlement_reference": "not-bank-proof"})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "native_bank_statement_evidence_required"
        assert (await db.store_delivery_payment_reviews.find_one({"assignment_id": "bank-selection-assignment"}))["status"] == "pending"
        assert await balance(db) == "500.00"
        assert (await totals(db))["bank"] == Decimal("1000")


@pytest.mark.asyncio
@pytest.mark.parametrize("case,changes", [
    ("canonical", {}), ("legacy_only", None),
    ("foreign", {"user_id": "another-owner"}), ("inactive", {"status": "inactive"}),
    ("archived", {"archived": True}), ("deleted", {"is_deleted": True}),
    ("disabled", {"is_active": False}), ("cash", {"account_type": "cash"}),
    ("foreign_currency", {"currency": "USD"}), ("missing_currency", {"currency": None}),
])
async def test_driver_resubmission_accepts_only_canonical_bank_and_never_settles(db, case, changes):
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from accounting_ledger_v2 import read_reporting_entries_v2
    from store_delivery_payment_resubmission_routes import make_store_delivery_payment_resubmission_router

    seed_client = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
    try:
        await seed_client[db.name].accounts.insert_one({"id": "legacy-other" if case == "canonical" else "bank-f", "user_id": OWNER,
            "account_type": "bank", "status": "active", "name": "Legacy must not authorize"})
    finally:
        seed_client.close()
    if changes is not None:
        bank_row = {"id": "bank-f", "user_id": OWNER, "account_type": "bank", "currency": "SAR",
                    "status": "active", "name": "Canonical SAR bank", **changes}
        if bank_row.get("currency") is None:
            bank_row.pop("currency")
        await db.mz2_financial_accounts.insert_one(bank_row)
    await db.store_drivers.update_one({"id": "driver-f"}, {"$set": {"account_user_id": "driver-user"}})
    assignment = await driver_delivery(db, "bank_transfer")
    await db.store_delivery_assignments.update_one({"id": assignment}, {"$set": {"active": True}})
    await db.store_delivery_payment_reviews.update_one({"assignment_id": assignment}, {"$set": {"status": "rejected"}})
    await db.store_delivery_collections.update_one({"assignment_id": assignment}, {"$set": {"review_status": "rejected", "payment_confirmed": False}})
    await db.store_delivery_receipts.insert_one({"user_id": OWNER, "driver_id": "driver-f", "assignment_id": assignment,
        "token": "replacement-receipt", "status": "uploaded"})
    before = await read_reporting_entries_v2(db, user_id=OWNER, effective_before="2026-10-01T00:00:00Z")
    async def user():
        return {"id": "driver-user", "role": "store_driver", "created_by": OWNER}
    app = FastAPI()
    app.include_router(make_store_delivery_payment_resubmission_router(db, user))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post(f"/store-delivery/app/payment-review/{assignment}/resubmit", json={
            "receipt_reference": "replacement-receipt", "bank_account_id": "bank-f"})
    if case == "canonical":
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "pending" and response.json()["revision"] == 2
        assert response.json()["bank_account_id"] == "bank-f"
        assert response.json()["bank_name_snapshot"] == "Canonical SAR bank"
        collection = await db.store_delivery_collections.find_one({"assignment_id": assignment})
        assert collection["review_status"] == "pending_accountant_review" and collection["payment_confirmed"] is False
        assert (await db.store_delivery_receipts.find_one({"token": "replacement-receipt"}))["status"] == "bound"
        assert (await db.store_delivery_receipts.find_one({"token": "receipt-1"}))["status"] == "superseded"
    else:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "business_bank_account_invalid"
        assert (await db.store_delivery_payment_reviews.find_one({"assignment_id": assignment}))["status"] == "rejected"
        assert (await db.store_delivery_receipts.find_one({"token": "replacement-receipt"}))["status"] == "uploaded"
        assert (await db.store_delivery_receipts.find_one({"token": "receipt-1"}))["status"] == "bound"
    assert await read_reporting_entries_v2(db, user_id=OWNER, effective_before="2026-10-01T00:00:00Z") == before
    assert await db[EVIDENCE].count_documents({}) == await db[EVENTS].count_documents({}) == 0
    assert (await db.mz2_daily_movements.find_one({"id": "verified-payment-1"}))["status"] == "unclassified"
