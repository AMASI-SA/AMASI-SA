"""Real Mongo + HTTP acceptance for evidence intake while native writes are paused."""
import asyncio
from io import BytesIO
import os
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from base64 import b64decode

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook, load_workbook
import pytest
import pytest_asyncio

from operational_balance_store import STATES, digest
from operational_driver_bank_store import FILES, IMPORTS, MOVEMENTS
from operational_driver_review_store import EVENTS
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router, ensure_store_delivery_payment_review_indexes
from store_delivery_payment_resubmission_routes import make_store_delivery_payment_resubmission_router
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router
from .test_store_delivery_noncash_review import seed, START

BANK = "account:legacy-1"
SECOND_BANK = "operational-bank:operational-1"
ACTOR = {"id": "owner", "role": "owner", "is_active": True}
DRIVER = {"id": "driver-account", "role": "store_driver", "created_by": "owner", "is_active": True}
PNG = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jf5kAAAAASUVORK5CYII=")
NATIVE = ("accounting_general_ledger_v2", "general_ledger", "mz2_shipping_native_events_v2",
          "mz2_daily_movements", "mz2_daily_movement_files", "accounting_source_files", "mz2_provider_receipts_v2")


async def prepare_review(db, *, bank_id=BANK, suffix="1", setup=True):
    assignment, _ = await seed(db, "card_terminal", suffix=suffix, setup=setup)
    if setup:
        await db.mz2_financial_accounts.delete_many({"user_id": "owner", "account_type": "bank"})
        await db.mz2_financial_accounts.insert_one({"user_id": "owner", "id": "cash-only", "name": "Synthetic cash",
                                                  "account_type": "cash", "currency": "SAR", "status": "active"})
        for number in range(1, 4):
            await db.accounts.insert_one({"user_id": "owner", "id": f"legacy-{number}", "name": f"Synthetic bank {number}",
                                          "account_type": "bank", "currency": "SAR", "status": "active"})
        await db.mz2_operational_bank_accounts.insert_one({"user_id": "owner", "id": "operational-1",
            "name": "Synthetic operational bank", "account_type": "bank", "currency": "SAR", "status": "active"})
        await db[STATES].update_one({"_id": "owner"}, {"$set": {"openings": {}}})
        await db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {
            "writes_paused": True, "revision": 0, "ledger_backend_state": "legacy_active"}}, upsert=True)
        await db.settings.insert_one({"user_id": "owner", "mezan2_financial_cutover": {"status": "not_configured"}})
    fields = {"payment_method": "bank_transfer", "bank_account_id": bank_id,
        "bank_identity_source": ("accounts" if bank_id.startswith("account:") else
                                 "mz2_operational_bank_accounts" if bank_id.startswith("operational-bank:") else "mz2_financial_accounts"),
        "bank_identity_version": 2, "bank_financial_source": "operational", "bank_operational_started_at": START}
    for name in ("store_delivery_collections", "store_delivery_payment_reviews"):
        await db[name].update_one({"assignment_id": assignment}, {"$set": fields})
    return assignment


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    parsed = urlparse(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not parsed.username and not parsed.password and parse_qs(parsed.query).get("replicaSet")
    connection = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    database = connection["courier_operational_bank_" + uuid4().hex]
    assert (await connection.admin.command("hello")).get("setName")
    await prepare_review(database)
    await ensure_store_delivery_payment_review_indexes(database)
    try:
        yield database
    finally:
        await connection.drop_database(database.name)
        connection.close()


def client_for(db, actor=ACTOR):
    app = FastAPI()
    app.include_router(make_store_delivery_payment_review_router(db, lambda: actor))
    app.include_router(make_store_delivery_payment_resubmission_router(db, lambda: actor))
    app.include_router(make_store_delivery_payment_evidence_router(db, lambda: actor))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def statement(*, amount="200.00", day="2026-10-06", reference="SYNTHETIC-BANK-1", direction="in", currency=None):
    book = Workbook()
    sheet = book.active
    headers = ["date", "amount", "direction", "description", "reference"]
    values = [day, amount, direction, "Synthetic customer transfer", reference]
    if currency is not None:
        headers.append("currency"); values.append(currency)
    sheet.append(headers); sheet.append(values)
    result = BytesIO(); book.save(result)
    return result.getvalue()


async def upload(client, content, *, bank=BANK, assignment="assignment-1", request_id="synthetic-upload-1", **changes):
    form = {"assignment_id": assignment, "bank_account_id": bank, "request_id": request_id,
            "expected_started_at": START, "expected_session_scope": digest(["owner", "owner"]), **changes}
    return await client.post("/store-delivery/payment-review/bank-statements/upload", data=form,
        files={"file": ("synthetic.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def decision(movement, *, bank=BANK):
    return {"decision": "approved", "note": "Synthetic original statement reviewed",
        "destination_financial_id": bank, "settlement_reference": movement,
        "expected_financial_source": "operational", "expected_review_revision": 1,
        "expected_session_scope": digest(["owner", "owner"])}


async def financial_snapshot(db):
    names = (STATES, "store_delivery_assignments", "store_delivery_collections", "store_delivery_payment_reviews",
             "store_delivery_receipts", "store_delivery_driver_earnings", "unified_orders", EVENTS)
    return {name: await db[name].find({}).sort("_id", 1).to_list(None) for name in names}


@pytest.mark.asyncio
@pytest.mark.parametrize("bank", [BANK, SECOND_BANK])
async def test_actual_paused_operational_intake_and_review_preserve_delivery_fees_and_native_ledger(db, bank):
    if bank != BANK:
        await prepare_review(db, bank_id=bank, suffix="2", setup=False)
    assignment = "assignment-1" if bank == BANK else "assignment-2"
    before = await financial_snapshot(db)
    accounts_before = await db.mz2_financial_accounts.find({}).to_list(None)
    async with client_for(db) as client:
        choices = await client.get("/store-delivery/payment-review/bank-accounts")
        assert choices.status_code == 200 and len(choices.json()["items"]) == 4
        context = await client.get("/store-delivery/payment-review/bank-statements/context")
        assert context.status_code == 200 and context.json()["can_upload"]
        content = statement()
        imported = await upload(client, content, bank=bank, assignment=assignment)
        assert imported.status_code == 200, imported.text
        item = imported.json()["items"][0]
        assert imported.json()["status"] == "imported" and item["bank_account_id"] == bank
        assert "content" not in imported.text and "iban" not in imported.text.lower()
        assert await financial_snapshot(db) == before
        duplicate = await upload(client, content, bank=bank, assignment=assignment)
        assert duplicate.status_code == 200 and duplicate.json()["status"] == "duplicate"
        options = await client.get(f"/store-delivery/payment-review/{assignment}/approval-options")
        assert options.status_code == 200, options.text
        assert options.json()["can_approve"] and options.json()["bank_movements"][0]["id"] == item["id"]
        payload = decision(item["id"], bank=bank)
        result = await client.post(f"/store-delivery/payment-review/{assignment}", json=payload)
        assert result.status_code == 200, result.text
        replay = await client.post(f"/store-delivery/payment-review/{assignment}", json=payload)
        assert replay.status_code == 200 and replay.json() == result.json()
    state = await db[STATES].find_one({"_id": "owner"})
    assert len(state["movements"]) == 1
    assert state["movements"][0]["bank_id"] == bank and state["movements"][0]["amount"] == "200.00"
    assert state["engine"]["obligations"] == before[STATES][0]["engine"]["obligations"]
    assert await db.store_delivery_driver_earnings.find({}).to_list(None) == before["store_delivery_driver_earnings"]
    assert await db.mz2_financial_accounts.find({}).to_list(None) == accounts_before
    control = await db.mz2_atomic_owners.find_one({"_id": "owner"})
    assert control["writes_paused"] is True and control["ledger_backend_state"] == "legacy_active"
    assert (await db[MOVEMENTS].find_one({"id": item["id"]}))["status"] == "operational_consumed"
    assert (await db.store_delivery_assignments.find_one({"id": assignment}))["status"] == "delivered"
    assert all([await db[name].count_documents({}) == 0 for name in NATIVE])


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["amount", "cutover_day", "old_date", "future", "outgoing", "bytes", "reference",
                                  "row_id", "foreign_owner", "disabled_bank", "native_phase"])
async def test_invalid_or_changed_bank_proof_never_changes_money_or_delivery(db, case):
    changes = {"amount": {"amount": "199.99"}, "cutover_day": {"day": "2026-10-05"},
               "old_date": {"day": "2026-10-04"}, "future": {"day": "2099-01-01"},
               "outgoing": {"direction": "out"}}.get(case, {})
    async with client_for(db) as client:
        imported = await upload(client, statement(**changes))
        assert imported.status_code == 200, imported.text
        item = imported.json()["items"][0]
        if case == "bytes": await db[FILES].update_one({}, {"$set": {"content": b"not the original"}})
        if case == "reference": await db[MOVEMENTS].update_one({}, {"$set": {"reference": "CHANGED"}})
        if case == "row_id": await db[MOVEMENTS].update_one({}, {"$set": {"row_no": 3}})
        if case == "foreign_owner": await db[FILES].update_one({}, {"$set": {"user_id": "foreign"}})
        if case == "disabled_bank": await db.accounts.update_one({"id": "legacy-1"}, {"$set": {"status": "inactive"}})
        if case == "native_phase": await db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"ledger_backend_state": "v2_active"}})
        before = await financial_snapshot(db)
        refused = await client.post("/store-delivery/payment-review/assignment-1", json=decision(item["id"]))
        assert refused.status_code == 409, refused.text
        assert await financial_snapshot(db) == before
        assert (await db[MOVEMENTS].find_one({"id": item["id"]}))["status"] != "operational_consumed"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["foreign_bank", "wrong_bank", "currency", "file_currency", "wrong_scope", "wrong_period", "phase", "permission"])
