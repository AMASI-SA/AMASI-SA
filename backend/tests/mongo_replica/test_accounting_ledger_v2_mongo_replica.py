"""Real MongoDB replica-set contracts for the sealed Accounting V2 core.

These tests refuse non-loopback hosts.  They exist to prove Mongo transaction,
index, BSON-width, aggregation, conflict, and retry behavior without ever
connecting to a deployment database.
"""
from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from bson.int64 import Int64
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring
from pymongo.errors import (
    ConfigurationError,
    DuplicateKeyError,
    InvalidOperation,
    PyMongoError,
)
from pymongo.read_concern import ReadConcern
from pymongo.uri_parser import parse_uri
from pymongo.write_concern import WriteConcern

from accounting_ledger_v2 import (
    AUDIT_COLLECTION,
    GENERAL_LEDGER_COLLECTION,
    GROUPS_COLLECTION,
    OPERATION_ID,
    SEQUENCES_COLLECTION,
    AccountingLedgerV2Error,
    aggregate_balances_v2,
    assert_no_mz2_rows_in_legacy_ledger,
    ensure_accounting_ledger_v2_indexes,
    post_journal_v2,
    post_opening_journal_v2,
    scan_mz2_rows_in_legacy_ledger,
    verify_journal_v2,
)


pytestmark = [pytest.mark.mongo_replica, pytest.mark.asyncio]

MONGO_URI_ENV = "MZ2_MONGO_REPLICA_URI"
APP_NAME = "MZ2AccountingReplicaContract"
TENANT = "mongo-replica-tenant"
OPENING_AT = "2026-01-01T00:00:00+03:00"
OPERATIONAL_AT = "2026-01-02T00:00:00+03:00"
_TXN_OPTIONS = {
    "read_concern": ReadConcern("snapshot"),
    "write_concern": WriteConcern("majority"),
    "max_commit_time_ms": 10_000,
}
_V2_COLLECTIONS = (
    GROUPS_COLLECTION,
    GENERAL_LEDGER_COLLECTION,
    AUDIT_COLLECTION,
    SEQUENCES_COLLECTION,
)
_EXPECTED_INDEXES = {
    GROUPS_COLLECTION: {
        "uq_accounting_v2_group_idempotency": {
            "key": [("user_id", 1), ("operation_id", 1), ("idempotency_key", 1)],
            "unique": True,
        },
        "uq_accounting_v2_group_reversal": {
            "key": [
                ("user_id", 1),
                ("operation_id", 1),
                ("reversal_of_txn_group_id", 1),
            ],
            "unique": True,
            "partialFilterExpression": {
                "reversal_of_txn_group_id": {"$type": "string"}
            },
        },
        "uq_accounting_v2_opening": {
            "key": [("user_id", 1), ("operation_id", 1), ("txn_type", 1)],
            "unique": True,
            "partialFilterExpression": {"txn_type": "opening_balance"},
        },
        "ix_accounting_v2_groups_effective": {
            "key": [("user_id", 1), ("operation_id", 1), ("effective_at", -1)]
        },
    },
    GENERAL_LEDGER_COLLECTION: {
        "uq_accounting_v2_leg_number": {
            "key": [
                ("user_id", 1),
                ("operation_id", 1),
                ("txn_group_id", 1),
                ("leg_no", 1),
            ],
            "unique": True,
        },
        "uq_accounting_v2_leg_key": {
            "key": [
                ("user_id", 1),
                ("operation_id", 1),
                ("txn_group_id", 1),
                ("leg_key", 1),
            ],
            "unique": True,
        },
        "uq_accounting_v2_entry_number": {
            "key": [("user_id", 1), ("operation_id", 1), ("entry_no", 1)],
            "unique": True,
        },
        "ix_accounting_v2_entity_balance": {
            "key": [
                ("user_id", 1),
                ("operation_id", 1),
                ("entity_type", 1),
                ("entity_id", 1),
                ("sub_account", 1),
                ("effective_at", 1),
                ("status", 1),
            ]
        },
        "ix_accounting_v2_reporting": {
            "key": [
                ("user_id", 1),
                ("operation_id", 1),
                ("effective_at", 1),
                ("entry_type", 1),
                ("entity_type", 1),
            ]
        },
    },
    AUDIT_COLLECTION: {
        "uq_accounting_v2_audit_id": {
            "key": [("user_id", 1), ("operation_id", 1), ("id", 1)],
            "unique": True,
        },
        "ix_accounting_v2_audit_group": {
            "key": [
                ("user_id", 1),
                ("operation_id", 1),
                ("txn_group_id", 1),
                ("recorded_at", 1),
            ]
        },
    },
    SEQUENCES_COLLECTION: {
        "uq_accounting_v2_sequence": {
            "key": [("user_id", 1), ("operation_id", 1)],
            "unique": True,
        }
    },
}