async def test_intake_rejects_wrong_scope_identity_currency_phase_or_permission_without_evidence(db, case):
    data, content = {}, statement()
    if case == "foreign_bank": await db.accounts.update_one({"id": "legacy-1"}, {"$set": {"user_id": "foreign"}})
    if case == "wrong_bank": data["bank"] = SECOND_BANK
    if case == "currency": await db.accounts.update_one({"id": "legacy-1"}, {"$set": {"currency": "USD"}})
    if case == "file_currency": content = statement(currency="USD")
    if case == "wrong_scope": data["expected_session_scope"] = "f" * 64
    if case == "wrong_period": data["expected_started_at"] = "2026-10-06T09:00:00+00:00"
    if case == "phase": await db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"ledger_backend_state": "v2_active"}})
    if case == "permission": await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
    before = await financial_snapshot(db)
    control = await db.mz2_atomic_owners.find_one({"_id": "owner"})
    async with client_for(db) as client:
        refused = await upload(client, content, **data)
    assert refused.status_code in {403, 409, 422}, refused.text
    assert await financial_snapshot(db) == before
    assert await db.mz2_atomic_owners.find_one({"_id": "owner"}) == control
    assert all([await db[name].count_documents({}) == 0 for name in (FILES, MOVEMENTS, IMPORTS)])


@pytest.mark.asyncio
async def test_same_original_cannot_be_rebound_to_another_bank_and_retries_do_not_duplicate_rows(db):
    await prepare_review(db, bank_id=SECOND_BANK, suffix="2", setup=False)
    content = statement()
    async with client_for(db) as client:
        results = await asyncio.gather(*[upload(client, content, request_id=f"synthetic-concurrent-{number}") for number in range(3)])
        assert all(result.status_code == 200 for result in results), [result.text for result in results]
        assert sorted(result.json()["status"] for result in results) == ["duplicate", "duplicate", "imported"]
        refused = await upload(client, content, bank=SECOND_BANK, assignment="assignment-2", request_id="synthetic-cross-bank")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "operational_driver_bank_file_context_conflict"
    assert await db[FILES].count_documents({}) == await db[MOVEMENTS].count_documents({}) == 1
    assert await db[STATES].find_one({"_id": "owner", "movements": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("first_namespace", ["operational", "native"])
@pytest.mark.parametrize("reference", [None, "SHARED-ORIGINAL-REFERENCE"])
async def test_original_row_is_consumed_once_across_native_and_operational_namespaces(db, first_namespace, reference):
    # Native evidence may arrive after operational intake. This test invokes the
    # real evidence importer as an isolated fixture, not the paused HTTP writer;
    # it creates original source rows only and leaves phase/pause unchanged.
    from accounting_daily_movements import import_daily_movement_file, parse_daily_movement_xlsx
    await db.mz2_financial_accounts.insert_one({"user_id": "owner", "id": "native-bank", "name": "Native identity",
        "account_type": "bank", "currency": "SAR", "status": "active"})
    await prepare_review(db, bank_id="native-bank", suffix="2", setup=False)
    content = statement(reference=reference)
    async with client_for(db) as client:
        original = await upload(client, content)
        assert original.status_code == 200, original.text
        operational_id = original.json()["items"][0]["id"]
        native = await import_daily_movement_file(db, owner="owner", actor=ACTOR,
            bank_account_id="native-bank", filename="same-original.xlsx", content=content,
            parsed=parse_daily_movement_xlsx(content))
        native_id = native["items"][0]["id"]
        assert operational_id != native_id
        canonical_options = await client.get("/store-delivery/payment-review/assignment-2/approval-options")
        assert canonical_options.status_code == 200, canonical_options.text
        assert canonical_options.json()["bank_movements"][0]["id"] == native_id
        assert canonical_options.json()["bank_movements"][0]["source_namespace"] == "mz2_daily_movements"
        attempts = [("assignment-1", decision(operational_id)),
                    ("assignment-2", decision(native_id, bank="native-bank"))]
        if first_namespace == "native": attempts.reverse()
        first = await client.post("/store-delivery/payment-review/" + attempts[0][0], json=attempts[0][1])
        assert first.status_code == 200, first.text
        before = await financial_snapshot(db)
        duplicate = await client.post("/store-delivery/payment-review/" + attempts[1][0], json=attempts[1][1])
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"]["code"] == "driver_payment_reference_already_consumed"
        assert await financial_snapshot(db) == before
    assert len((await db[STATES].find_one({"_id": "owner"}))["movements"]) == 1
    assert (await db.mz2_atomic_owners.find_one({"_id": "owner"}))["writes_paused"] is True
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("first_namespace", ["operational", "native"])
@pytest.mark.parametrize("case", ["reference_metadata", "unreferenced_metadata", "unreferenced_moved_row"])
@pytest.mark.parametrize("historical_claim_only", [False, True])
async def test_resaved_statement_cannot_repeat_a_bank_fact_across_namespaces_or_old_claims(
        db, first_namespace, case, historical_claim_only):
    from accounting_daily_movements import import_daily_movement_file, parse_daily_movement_xlsx
    bank = "canonical-bank"
    await db.mz2_financial_accounts.insert_one({"user_id": "owner", "id": bank, "name": "Synthetic shared identity",
        "account_type": "bank", "currency": "SAR", "status": "active"})
    for name in ("store_delivery_collections", "store_delivery_payment_reviews"):
        await db[name].update_one({"assignment_id": "assignment-1"}, {"$set": {
            "bank_account_id": bank, "bank_identity_source": "mz2_financial_accounts"}})
    await prepare_review(db, bank_id=bank, suffix="2", setup=False)
    original = statement(reference="SYNTHETIC-SAME-BANK-REFERENCE" if case == "reference_metadata" else None)
    book = load_workbook(BytesIO(original))
    book.properties.title = "Same bank facts, another XLSX container"
    if case == "unreferenced_moved_row":
        book.active.insert_rows(2)
    output = BytesIO(); book.save(output); resaved = output.getvalue()
    assert original != resaved
    facts = [parse_daily_movement_xlsx(content)["rows"][0] for content in (original, resaved)]
    assert {k: v for k, v in facts[0].items() if k not in {"row_no", "row_hash"}} == {
        k: v for k, v in facts[1].items() if k not in {"row_no", "row_hash"}}
    if case == "unreferenced_moved_row":
        assert facts[0]["row_no"] != facts[1]["row_no"] and facts[0]["row_hash"] != facts[1]["row_hash"]
    else:
        assert facts[0] == facts[1]

    async with client_for(db) as client:
        async def ingest(namespace, content, assignment):
            if namespace == "operational":
                response = await upload(client, content, bank=bank, assignment=assignment,
                    request_id="resaved-bank-facts-" + assignment)
                assert response.status_code == 200, response.text
                return response.json()["items"][0]["id"], MOVEMENTS
            imported = await import_daily_movement_file(db, owner="owner", actor=ACTOR,
                bank_account_id=bank, filename="resaved.xlsx", content=content,
                parsed=parse_daily_movement_xlsx(content))
            return imported["items"][0]["id"], "mz2_daily_movements"

        first_id, first_source = await ingest(first_namespace, original, "assignment-1")
        first = await client.post("/store-delivery/payment-review/assignment-1", json=decision(first_id, bank=bank))
        assert first.status_code == 200, first.text
        if historical_claim_only:
            # Simulate an approval before shared bank-fact claims existed. The
            # legacy source claim must still block a new namespace even if the
            # source row's mutable status was subsequently reset.
            old_claim = digest(["owner", "operational-driver-review-claim", "reference", [first_source, first_id]])
            await db[EVENTS].delete_many({"user_id": "owner", "kind": "claim", "_id": {"$ne": old_claim}})
            await db[first_source].update_one({"user_id": "owner", "id": first_id}, {"$set": {"status": "unclassified"},
                "$unset": {"operational_event_id": "", "operational_consumed_at": ""}})
        second_id, _ = await ingest("native" if first_namespace == "operational" else "operational",
            resaved, "assignment-2")
        before = await financial_snapshot(db)
        duplicate = await client.post("/store-delivery/payment-review/assignment-2", json=decision(second_id, bank=bank))
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"]["code"] in {
            "driver_payment_reference_already_consumed", "operational_driver_bank_fact_reconciliation_required"}
        if case != "reference_metadata":
            assert duplicate.json()["detail"]["code"] == "operational_driver_bank_fact_reconciliation_required"
            assert "message" in duplicate.json()["detail"]
        assert await financial_snapshot(db) == before
    assert len((await db[STATES].find_one({"_id": "owner"}))["movements"]) == 1
    assert (await db.mz2_atomic_owners.find_one({"_id": "owner"}))["writes_paused"] is True
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_existing_native_original_is_not_imported_again_as_operational(db):
    from accounting_daily_movements import import_daily_movement_file, parse_daily_movement_xlsx
    await db.mz2_financial_accounts.insert_one({"user_id": "owner", "id": "native-bank", "name": "Native identity",
        "account_type": "bank", "currency": "SAR", "status": "active"})
    content = statement()
    await import_daily_movement_file(db, owner="owner", actor=ACTOR, bank_account_id="native-bank",
        filename="native-original.xlsx", content=content, parsed=parse_daily_movement_xlsx(content))
    async with client_for(db) as client:
        refused = await upload(client, content)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "operational_driver_bank_existing_native_statement"
    assert await db[FILES].count_documents({}) == 0
    assert await db[MOVEMENTS].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [False, True])