class _CommandRecorder(monitoring.CommandListener):
    def __init__(self) -> None:
        self.started_names: list[str] = []

    def reset(self) -> None:
        self.started_names.clear()

    def started(self, event) -> None:
        self.started_names.append(event.command_name)

    def succeeded(self, event) -> None:
        pass

    def failed(self, event) -> None:
        pass


def _loopback_uri() -> str:
    uri = os.environ.get(MONGO_URI_ENV)
    if not uri:
        pytest.skip(f"{MONGO_URI_ENV} is not configured")
    if not uri.startswith("mongodb://"):
        pytest.fail("Mongo replica contract requires a non-SRV mongodb:// URI")
    try:
        parsed = parse_uri(uri)
    except ConfigurationError:
        pytest.fail("Mongo replica contract URI is invalid")
    if parsed["username"] is not None or parsed["password"] is not None:
        pytest.fail("Mongo replica contract refuses credentials")
    if parsed["database"] is not None or parsed["collection"] is not None:
        pytest.fail("Mongo replica contract refuses a database path")
    nodes = parsed["nodelist"]
    if len(nodes) != 1:
        pytest.fail("Mongo replica contract requires exactly one seed")
    host, port = nodes[0]
    if host.lower() not in {"127.0.0.1", "localhost", "::1"} or port != 27017:
        pytest.fail("Mongo replica contract requires loopback port 27017")
    options = dict(parsed["options"])
    if set(options) != {"replicaset", "directconnection"}:
        pytest.fail("Mongo replica contract refuses unapproved URI options")
    if options.get("replicaset") != "rs0" or options.get("directconnection") is not True:
        pytest.fail(
            "Mongo replica contract requires replicaSet=rs0 and directConnection=true"
        )
    return uri


async def test_uri_guard_accepts_only_the_dedicated_replica_contract(monkeypatch):
    expected = "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true"
    monkeypatch.setenv(
        "MONGO_URL",
        "mongodb://production.example.com:27017/?replicaSet=production",
    )
    monkeypatch.setenv(MONGO_URI_ENV, expected)
    assert _loopback_uri() == expected


async def test_uri_guard_never_falls_back_to_application_mongo_url(monkeypatch):
    monkeypatch.delenv(MONGO_URI_ENV, raising=False)
    monkeypatch.setenv(
        "MONGO_URL",
        "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true",
    )
    with pytest.raises(pytest.skip.Exception):
        _loopback_uri()


@pytest.mark.parametrize(
    "unsafe_uri",
    [
        "mongodb+srv://cluster.example.com/?replicaSet=rs0&directConnection=true",
        "mongodb://user:secret@127.0.0.1:27017/?replicaSet=rs0&directConnection=true",
        "mongodb://127.0.0.1:27017/test?replicaSet=rs0&directConnection=true",
        "mongodb://127.0.0.1:27017,localhost:27017/?replicaSet=rs0",
        "mongodb://10.0.0.2:27017/?replicaSet=rs0&directConnection=true",
        "mongodb://127.0.0.1:27018/?replicaSet=rs0&directConnection=true",
        "mongodb://127.0.0.1:27017/?replicaSet=rs0",
        "mongodb://127.0.0.1:27017/?replicaSet=production&directConnection=true",
        "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=false",
        "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true&tls=false",
    ],
)
async def test_uri_guard_rejects_non_ci_topologies(monkeypatch, unsafe_uri):
    monkeypatch.setenv(MONGO_URI_ENV, unsafe_uri)
    with pytest.raises(pytest.fail.Exception):
        _loopback_uri()