async def test_changed_file_cannot_reuse_an_import_request_or_bank_reference(db, same_request):
    async with client_for(db) as client:
        result = await upload(client, statement())
        assert result.status_code == 200, result.text
        refused = await upload(client, statement(amount="201.00"),
            request_id="synthetic-upload-1" if same_request else "synthetic-conflicting-reference")
    assert refused.status_code == 409, refused.text
    expected = "operational_driver_bank_import_idempotency_conflict" if same_request else "operational_driver_bank_reference_conflict"
    assert refused.json()["detail"]["code"] == expected
    assert await db[FILES].count_documents({}) == await db[MOVEMENTS].count_documents({}) == 1
    assert await db[STATES].find_one({"_id": "owner", "movements": []})


@pytest.mark.asyncio
async def test_malformed_xlsx_returns_a_specific_error_without_server_failure_or_stored_evidence(db):
    from zipfile import ZipFile
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", b"not XML")
        archive.writestr("xl/workbook.xml", b"not XML")
    async with client_for(db) as client:
        response = await upload(client, output.getvalue())
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "operational_driver_bank_file_invalid"
    assert all([await db[name].count_documents({}) == 0 for name in (FILES, MOVEMENTS, IMPORTS)])


@pytest.mark.asyncio
async def test_upload_permission_is_independent_of_review_and_native_activation(db):
    accountant = {"id": "accountant", "role": "accountant", "created_by": "owner"}
    async with client_for(db, accountant) as client:
        response = await client.get("/store-delivery/payment-review/bank-statements/context")
        assert response.status_code == 200 and response.json()["can_upload"] is False
        denied = await upload(client, statement(), expected_session_scope=digest(["owner", "accountant"]))
        assert denied.status_code == 403 and denied.json()["detail"]["permission"] == "accounting.movements.import"
        await db.users.update_one({"id": "accountant"}, {"$set": {"accounting_permissions": ["accounting.movements.import"]}})
        allowed = await upload(client, statement(), expected_session_scope=digest(["owner", "accountant"]))
        assert allowed.status_code == 200, allowed.text
    assert (await db.mz2_atomic_owners.find_one({"_id": "owner"}))["writes_paused"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["journal", "bank_create", "balance", "receipt", "foreign_read", "owner_control"])
async def test_bank_intake_capability_cannot_escape_into_accounts_or_money(db, case):
    from operational_driver_bank_store import transact
    from operational_driver_bank_statements import authorize_import
    before = await financial_snapshot(db)
    control = await db.mz2_atomic_owners.find_one({"_id": "owner"})
    async def attempt(scoped):
        try:
            if case == "journal": await scoped.accounting_general_ledger_v2.insert_one({"user_id": "owner"})
            elif case == "bank_create": await scoped.mz2_financial_accounts.insert_one({"user_id": "owner", "id": "forbidden"})
            elif case == "balance": await scoped[STATES].update_one({"_id": "owner"}, {"$set": {"movements": []}})
            elif case == "receipt": await scoped.store_delivery_collections.update_one({"user_id": "owner"}, {"$set": {"payment_confirmed": True}})
            elif case == "foreign_read": await scoped[FILES].find_one({"user_id": "foreign"})
            else: await scoped.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"writes_paused": False}})
        except HTTPException:
            return "caught-cannot-commit"
    with pytest.raises(HTTPException) as error:
        await transact(db, "owner", attempt, lambda scoped: authorize_import(scoped, "owner", "owner"))
    assert error.value.detail["code"] == "operational_driver_bank_capability_denied"
    assert await financial_snapshot(db) == before
    assert await db.mz2_atomic_owners.find_one({"_id": "owner"}) == control
    assert await db.mz2_financial_accounts.count_documents({}) == 1