@pytest_asyncio.fixture
async def replica_db():
    recorder = _CommandRecorder()
    client = AsyncIOMotorClient(
        _loopback_uri(),
        appname=APP_NAME,
        event_listeners=[recorder],
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=5_000,
        socketTimeoutMS=15_000,
        uuidRepresentation="standard",
    )
    database_name = f"mz2_accounting_v2_ci_replica_{uuid4().hex}"
    db = client[database_name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") == "rs0"
        assert hello.get("isWritablePrimary") is True
        await ensure_accounting_ledger_v2_indexes(db)
        yield client, db, recorder
    finally:
        with suppress(PyMongoError):
            await client.admin.command(
                {"configureFailPoint": "failCommand", "mode": "off"}
            )
        with suppress(PyMongoError):
            await client.drop_database(database_name)
        client.close()


def _opening_entries(amount: str = "12.34") -> list[dict]:
    return [
        {
            "leg_key": "opening-bank",
            "entity_type": "bank",
            "entity_id": "bank-main",
            "entry_type": "opening_balance",
            "amount": amount,
            "side": "debit",
            "metadata": {"evidence_id": "evidence-bank"},
        },
        {
            "leg_key": "opening-equity",
            "entity_type": "equity",
            "entity_id": "opening-equity",
            "entry_type": "opening_balance",
            "amount": amount,
            "side": "credit",
            "metadata": {"evidence_id": "evidence-equity"},
        },
    ]


def _operational_entries(key: str, amount: str = "1.25") -> list[dict]:
    return [
        {
            "leg_key": f"{key}-receivable",
            "entity_type": "receivable",
            "entity_id": f"customer-{key}",
            "entry_type": "delivered_sale",
            "amount": amount,
            "side": "debit",
        },
        {
            "leg_key": f"{key}-revenue",
            "entity_type": "revenue",
            "entity_id": "sales",
            "entry_type": "delivered_sale",
            "amount": amount,
            "side": "credit",
        },
    ]


async def _post_opening(client, db, *, amount: str = "12.34") -> dict:
    async with await client.start_session() as session:
        async with session.start_transaction(**_TXN_OPTIONS):
            return await post_opening_journal_v2(
                db,
                user_id=TENANT,
                actor_id="owner-1",
                actor_name="Owner",
                opening_operation_id="opening-v1",
                approved_preview_hash="a" * 64,
                effective_at=OPENING_AT,
                entries=_opening_entries(amount),
                mongo_session=session,
            )


async def _post_operational(client, db, *, key: str) -> dict:
    async with await client.start_session() as session:
        async with session.start_transaction(**_TXN_OPTIONS):
            return await post_journal_v2(
                db,
                user_id=TENANT,
                actor_id="writer-1",
                actor_name="Writer",
                idempotency_key=f"operational:{key}",
                txn_type="delivered_sale",
                source="mongo_replica_contract",
                effective_at=OPERATIONAL_AT,
                entries=_operational_entries(key),
                mongo_session=session,
            )


async def _counts(db) -> dict[str, int]:
    return {
        name: await db[name].count_documents({"operation_id": OPERATION_ID})
        for name in _V2_COLLECTIONS
    }


async def _set_failpoint(client, *, mode, data: dict | None = None) -> None:
    command = {"configureFailPoint": "failCommand", "mode": mode}
    if data is not None:
        command["data"] = data
    await client.admin.command(command)


async def _assert_index_definitions(db) -> None:
    for collection_name, expected in _EXPECTED_INDEXES.items():
        actual = await db[collection_name].index_information()
        for index_name, definition in expected.items():
            assert index_name in actual
            assert list(actual[index_name]["key"]) == definition["key"]
            assert bool(actual[index_name].get("unique", False)) is bool(
                definition.get("unique", False)
            )
            if "partialFilterExpression" in definition:
                assert actual[index_name].get("partialFilterExpression") == definition[
                    "partialFilterExpression"
                ]
            else:
                assert "partialFilterExpression" not in actual[index_name]


def _assert_duplicate_from_index(failure: pytest.ExceptionInfo, index_name: str) -> None:
    assert index_name in str(failure.value)


async def _assert_unique_index_enforcement(db) -> None:
    selector = {"user_id": TENANT, "operation_id": OPERATION_ID}
    opening_group = await db[GROUPS_COLLECTION].find_one(selector)
    first_entry = await db[GENERAL_LEDGER_COLLECTION].find_one(selector)
    audit = await db[AUDIT_COLLECTION].find_one(selector)
    sequence = await db[SEQUENCES_COLLECTION].find_one(selector)
    assert opening_group and first_entry and audit and sequence

    duplicate_idempotency = deepcopy(opening_group)
    duplicate_idempotency.update(
        {
            "_id": "index-probe-group-idempotency",
            "id": "index-probe-group-idempotency",
            "txn_group_id": "index-probe-group-idempotency",
            "txn_type": "index_probe",
        }
    )
    with pytest.raises(DuplicateKeyError) as failure:
        await db[GROUPS_COLLECTION].insert_one(duplicate_idempotency)
    _assert_duplicate_from_index(failure, "uq_accounting_v2_group_idempotency")

    duplicate_opening = deepcopy(opening_group)
    duplicate_opening.update(
        {
            "_id": "index-probe-group-opening",
            "id": "index-probe-group-opening",
            "txn_group_id": "index-probe-group-opening",
            "idempotency_key": "index-probe-group-opening",
        }
    )
    with pytest.raises(DuplicateKeyError) as failure:
        await db[GROUPS_COLLECTION].insert_one(duplicate_opening)
    _assert_duplicate_from_index(failure, "uq_accounting_v2_opening")

    for suffix in ("none-a", "none-b"):
        await db[GROUPS_COLLECTION].insert_one(
            {
                "_id": f"index-probe-reversal-{suffix}",
                "user_id": TENANT,
                "operation_id": OPERATION_ID,
                "idempotency_key": f"index-probe-reversal-{suffix}",
                "txn_type": "index_probe",
                "reversal_of_txn_group_id": None,
            }
        )
    await db[GROUPS_COLLECTION].insert_one(
        {
            "_id": "index-probe-reversal-string-a",
            "user_id": TENANT,
            "operation_id": OPERATION_ID,
            "idempotency_key": "index-probe-reversal-string-a",
            "txn_type": "index_probe",
            "reversal_of_txn_group_id": "index-probe-original",
        }
    )
    with pytest.raises(DuplicateKeyError) as failure:
        await db[GROUPS_COLLECTION].insert_one(
            {
                "_id": "index-probe-reversal-string-b",
                "user_id": TENANT,
                "operation_id": OPERATION_ID,
                "idempotency_key": "index-probe-reversal-string-b",
                "txn_type": "index_probe",
                "reversal_of_txn_group_id": "index-probe-original",
            }
        )
    _assert_duplicate_from_index(failure, "uq_accounting_v2_group_reversal")

    duplicate_leg_number = deepcopy(first_entry)
    duplicate_leg_number.update(
        {
            "_id": "index-probe-leg-number",
            "id": "index-probe-leg-number",
            "leg_key": "index-probe-leg-number",
            "entry_no": Int64(10_001),
        }
    )
    with pytest.raises(DuplicateKeyError) as failure:
        await db[GENERAL_LEDGER_COLLECTION].insert_one(duplicate_leg_number)
    _assert_duplicate_from_index(failure, "uq_accounting_v2_leg_number")

    duplicate_leg_key = deepcopy(first_entry)
    duplicate_leg_key.update(
        {
            "_id": "index-probe-leg-key",
            "id": "index-probe-leg-key",
            "leg_no": 10_002,
            "entry_no": Int64(10_002),
        }
    )
    with pytest.raises(DuplicateKeyError) as failure:
        await db[GENERAL_LEDGER_COLLECTION].insert_one(duplicate_leg_key)
    _assert_duplicate_from_index(failure, "uq_accounting_v2_leg_key")

    duplicate_entry_number = deepcopy(first_entry)
    duplicate_entry_number.update(
        {
            "_id": "index-probe-entry-number",
            "id": "index-probe-entry-number",
            "txn_group_id": "index-probe-entry-number",
            "leg_no": 10_003,
            "leg_key": "index-probe-entry-number",
        }
    )
    with pytest.raises(DuplicateKeyError) as failure:
        await db[GENERAL_LEDGER_COLLECTION].insert_one(duplicate_entry_number)
    _assert_duplicate_from_index(failure, "uq_accounting_v2_entry_number")

    duplicate_audit_id = deepcopy(audit)
    duplicate_audit_id.update(
        {
            "_id": "index-probe-audit-id",
            "txn_group_id": "index-probe-audit-id",
        }
    )
    with pytest.raises(DuplicateKeyError) as failure:
        await db[AUDIT_COLLECTION].insert_one(duplicate_audit_id)
    _assert_duplicate_from_index(failure, "uq_accounting_v2_audit_id")

    duplicate_sequence = deepcopy(sequence)
    duplicate_sequence["_id"] = "index-probe-sequence"
    with pytest.raises(DuplicateKeyError) as failure:
        await db[SEQUENCES_COLLECTION].insert_one(duplicate_sequence)
    _assert_duplicate_from_index(failure, "uq_accounting_v2_sequence")


async def test_server_indexes_atomic_commit_bson_width_and_aggregation(replica_db):
    client, db, _recorder = replica_db
    await _assert_index_definitions(db)

    async with await client.start_session() as session:
        session.start_transaction(**_TXN_OPTIONS)
        opening = await post_opening_journal_v2(
            db,
            user_id=TENANT,
            actor_id="owner-1",
            actor_name="Owner",
            opening_operation_id="opening-v1",
            approved_preview_hash="a" * 64,
            effective_at=OPENING_AT,
            entries=_opening_entries(),
            mongo_session=session,
        )
        # A reader outside the transaction must see none of the four writes.
        assert await _counts(db) == dict.fromkeys(_V2_COLLECTIONS, 0)
        await session.commit_transaction()

    assert await _counts(db) == {
        GROUPS_COLLECTION: 1,
        GENERAL_LEDGER_COLLECTION: 2,
        AUDIT_COLLECTION: 1,
        SEQUENCES_COLLECTION: 1,
    }
    verification = await verify_journal_v2(
        db,
        user_id=TENANT,
        txn_group_id=opening["group"]["txn_group_id"],
    )
    assert verification["verified"] is True

    entries = await db[GENERAL_LEDGER_COLLECTION].find(
        {"user_id": TENANT, "operation_id": OPERATION_ID}
    ).sort("entry_no", 1).to_list(10)
    sequence = await db[SEQUENCES_COLLECTION].find_one(
        {"user_id": TENANT, "operation_id": OPERATION_ID}
    )
    assert all(isinstance(entry["amount_minor"], Int64) for entry in entries)
    assert all(isinstance(entry["entry_no"], Int64) for entry in entries)
    assert isinstance(sequence["last_entry_no"], Int64)

    balances = await aggregate_balances_v2(
        db,
        user_id=TENANT,
        group_by=("entity_type",),
    )
    by_type = {row["group"]["entity_type"]: row for row in balances}
    assert by_type["bank"]["debit_total_minor"] == 1234
    assert by_type["bank"]["credit_total_minor"] == 0
    assert by_type["equity"]["debit_total_minor"] == 0
    assert by_type["equity"]["credit_total_minor"] == 1234
    await _assert_unique_index_enforcement(db)


async def test_late_audit_failure_rolls_back_sequence_group_and_legs(replica_db):
    client, db, _recorder = replica_db
    await db.command(
        {
            "collMod": AUDIT_COLLECTION,
            "validator": {"_replica_contract_allows_audit": True},
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
    try:
        with pytest.raises(PyMongoError) as failure:
            await _post_opening(client, db)
        assert getattr(failure.value, "code", None) == 121
    finally:
        await db.command(
            {
                "collMod": AUDIT_COLLECTION,
                "validator": {},
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    assert await _counts(db) == dict.fromkeys(_V2_COLLECTIONS, 0)

    await _post_opening(client, db)
    entries = await db[GENERAL_LEDGER_COLLECTION].find(
        {"user_id": TENANT, "operation_id": OPERATION_ID}
    ).sort("entry_no", 1).to_list(10)
    assert [int(entry["entry_no"]) for entry in entries] == [1, 2]


async def test_idempotent_replay_and_conflicting_payload_are_server_atomic(replica_db):
    client, db, _recorder = replica_db
    first = await _post_opening(client, db)
    replay = await _post_opening(client, db)
    assert replay["group"]["txn_group_id"] == first["group"]["txn_group_id"]

    with pytest.raises(AccountingLedgerV2Error) as conflict:
        await _post_opening(client, db, amount="13.00")
    assert conflict.value.code == "accounting_v2_idempotency_conflict"
    assert await _counts(db) == {
        GROUPS_COLLECTION: 1,
        GENERAL_LEDGER_COLLECTION: 2,
        AUDIT_COLLECTION: 1,
        SEQUENCES_COLLECTION: 1,
    }


async def test_overlapping_transactions_surface_write_conflict_and_retry_sequence(replica_db):
    client, db, _recorder = replica_db
    await _post_opening(client, db)
    selector = {"user_id": TENANT, "operation_id": OPERATION_ID}

    async with await client.start_session() as first_session:
        async with await client.start_session() as second_session:
            first_session.start_transaction(**_TXN_OPTIONS)
            second_session.start_transaction(**_TXN_OPTIONS)
            try:
                first_snapshot = await db[SEQUENCES_COLLECTION].find_one(
                    selector, session=first_session
                )
                second_snapshot = await db[SEQUENCES_COLLECTION].find_one(
                    selector, session=second_session
                )
                assert first_snapshot["last_entry_no"] == 2
                assert second_snapshot["last_entry_no"] == 2

                first = await post_journal_v2(
                    db,
                    user_id=TENANT,
                    actor_id="writer-a",
                    actor_name="Writer A",
                    idempotency_key="operational:concurrent-a",
                    txn_type="delivered_sale",
                    source="mongo_replica_contract",
                    effective_at=OPERATIONAL_AT,
                    entries=_operational_entries("concurrent-a"),
                    mongo_session=first_session,
                )
                with pytest.raises(PyMongoError) as write_conflict:
                    await post_journal_v2(
                        db,
                        user_id=TENANT,
                        actor_id="writer-b",
                        actor_name="Writer B",
                        idempotency_key="operational:concurrent-b",
                        txn_type="delivered_sale",
                        source="mongo_replica_contract",
                        effective_at=OPERATIONAL_AT,
                        entries=_operational_entries("concurrent-b"),
                        mongo_session=second_session,
                    )
                assert getattr(write_conflict.value, "code", None) == 112
                assert write_conflict.value.has_error_label(
                    "TransientTransactionError"
                )
                await first_session.commit_transaction()
                assert first["group"]["txn_group_id"]
            finally:
                for session in (first_session, second_session):
                    if session.in_transaction:
                        with suppress(PyMongoError, InvalidOperation):
                            await session.abort_transaction()

    retried = await _post_operational(client, db, key="concurrent-b")
    assert retried["group"]["txn_group_id"]
    entry_numbers = await db[GENERAL_LEDGER_COLLECTION].distinct(
        "entry_no", {"user_id": TENANT, "operation_id": OPERATION_ID}
    )
    assert sorted(map(int, entry_numbers)) == [1, 2, 3, 4, 5, 6]
    sequence = await db[SEQUENCES_COLLECTION].find_one(selector)
    assert isinstance(sequence["last_entry_no"], Int64)
    assert sequence["last_entry_no"] == 6


async def test_unknown_commit_result_replays_without_duplicate_journal(replica_db):
    client, db, recorder = replica_db
    async with await client.start_session() as session:
        session.start_transaction(**_TXN_OPTIONS)
        opening = await post_opening_journal_v2(
            db,
            user_id=TENANT,
            actor_id="owner-1",
            actor_name="Owner",
            opening_operation_id="opening-v1",
            approved_preview_hash="a" * 64,
            effective_at=OPENING_AT,
            entries=_opening_entries(),
            mongo_session=session,
        )
        await _set_failpoint(
            client,
            mode={"times": 2},
            data={
                "failCommands": ["commitTransaction"],
                "closeConnection": True,
                "appName": APP_NAME,
            },
        )
        recorder.reset()
        try:
            with pytest.raises(PyMongoError) as unknown:
                await session.commit_transaction()
            assert unknown.value.has_error_label("UnknownTransactionCommitResult")
        finally:
            await _set_failpoint(client, mode="off")
        assert recorder.started_names.count("commitTransaction") == 2
        await session.commit_transaction()
        assert recorder.started_names.count("commitTransaction") == 3

    assert await _counts(db) == {
        GROUPS_COLLECTION: 1,
        GENERAL_LEDGER_COLLECTION: 2,
        AUDIT_COLLECTION: 1,
        SEQUENCES_COLLECTION: 1,
    }
    replay = await _post_opening(client, db)
    assert replay["group"]["txn_group_id"] == opening["group"]["txn_group_id"]
    assert recorder.started_names.count("commitTransaction") == 4
    assert await _counts(db) == {
        GROUPS_COLLECTION: 1,
        GENERAL_LEDGER_COLLECTION: 2,
        AUDIT_COLLECTION: 1,
        SEQUENCES_COLLECTION: 1,
    }
    sequence = await db[SEQUENCES_COLLECTION].find_one(
        {"user_id": TENANT, "operation_id": OPERATION_ID}
    )
    assert isinstance(sequence["last_entry_no"], Int64)
    assert sequence["last_entry_no"] == 2


async def test_legacy_mz2_rows_are_detected_but_never_aggregated_into_v2(replica_db):
    client, db, _recorder = replica_db
    await _post_opening(client, db)
    baseline = await aggregate_balances_v2(
        db,
        user_id=TENANT,
        group_by=("entity_type",),
    )

    legacy_row = {
        "_id": "legacy-mz2-counterfeit",
        "id": "legacy-mz2-counterfeit",
        "user_id": TENANT,
        "operation_id": OPERATION_ID,
        "txn_group_id": "legacy-mz2-group",
        "amount": "999999999.99",
        "amount_minor": Int64(99_999_999_999),
    }
    await db["general_ledger"].insert_one(legacy_row)

    assert await aggregate_balances_v2(
        db,
        user_id=TENANT,
        group_by=("entity_type",),
    ) == baseline
    scan = await scan_mz2_rows_in_legacy_ledger(db, user_id=TENANT)
    assert scan == {
        "clear": False,
        "count": 1,
        "sample_entry_ids": ["legacy-mz2-counterfeit"],
        "sample_txn_group_ids": ["legacy-mz2-group"],
    }

    async with await client.start_session() as session:
        session.start_transaction(**_TXN_OPTIONS)
        try:
            with pytest.raises(AccountingLedgerV2Error) as blocked:
                await assert_no_mz2_rows_in_legacy_ledger(
                    db,
                    user_id=TENANT,
                    mongo_session=session,
                )
            assert blocked.value.code == "accounting_v2_legacy_rows_detected"
            assert blocked.value.details["count"] == 1
        finally:
            await session.abort_transaction()

    assert await db["general_ledger"].find_one({"_id": legacy_row["_id"]}) == legacy_row
    assert await _counts(db) == {
        GROUPS_COLLECTION: 1,
        GENERAL_LEDGER_COLLECTION: 2,
        AUDIT_COLLECTION: 1,
        SEQUENCES_COLLECTION: 1,
    }