async def reject_and_upload_replacement(db):
    await db.users.insert_one(dict(DRIVER))
    await db.store_drivers.update_one({"id": "driver", "user_id": "owner"}, {"$set": {"account_user_id": DRIVER["id"]}})
    async with client_for(db) as client:
        rejected = await client.post("/store-delivery/payment-review/assignment-1", json={
            "decision": "rejected", "note": "Synthetic unreadable receipt", "expected_financial_source": "operational",
            "expected_review_revision": 1, "expected_session_scope": digest(["owner", "owner"])})
        assert rejected.status_code == 200, rejected.text
    async with client_for(db, DRIVER) as client:
        received = await client.post("/store-delivery/evidence/receipt", data={"assignment_id": "assignment-1"},
            files={"file": ("corrected.png", PNG, "image/png")})
        assert received.status_code == 200, received.text
    return {"receipt_reference": received.json()["receipt_reference"], "bank_account_id": SECOND_BANK}


@pytest.mark.asyncio
async def test_rejected_operational_receipt_can_be_replaced_atomically_and_retried_after_bank_change(db):
    payload = await reject_and_upload_replacement(db)
    before = await financial_snapshot(db)
    async with client_for(db, DRIVER) as client:
        result = await client.post("/store-delivery/app/payment-review/assignment-1/resubmit", json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "pending" and result.json()["revision"] == 2
        assert result.json()["bank_account_id"] == SECOND_BANK
        assert result.json()["bank_identity_source"] == "mz2_operational_bank_accounts"
        after = await financial_snapshot(db)
        await db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"ledger_backend_state": "v2_active"}})
        await db.mz2_operational_bank_accounts.update_one({"id": "operational-1"}, {"$set": {"status": "inactive"}})
        replay = await client.post("/store-delivery/app/payment-review/assignment-1/resubmit", json=payload)
        assert replay.status_code == 200 and replay.json() == result.json()
        assert await financial_snapshot(db) == after
    assert before[STATES] == after[STATES]
    assert before["store_delivery_driver_earnings"] == after["store_delivery_driver_earnings"]
    assert (await db.store_delivery_assignments.find_one({"id": "assignment-1"}))["status"] == "delivered"
    assert (await db.store_delivery_collections.find_one({"assignment_id": "assignment-1"}))["payment_confirmed"] is False
    assert (await db.store_delivery_receipts.find_one({"token": "token-1"}))["status"] == "superseded"


@pytest.mark.asyncio
async def test_resubmission_failure_rolls_back_the_whole_evidence_update(db, monkeypatch):
    from operational_driver_review_store import ReviewDatabase
    payload = await reject_and_upload_replacement(db)
    original = ReviewDatabase.__getitem__
    class RefuseLastUpdate:
        def __init__(self, collection): self.collection = collection
        def __getattr__(self, name): return getattr(self.collection, name)
        async def update_one(self, *args, **kwargs): raise HTTPException(409, detail={"code": "synthetic_receipt_write_failure"})
    def inject(self, name):
        collection = original(self, name)
        return RefuseLastUpdate(collection) if name == "store_delivery_receipts" else collection
    monkeypatch.setattr(ReviewDatabase, "__getitem__", inject)
    before = await financial_snapshot(db)
    control = await db.mz2_atomic_owners.find_one({"_id": "owner"})
    async with client_for(db, DRIVER) as client:
        result = await client.post("/store-delivery/app/payment-review/assignment-1/resubmit", json=payload)
    assert result.status_code == 409 and result.json()["detail"]["code"] == "synthetic_receipt_write_failure"
    assert await financial_snapshot(db) == before
    assert await db.mz2_atomic_owners.find_one({"_id": "owner"}) == control
