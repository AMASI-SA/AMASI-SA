from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
from bson import BSON
from bson.int64 import Int64

from accounting_ledger_v2 import (
    AUDIT_COLLECTION,
    GENERAL_LEDGER_COLLECTION,
    GROUPS_COLLECTION,
    OPERATION_ID,
    SEQUENCES_COLLECTION,
    AccountingLedgerV2Error,
    aggregate_balances_v2,
    assert_no_mz2_rows_in_legacy_ledger,
    compute_balance_v2,
    ensure_accounting_ledger_v2_indexes,
    get_journal_v2,
    post_journal_v2,
    post_opening_journal_v2,
    query_entries_v2,
    reverse_journal_v2,
    scan_mz2_rows_in_legacy_ledger,
    verify_journal_v2,
)
from accounting_clean_start_guard import accounting_safe_active


class DuplicateKeyError(Exception):
    code = 11000


class UnknownCommitResult(RuntimeError):
    pass


def _nested(row: dict, key: str):
    value = row
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(row: dict, query: dict) -> bool:
    for key, wanted in query.items():
        if key == "$or":
            if not any(_matches(row, branch) for branch in wanted):
                return False
            continue
        actual = _nested(row, key)
        if isinstance(wanted, dict):
            if "$in" in wanted and actual not in wanted["$in"]:
                return False
            if "$ne" in wanted and actual == wanted["$ne"]:
                return False
            if "$gte" in wanted and (actual is None or actual < wanted["$gte"]):
                return False
            if "$gt" in wanted and (actual is None or actual <= wanted["$gt"]):
                return False
            if "$lte" in wanted and (actual is None or actual > wanted["$lte"]):
                return False
            if "$exists" in wanted and (actual is not None) is not wanted["$exists"]:
                return False
            if "$type" in wanted:
                if wanted["$type"] == "string" and not isinstance(actual, str):
                    return False
            continue
        if actual != wanted:
            return False
    return True


def _expression(row: dict, expression):
    if isinstance(expression, str) and expression.startswith("$"):
        return _nested(row, expression[1:])
    if isinstance(expression, (list, tuple)):
        return [_expression(row, value) for value in expression]
    if not isinstance(expression, dict):
        return expression
    if "$eq" in expression:
        left, right = expression["$eq"]
        return _expression(row, left) == _expression(row, right)
    if "$gt" in expression:
        left, right = expression["$gt"]
        return _expression(row, left) > _expression(row, right)
    if "$lte" in expression:
        left, right = expression["$lte"]
        return _expression(row, left) <= _expression(row, right)
    if "$in" in expression:
        value, choices = expression["$in"]
        return _expression(row, value) in _expression(row, choices)
    if "$and" in expression:
        return all(_expression(row, part) for part in expression["$and"])
    if "$type" in expression:
        value = _expression(row, expression["$type"])
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, Int64):
            return "long"
        if isinstance(value, int):
            return "int" if -(2**31) <= value < 2**31 else "long"
        if isinstance(value, str):
            return "string"
        return "missing" if value is None else type(value).__name__
    if "$convert" in expression:
        options = expression["$convert"]
        value = _expression(row, options["input"])
        if value is None:
            return options.get("onNull")
        try:
            if options["to"] == "decimal":
                return Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            return options.get("onError")
        raise AssertionError(f"unsupported fake conversion: {options['to']}")
    if "$multiply" in expression:
        values = [_expression(row, value) for value in expression["$multiply"]]
        if any(value is None for value in values):
            return None
        result = values[0]
        for value in values[1:]:
            result *= value
        return result
    if "$regexMatch" in expression:
        options = expression["$regexMatch"]
        value = _expression(row, options["input"])
        return isinstance(value, str) and re.fullmatch(options["regex"], value) is not None
    if "$cond" in expression:
        condition, yes, no = expression["$cond"]
        return _expression(row, yes if _expression(row, condition) else no)
    return {key: _expression(row, value) for key, value in expression.items()}


def _project(row: dict, projection: dict | None) -> dict:
    if not projection:
        return deepcopy(row)
    included = {key for key, enabled in projection.items() if enabled and key != "_id"}
    if included:
        result = {key: deepcopy(_nested(row, key)) for key in included if _nested(row, key) is not None}
        if projection.get("_id", 1) and "_id" in row:
            result["_id"] = deepcopy(row["_id"])
        return result
    result = deepcopy(row)
    for key, enabled in projection.items():
        if not enabled:
            result.pop(key, None)
    return result


class _Cursor:
    def __init__(self, rows: list[dict]):
        self.rows = [deepcopy(row) for row in rows]
        self._position = 0

    def sort(self, key, direction=None):
        specs = key if isinstance(key, list) else [(key, direction)]
        for field, order in reversed(specs):
            self.rows.sort(key=lambda row: _nested(row, field), reverse=order == -1)
        return self

    def limit(self, length: int):
        self.rows = self.rows[:length]
        return self

    async def to_list(self, length=None):
        rows = self.rows if length is None else self.rows[:length]
        return deepcopy(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._position >= len(self.rows):
            raise StopAsyncIteration
        value = deepcopy(self.rows[self._position])
        self._position += 1
        return value


class _Result:
    def __init__(self, *, matched=0, modified=0):
        self.matched_count = matched
        self.modified_count = modified


class _Collection:
    def __init__(self, db: "_DB", name: str):
        self.db = db
        self.name = name

    @property
    def rows(self) -> list[dict]:
        return self.db.rows.setdefault(self.name, [])

    async def create_index(self, keys, **kwargs):
        self.db.indexes.append((self.name, deepcopy(keys), deepcopy(kwargs)))
        return kwargs.get("name") or "index"

    async def find_one(self, query, projection=None, **_kwargs):
        for row in self.rows:
            if _matches(row, query):
                return _project(row, projection)
        return None

    def find(self, query, projection=None, **_kwargs):
        return _Cursor([
            _project(row, projection)
            for row in self.rows
            if _matches(row, query)
        ])

    def aggregate(self, pipeline, **_kwargs):
        def run(rows, stages):
            for stage in stages:
                if "$match" in stage:
                    rows = [row for row in rows if _matches(row, stage["$match"])]
                elif "$group" in stage:
                    spec = stage["$group"]
                    grouped = {}
                    for row in rows:
                        group_id = _expression(row, spec["_id"])
                        key = tuple((name, group_id[name]) for name in group_id)
                        result = grouped.setdefault(key, {"_id": group_id})
                        for field, accumulator in spec.items():
                            if field == "_id":
                                continue
                            value = _expression(row, accumulator["$sum"])
                            if isinstance(value, bool) or not isinstance(value, (int, float)):
                                value = 0
                            previous = result.get(field, 0)
                            updated = previous + value
                            if isinstance(previous, Int64) or isinstance(value, Int64):
                                updated = (
                                    float(updated)
                                    if abs(updated) > (2**63) - 1
                                    else Int64(updated)
                                )
                            result[field] = updated
                    rows = list(grouped.values())
                elif "$sort" in stage:
                    for field, direction in reversed(list(stage["$sort"].items())):
                        rows.sort(
                            key=lambda row: (
                                _nested(row, field) is None,
                                str(_nested(row, field) or ""),
                            ),
                            reverse=direction == -1,
                        )
                elif "$limit" in stage:
                    rows = rows[: stage["$limit"]]
                elif "$project" in stage:
                    rows = [_project(row, stage["$project"]) for row in rows]
                elif "$count" in stage:
                    rows = [{stage["$count"]: len(rows)}] if rows else []
                elif "$facet" in stage:
                    rows = [{
                        name: run(deepcopy(rows), nested)
                        for name, nested in stage["$facet"].items()
                    }]
                else:
                    raise AssertionError(f"unsupported fake aggregate stage: {stage}")
            return rows

        return _Cursor(run([deepcopy(row) for row in self.rows], pipeline))

    async def count_documents(self, query, **_kwargs):
        return sum(1 for row in self.rows if _matches(row, query))

    def _check_unique(self, doc: dict) -> None:
        for row in self.rows:
            if doc.get("_id") is not None and row.get("_id") == doc.get("_id"):
                raise DuplicateKeyError("duplicate _id")
            if self.name == GROUPS_COLLECTION:
                common = (
                    row.get("user_id") == doc.get("user_id")
                    and row.get("operation_id") == doc.get("operation_id")
                )
                if common and row.get("idempotency_key") == doc.get("idempotency_key"):
                    raise DuplicateKeyError("duplicate idempotency key")
                reverse_of = doc.get("reversal_of_txn_group_id")
                if common and reverse_of and row.get("reversal_of_txn_group_id") == reverse_of:
                    raise DuplicateKeyError("duplicate reversal")
                if (
                    common
                    and doc.get("txn_type") == "opening_balance"
                    and row.get("txn_type") == "opening_balance"
                ):
                    raise DuplicateKeyError("duplicate opening journal")
            elif self.name == GENERAL_LEDGER_COLLECTION:
                common = (
                    row.get("user_id") == doc.get("user_id")
                    and row.get("operation_id") == doc.get("operation_id")
                )
                if common and row.get("entry_no") == doc.get("entry_no"):
                    raise DuplicateKeyError("duplicate entry number")
                if (
                    common
                    and row.get("txn_group_id") == doc.get("txn_group_id")
                    and row.get("leg_no") == doc.get("leg_no")
                ):
                    raise DuplicateKeyError("duplicate leg number")
                if (
                    common
                    and row.get("txn_group_id") == doc.get("txn_group_id")
                    and row.get("leg_key") == doc.get("leg_key")
                ):
                    raise DuplicateKeyError("duplicate leg key")

    def _maybe_fail(self) -> None:
        if self.db.fail_collection != self.name:
            return
        self.db.fail_insert_count += 1
        if self.db.fail_insert_count >= self.db.fail_on_insert_number:
            raise RuntimeError("injected insert failure")

    async def insert_one(self, document, **_kwargs):
        self._check_unique(document)
        self.rows.append(deepcopy(document))
        self._maybe_fail()
        return _Result(matched=1, modified=1)

    async def insert_many(self, documents, **_kwargs):
        for document in documents:
            await self.insert_one(document, **_kwargs)
        return _Result(matched=len(documents), modified=len(documents))

    async def update_one(self, query, update, *, upsert=False, **_kwargs):
        for row in self.rows:
            if not _matches(row, query):
                continue
            for key, value in (update.get("$inc") or {}).items():
                updated = int(row.get(key) or 0) + int(value)
                row[key] = Int64(updated) if isinstance(value, Int64) else updated
            for key, value in (update.get("$set") or {}).items():
                row[key] = deepcopy(value)
            return _Result(matched=1, modified=1)
        if not upsert:
            return _Result()
        inserted = {
            key: deepcopy(value)
            for key, value in query.items()
            if not key.startswith("$") and not isinstance(value, dict)
        }
        inserted.update(deepcopy(update.get("$setOnInsert") or {}))
        for key, value in (update.get("$inc") or {}).items():
            updated = int(inserted.get(key) or 0) + int(value)
            inserted[key] = Int64(updated) if isinstance(value, Int64) else updated
        await self.insert_one(inserted, **_kwargs)
        return _Result(modified=1)


class _Session:
    def __init__(self, db: "_DB"):
        self.db = db
        self.in_transaction = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def with_transaction(self, callback):
        async with self.db.transaction_lock:
            snapshot = deepcopy(self.db.rows)
            self.in_transaction = True
            try:
                result = await callback(self)
            except Exception:
                self.db.rows.clear()
                self.db.rows.update(snapshot)
                raise
            finally:
                self.in_transaction = False
            if self.db.raise_unknown_commit_once:
                self.db.raise_unknown_commit_once = False
                raise UnknownCommitResult("commit result was lost")
            return result


class _Client:
    def __init__(self, db: "_DB"):
        self.db = db

    async def start_session(self):
        return _Session(self.db)


class _DB:
    def __init__(self, *, with_transactions=True, **seeded):
        self.rows = {name: deepcopy(rows) for name, rows in seeded.items()}
        self.indexes: list[tuple[str, object, dict]] = []
        self.collections: dict[str, _Collection] = {}
        self.transaction_lock = asyncio.Lock()
        self.client = _Client(self) if with_transactions else None
        self.fail_collection: str | None = None
        self.fail_on_insert_number = 0
        self.fail_insert_count = 0
        self.raise_unknown_commit_once = False

    def __getitem__(self, name: str):
        return self.collections.setdefault(name, _Collection(self, name))

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def _entries(amount: str = "125.50") -> list[dict]:
    return [
        {
            "leg_key": "bank-net",
            "entity_type": "bank",
            "entity_id": "bank-1",
            "sub_account": "main",
            "entry_type": "settlement",
            "amount": amount,
            "side": "debit",
            "metadata": {"role": "bank_net"},
        },
        {
            "leg_key": "provider-receivable",
            "entity_type": "payment_gateway",
            "entity_id": "tamara",
            "sub_account": "receivable",
            "entry_type": "settlement",
            "amount": amount,
            "side": "credit",
            "metadata": {"role": "provider_receivable"},
        },
    ]


async def _open(db: _DB):
    return await _atomic(
        db,
        lambda session: post_opening_journal_v2(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            actor_name="Owner",
            opening_operation_id="test-opening",
            approved_preview_hash="f" * 64,
            effective_at="2026-09-12T00:00:00Z",
            entries=[
                {
                    "leg_key": "opening-control",
                    "entity_type": "opening_control",
                    "entity_id": "clean-start",
                    "entry_type": "opening_balance",
                    "amount": "0.01",
                    "side": "debit",
                },
                {
                    "leg_key": "opening-equity",
                    "entity_type": "equity",
                    "entity_id": "opening-equity",
                    "entry_type": "opening_balance",
                    "amount": "0.01",
                    "side": "credit",
                },
            ],
            mongo_session=session,
        ),
    )


async def _atomic(db: _DB, callback):
    context = await db.client.start_session()
    async with context as session:
        return await session.with_transaction(callback)


async def _post(db: _DB, *, key="tamara:statement-1", amount="125.50"):
    await _open(db)
    return await _atomic(
        db,
        lambda session: post_journal_v2(
            db,
            user_id="owner-1",
            actor_id="accountant-1",
            actor_name="Accountant",
            idempotency_key=key,
            txn_type="provider_settlement_v2",
            source="accounting_settlement_p01",
            effective_at="2026-09-12T12:00:00+03:00",
            entries=_entries(amount),
            notes="Statement 1",
            metadata={"statement_reference": "statement-1"},
            mongo_session=session,
        ),
    )


async def _reverse(db: _DB, **kwargs):
    return await _atomic(
        db,
        lambda session: reverse_journal_v2(db, mongo_session=session, **kwargs),
    )


@pytest.mark.asyncio
async def test_post_isolated_balanced_sar_decimal_journal_and_verify():
    db = _DB()

    journal = await _post(db)

    assert journal["group"]["operation_id"] == OPERATION_ID
    assert journal["group"]["currency"] == "SAR"
    assert journal["group"]["effective_at"] == "2026-09-12T09:00:00.000000Z"
    assert journal["group"]["debit_total"] == "125.50"
    assert journal["group"]["credit_total"] == "125.50"
    assert [entry["entry_no"] for entry in journal["entries"]] == [3, 4]
    assert all(isinstance(entry["amount"], str) for entry in journal["entries"])
    assert [entry["amount_minor"] for entry in journal["entries"]] == [12550, 12550]
    assert all(isinstance(entry["amount_minor"], Int64) for entry in journal["entries"])
    encoded = BSON.encode({"amount_minor": journal["entries"][0]["amount_minor"]})
    assert isinstance(BSON(encoded).decode()["amount_minor"], Int64)
    assert isinstance(journal["group"]["debit_total_minor"], Int64)
    assert isinstance(journal["group"]["first_entry_no"], Int64)
    assert isinstance(db.rows[SEQUENCES_COLLECTION][0]["last_entry_no"], Int64)
    audit = next(
        row
        for row in db.rows[AUDIT_COLLECTION]
        if row.get("txn_group_id") == journal["group"]["txn_group_id"]
    )
    assert isinstance(audit["summary"]["debit_total_minor"], Int64)
    assert db.rows.get("general_ledger", []) == []
    assert len(db.rows[GROUPS_COLLECTION]) == 2
    assert len(db.rows[GENERAL_LEDGER_COLLECTION]) == 4
    assert len(db.rows[AUDIT_COLLECTION]) == 2

    verification = await verify_journal_v2(
        db, user_id="owner-1", txn_group_id=journal["group"]["txn_group_id"]
    )
    assert verification == {
        "verified": True,
        "errors": [],
        "txn_group_id": journal["group"]["txn_group_id"],
        "content_hash": journal["group"]["content_hash"],
        "entry_count": 2,
        "debit_total": "125.50",
        "credit_total": "125.50",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("effective_at", "entries", "code"),
    [
        ("2026-09-12T12:00:00", _entries(), "effective_at_timezone_required"),
        (
            "2026-09-12T12:00:00+03:00",
            [*_entries()[:1], {**_entries()[1], "amount": "125.49"}],
            "journal_unbalanced",
        ),
        (
            "2026-09-12T12:00:00+03:00",
            [{**_entries()[0], "amount": 125.50}, _entries()[1]],
            "amount_decimal_string_required",
        ),
        (
            "2026-09-12T12:00:00+03:00",
            [{**_entries()[0], "currency": "USD"}, _entries()[1]],
            "ledger_leg_unknown_fields",
        ),
        (
            "2026-09-12T12:00:00+03:00",
            _entries("92233720368547758.08"),
            "amount_minor_overflow",
        ),
    ],
)
async def test_validation_fails_before_any_storage_write(effective_at, entries, code):
    db = _DB()
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await post_journal_v2(
            db,
            user_id="owner-1",
            actor_id="accountant-1",
            actor_name="Accountant",
            idempotency_key="bad-input",
            txn_type="provider_settlement_v2",
            source="accounting_settlement_p01",
            effective_at=effective_at,
            entries=entries,
        )
    assert exc.value.code == code
    assert db.rows == {}


@pytest.mark.asyncio
async def test_balanced_journal_rejects_minor_unit_side_total_overflow():
    largest = "92233720368547758.07"
    entries = [
        {**_entries(largest)[0], "leg_key": "debit-1"},
        {**_entries(largest)[0], "leg_key": "debit-2"},
        {**_entries(largest)[1], "leg_key": "credit-1"},
        {**_entries(largest)[1], "leg_key": "credit-2"},
    ]
    db = _DB()
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await post_journal_v2(
            db,
            user_id="owner-1",
            actor_id="accountant-1",
            actor_name="Accountant",
            idempotency_key="overflowing-balanced-journal",
            txn_type="provider_settlement_v2",
            source="accounting_settlement_p01",
            effective_at="2026-09-12T12:00:00+03:00",
            entries=entries,
        )
    assert exc.value.code == "journal_total_minor_overflow"
    assert db.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("txn_type", ["opening_balance", "reversal"])
async def test_operational_post_cannot_bypass_sealed_reserved_type_wrappers(txn_type):
    db = _DB()
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await post_journal_v2(
            db,
            user_id="owner-1",
            actor_id="accountant-1",
            actor_name="Accountant",
            idempotency_key=f"reserved:{txn_type}",
            txn_type=txn_type,
            source="unsafe_direct_call",
            effective_at="2026-09-12T09:00:00Z",
            entries=_entries(),
        )
    assert exc.value.code == "accounting_v2_reserved_txn_type"
    assert db.rows == {}


@pytest.mark.asyncio
async def test_operational_post_requires_verified_opening_and_cutover_time():
    db = _DB()
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _atomic(
            db,
            lambda session: post_journal_v2(
                db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                idempotency_key="before-opening",
                txn_type="provider_settlement_v2",
                source="accounting_settlement_p01",
                effective_at="2026-09-12T09:00:00Z",
                entries=_entries(),
                mongo_session=session,
            ),
        )
    assert exc.value.code == "accounting_v2_verified_opening_required"
    assert db.rows == {}

    await _open(db)
    opening_snapshot = deepcopy(db.rows)
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _atomic(
            db,
            lambda session: post_journal_v2(
                db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                idempotency_key="before-cutover",
                txn_type="provider_settlement_v2",
                source="accounting_settlement_p01",
                effective_at="2026-09-11T23:59:59Z",
                entries=_entries(),
                mongo_session=session,
            ),
        )
    assert exc.value.code == "accounting_v2_effective_before_cutover"
    assert db.rows == opening_snapshot


@pytest.mark.asyncio
async def test_fixed_width_effective_time_orders_fractional_cutover_correctly():
    db = _DB()

    async def fractional_opening(session):
        return await post_opening_journal_v2(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            actor_name="Owner",
            opening_operation_id="fractional-cutover",
            approved_preview_hash="c" * 64,
            effective_at="2026-09-12T00:00:00.500000Z",
            entries=[
                {**_entries("1.00")[0], "entry_type": "opening_balance"},
                {**_entries("1.00")[1], "entry_type": "opening_balance"},
            ],
            mongo_session=session,
        )

    await _atomic(db, fractional_opening)
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _atomic(
            db,
            lambda session: post_journal_v2(
                db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                idempotency_key="same-second-before-fractional-cutover",
                txn_type="provider_settlement_v2",
                source="accounting_settlement_p01",
                effective_at="2026-09-12T00:00:00Z",
                entries=_entries(),
                mongo_session=session,
            ),
        )
    assert exc.value.code == "accounting_v2_effective_before_cutover"


@pytest.mark.asyncio
async def test_operational_post_rejects_corrupt_or_reversed_opening_but_retries_are_safe():
    corrupt_db = _DB()
    await _open(corrupt_db)
    corrupt_db.rows[GENERAL_LEDGER_COLLECTION][0]["amount_minor"] = 2
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _atomic(
            corrupt_db,
            lambda session: post_journal_v2(
                corrupt_db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                idempotency_key="after-corrupt-opening",
                txn_type="provider_settlement_v2",
                source="accounting_settlement_p01",
                effective_at="2026-09-12T09:00:00Z",
                entries=_entries(),
                mongo_session=session,
            ),
        )
    assert exc.value.code == "accounting_v2_opening_integrity_failure"

    db = _DB()
    opening = await _open(db)
    original = await _post(db)
    reversal = await _reverse(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        actor_name="Owner",
        original_txn_group_id=opening["group"]["txn_group_id"],
        effective_at="2026-09-13T00:00:00Z",
        reason="Owner-approved opening correction",
    )
    retry_reversal = await _reverse(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        actor_name="Owner",
        original_txn_group_id=opening["group"]["txn_group_id"],
        effective_at="2026-09-13T00:00:00Z",
        reason="Owner-approved opening correction",
    )
    assert retry_reversal == reversal

    # An already committed request remains safely idempotent after the gate
    # closes; a new request cannot append to a book whose opening was reversed.
    assert await _post(db) == original
    snapshot = deepcopy(db.rows)
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _post(db, key="new-after-opening-reversal")
    assert exc.value.code == "accounting_v2_opening_reversed"
    assert db.rows == snapshot


@pytest.mark.asyncio
async def test_every_writer_rejects_missing_or_inactive_caller_transaction():
    db = _DB()

    def writers(session):
        return [
            lambda: post_opening_journal_v2(
                db,
                user_id="owner-1",
                actor_id="owner-1",
                actor_name="Owner",
                opening_operation_id="opening-without-transaction",
                approved_preview_hash="a" * 64,
                effective_at="2026-09-12T00:00:00Z",
                entries=[
                    {**_entries("1.00")[0], "entry_type": "opening_balance"},
                    {**_entries("1.00")[1], "entry_type": "opening_balance"},
                ],
                mongo_session=session,
            ),
            lambda: post_journal_v2(
                db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                idempotency_key="post-without-transaction",
                txn_type="provider_settlement_v2",
                source="accounting_settlement_p01",
                effective_at="2026-09-12T09:00:00Z",
                entries=_entries(),
                mongo_session=session,
            ),
            lambda: reverse_journal_v2(
                db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                original_txn_group_id="missing-original",
                effective_at="2026-09-12T09:00:00Z",
                reason="No active transaction",
                mongo_session=session,
            ),
        ]

    for session in (None, _Session(db)):
        for writer in writers(session):
            with pytest.raises(AccountingLedgerV2Error) as exc:
                await writer()
            assert exc.value.code == "accounting_v2_atomic_transaction_required"
    assert db.rows == {}


@pytest.mark.asyncio
async def test_unknown_commit_outcome_retries_idempotently_in_a_fresh_session():
    db = _DB()
    await _open(db)

    async def post_operation(session):
        return await post_journal_v2(
            db,
            user_id="owner-1",
            actor_id="accountant-1",
            actor_name="Accountant",
            idempotency_key="unknown-commit-result",
            txn_type="provider_settlement_v2",
            source="accounting_settlement_p01",
            effective_at="2026-09-12T09:00:00Z",
            entries=_entries(),
            mongo_session=session,
        )

    db.raise_unknown_commit_once = True
    with pytest.raises(UnknownCommitResult, match="commit result was lost"):
        await _atomic(db, post_operation)
    committed_snapshot = deepcopy(db.rows)

    retry = await _atomic(db, post_operation)
    assert retry["group"]["idempotency_key"] == "unknown-commit-result"
    assert db.rows == committed_snapshot


@pytest.mark.asyncio
async def test_partial_insert_failure_rolls_back_group_legs_audit_and_sequence():
    db = _DB()
    await _open(db)
    before = deepcopy(db.rows)
    db.fail_collection = GENERAL_LEDGER_COLLECTION
    db.fail_on_insert_number = 2

    with pytest.raises(RuntimeError, match="injected insert failure"):
        await _atomic(
            db,
            lambda session: post_journal_v2(
                db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                idempotency_key="tamara:statement-1",
                txn_type="provider_settlement_v2",
                source="accounting_settlement_p01",
                effective_at="2026-09-12T12:00:00+03:00",
                entries=_entries(),
                mongo_session=session,
            ),
        )

    assert db.rows == before


@pytest.mark.asyncio
async def test_entry_sequence_exhaustion_fails_before_any_journal_insert():
    db = _DB()
    await _open(db)
    db.rows[SEQUENCES_COLLECTION][0]["last_entry_no"] = (2**63) - 1
    snapshot = deepcopy(db.rows)
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _atomic(
            db,
            lambda session: post_journal_v2(
                db,
                user_id="owner-1",
                actor_id="accountant-1",
                actor_name="Accountant",
                idempotency_key="sequence-exhausted",
                txn_type="provider_settlement_v2",
                source="accounting_settlement_p01",
                effective_at="2026-09-12T09:00:00Z",
                entries=_entries(),
                mongo_session=session,
            ),
        )
    assert exc.value.code == "accounting_v2_sequence_overflow"
    assert db.rows == snapshot


@pytest.mark.asyncio
async def test_idempotent_retry_returns_one_group_and_conflicting_payload_is_rejected():
    db = _DB()
    first = await _post(db)
    second = await _post(db)

    assert first == second
    assert len(db.rows[GROUPS_COLLECTION]) == 2
    assert len(db.rows[GENERAL_LEDGER_COLLECTION]) == 4
    assert len(db.rows[AUDIT_COLLECTION]) == 2
    assert db.rows[SEQUENCES_COLLECTION][0]["last_entry_no"] == 4

    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _post(db, amount="126.00")
    assert exc.value.code == "accounting_v2_idempotency_conflict"
    assert len(db.rows[GROUPS_COLLECTION]) == 2
    assert len(db.rows[GENERAL_LEDGER_COLLECTION]) == 4


@pytest.mark.asyncio
async def test_concurrent_same_request_commits_exactly_one_group():
    db = _DB()
    first, second = await asyncio.gather(_post(db), _post(db))
    assert first == second
    assert len(db.rows[GROUPS_COLLECTION]) == 2
    assert len(db.rows[GENERAL_LEDGER_COLLECTION]) == 4
    assert len(db.rows[AUDIT_COLLECTION]) == 2


@pytest.mark.asyncio
async def test_compute_and_append_only_reversal_restore_balance_without_mutating_original():
    db = _DB()
    original = await _post(db)
    original_snapshot = deepcopy(db.rows[GENERAL_LEDGER_COLLECTION])

    before = await compute_balance_v2(
        db,
        user_id="owner-1",
        entity_type="bank",
        entity_id="bank-1",
        sub_account="main",
    )
    assert before == {
        "debit_total": "125.50",
        "credit_total": "0.00",
        "net_balance": "125.50",
        "debit_total_minor": 12550,
        "credit_total_minor": 0,
        "net_balance_minor": 12550,
        "entry_count": 1,
    }

    reversal = await _reverse(
        db,
        user_id="owner-1",
        actor_id="accountant-2",
        actor_name="Reviewer",
        original_txn_group_id=original["group"]["txn_group_id"],
        effective_at="2026-09-13T10:00:00+03:00",
        reason="Approved correction",
    )
    retry = await _reverse(
        db,
        user_id="owner-1",
        actor_id="accountant-2",
        actor_name="Reviewer",
        original_txn_group_id=original["group"]["txn_group_id"],
        effective_at="2026-09-13T10:00:00+03:00",
        reason="Approved correction",
    )

    assert reversal == retry
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _reverse(
            db,
            user_id="owner-1",
            actor_id="accountant-2",
            actor_name="Reviewer",
            original_txn_group_id=original["group"]["txn_group_id"],
            effective_at="2026-09-13T10:00:00+03:00",
            reason="A conflicting reversal reason",
        )
    assert exc.value.code == "accounting_v2_reversal_conflict"
    assert db.rows[GENERAL_LEDGER_COLLECTION][: len(original_snapshot)] == original_snapshot
    assert reversal["group"]["reversal_of_txn_group_id"] == original["group"]["txn_group_id"]
    assert [entry["side"] for entry in reversal["entries"]] == ["credit", "debit"]
    assert len(db.rows[GROUPS_COLLECTION]) == 3
    assert len(db.rows[GENERAL_LEDGER_COLLECTION]) == 6
    after = await compute_balance_v2(
        db,
        user_id="owner-1",
        entity_type="bank",
        entity_id="bank-1",
        sub_account="main",
    )
    assert after["net_balance"] == "0.00"


@pytest.mark.asyncio
async def test_reversal_cannot_precede_original_effective_time():
    db = _DB()
    original = await _post(db)
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _reverse(
            db,
            user_id="owner-1",
            actor_id="accountant-2",
            actor_name="Reviewer",
            original_txn_group_id=original["group"]["txn_group_id"],
            effective_at="2026-09-11T10:00:00+03:00",
            reason="Approved correction",
        )
    assert exc.value.code == "accounting_v2_reversal_before_original"
    assert len(db.rows[GROUPS_COLLECTION]) == 2


@pytest.mark.asyncio
async def test_verifier_detects_tampering():
    db = _DB()
    journal = await _post(db)
    operational_bank = next(
        entry
        for entry in db.rows[GENERAL_LEDGER_COLLECTION]
        if entry.get("entity_type") == "bank"
    )
    operational_bank["amount"] = "125.49"

    verification = await verify_journal_v2(
        db, user_id="owner-1", txn_group_id=journal["group"]["txn_group_id"]
    )
    assert verification["verified"] is False
    assert "journal_entry_amount_minor_mismatch" in verification["errors"]
    assert "journal_debit_total_mismatch" in verification["errors"]
    assert "journal_content_hash_mismatch" in verification["errors"]
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await compute_balance_v2(
            db,
            user_id="owner-1",
            entity_type="bank",
            entity_id="bank-1",
        )
    assert exc.value.code == "accounting_v2_journal_integrity_failure"


@pytest.mark.asyncio
async def test_verifier_rejects_any_extra_audit_event_for_a_group():
    db = _DB()
    journal = await _post(db)
    db.rows[AUDIT_COLLECTION].append({
        "id": "unexpected-audit",
        "user_id": "owner-1",
        "operation_id": OPERATION_ID,
        "txn_group_id": journal["group"]["txn_group_id"],
        "event_type": "unexpected_event",
    })
    verification = await verify_journal_v2(
        db,
        user_id="owner-1",
        txn_group_id=journal["group"]["txn_group_id"],
    )
    assert verification["verified"] is False
    assert "journal_audit_missing_or_duplicated" in verification["errors"]


@pytest.mark.asyncio
async def test_int64_width_downgrade_is_detected_even_when_numeric_value_matches():
    db = _DB()
    journal = await _post(db)
    operational_bank = next(
        entry
        for entry in db.rows[GENERAL_LEDGER_COLLECTION]
        if entry.get("entity_type") == "bank"
    )
    operational_bank["amount_minor"] = int(operational_bank["amount_minor"])

    verification = await verify_journal_v2(
        db,
        user_id="owner-1",
        txn_group_id=journal["group"]["txn_group_id"],
    )
    assert verification["verified"] is False
    assert "journal_entry_amount_minor_mismatch" in verification["errors"]
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await aggregate_balances_v2(
            db,
            user_id="owner-1",
            group_by=("entity_type", "entity_id"),
            entity_type="bank",
            entity_id="bank-1",
        )
    assert exc.value.code == "accounting_v2_aggregate_integrity_failure"


@pytest.mark.asyncio
async def test_query_and_aggregate_use_exact_minor_units_and_never_legacy_rows():
    db = _DB(
        general_ledger=[
            {
                "user_id": "owner-1",
                "operation_id": OPERATION_ID,
                "entity_type": "bank",
                "entity_id": "bank-1",
                "amount": "999999.99",
                "amount_minor": 99999999,
                "side": "debit",
            }
        ]
    )
    await _post(db, key="small-1", amount="0.10")
    await _post(db, key="small-2", amount="0.20")

    rows = await query_entries_v2(
        db,
        user_id="owner-1",
        entity_type="bank",
        entity_id="bank-1",
        effective_from="2026-09-12T00:00:00Z",
        effective_through="2026-09-13T00:00:00Z",
    )
    assert [row["amount_minor"] for row in rows] == [10, 20]
    assert all(row["currency"] == "SAR" for row in rows)

    aggregate = await aggregate_balances_v2(
        db,
        user_id="owner-1",
        group_by=("entity_type", "entity_id"),
        entity_type="bank",
        entity_id="bank-1",
    )
    assert aggregate == [
        {
            "group": {"entity_type": "bank", "entity_id": "bank-1"},
            "debit_total": "0.30",
            "credit_total": "0.00",
            "net_balance": "0.30",
            "debit_total_minor": 30,
            "credit_total_minor": 0,
            "net_balance_minor": 30,
            "entry_count": 2,
        }
    ]

    operational_bank = next(
        entry
        for entry in db.rows[GENERAL_LEDGER_COLLECTION]
        if entry.get("entity_type") == "bank"
    )
    operational_bank["amount_minor"] = Int64(11)
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await aggregate_balances_v2(
            db,
            user_id="owner-1",
            group_by=("entity_type", "entity_id"),
            entity_type="bank",
            entity_id="bank-1",
        )
    assert exc.value.code == "accounting_v2_aggregate_integrity_failure"


@pytest.mark.asyncio
async def test_query_and_aggregate_fail_closed_on_v2_provenance_corruption():
    db = _DB()
    await _post(db)
    operational_bank = next(
        entry
        for entry in db.rows[GENERAL_LEDGER_COLLECTION]
        if entry.get("entity_type") == "bank"
    )
    operational_bank["operation_id"] = "wrong-operation"

    with pytest.raises(AccountingLedgerV2Error) as exc:
        await query_entries_v2(
            db,
            user_id="owner-1",
            entity_type="bank",
            entity_id="bank-1",
        )
    assert exc.value.code == "accounting_v2_journal_integrity_failure"

    with pytest.raises(AccountingLedgerV2Error) as exc:
        await aggregate_balances_v2(
            db,
            user_id="owner-1",
            group_by=("entity_type", "entity_id"),
            entity_type="bank",
            entity_id="bank-1",
        )
    assert exc.value.code == "accounting_v2_aggregate_integrity_failure"


@pytest.mark.asyncio
async def test_opening_wrapper_is_atomic_hash_bound_and_unique_per_book():
    db = _DB()
    entries = [
        {
            "leg_key": "cash",
            "entity_type": "bank",
            "entity_id": "cash-1",
            "sub_account": "main",
            "entry_type": "opening_balance",
            "amount": "500.00",
            "side": "debit",
        },
        {
            "leg_key": "capital",
            "entity_type": "equity",
            "entity_id": "capital",
            "sub_account": "opening",
            "entry_type": "opening_balance",
            "amount": "500.00",
            "side": "credit",
        },
    ]

    async def post_opening(session, *, operation_id="opening-1", preview_hash="a" * 64):
        return await post_opening_journal_v2(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            actor_name="Owner",
            opening_operation_id=operation_id,
            approved_preview_hash=preview_hash,
            effective_at="2027-01-01T00:00:00+03:00",
            entries=entries,
            mongo_session=session,
        )

    first = await _atomic(db, post_opening)
    retry = await _atomic(db, post_opening)
    assert first == retry
    assert first["group"]["txn_type"] == "opening_balance"
    assert first["group"]["metadata"]["approved_preview_hash"] == "a" * 64

    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _atomic(
            db,
            lambda session: post_opening(
                session,
                operation_id="opening-2",
                preview_hash="b" * 64,
            ),
        )
    assert exc.value.code == "accounting_v2_opening_already_exists"


@pytest.mark.asyncio
async def test_legacy_mz2_scan_blocks_activation_without_mutation():
    tagged = {
        "id": "legacy-mz2-1",
        "user_id": "owner-1",
        "txn_group_id": "legacy-group",
        "metadata": {"operation_id": OPERATION_ID},
    }
    unrelated = {
        "id": "legacy-other",
        "user_id": "owner-1",
        "metadata": {"source": "legacy"},
    }
    db = _DB(general_ledger=[tagged, unrelated])

    scan = await scan_mz2_rows_in_legacy_ledger(db, user_id="owner-1")
    assert scan == {
        "clear": False,
        "count": 1,
        "sample_entry_ids": ["legacy-mz2-1"],
        "sample_txn_group_ids": ["legacy-group"],
    }
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await _atomic(
            db,
            lambda session: assert_no_mz2_rows_in_legacy_ledger(
                db,
                user_id="owner-1",
                mongo_session=session,
            ),
        )
    assert exc.value.code == "accounting_v2_legacy_rows_detected"
    assert db.rows["general_ledger"] == [tagged, unrelated]
    assert await accounting_safe_active(db, user_id="owner-1") is False

    clear_db = _DB()
    with pytest.raises(AccountingLedgerV2Error) as exc:
        await assert_no_mz2_rows_in_legacy_ledger(clear_db, user_id="owner-1")
    assert exc.value.code == "accounting_v2_atomic_transaction_required"


@pytest.mark.asyncio
async def test_indexes_cover_idempotency_legs_entries_reversal_and_opening():
    db = _DB()
    await ensure_accounting_ledger_v2_indexes(db)

    by_name = {options["name"]: (collection, keys, options) for collection, keys, options in db.indexes}
    required_unique = {
        "uq_accounting_v2_group_idempotency",
        "uq_accounting_v2_group_reversal",
        "uq_accounting_v2_opening",
        "uq_accounting_v2_leg_number",
        "uq_accounting_v2_leg_key",
        "uq_accounting_v2_entry_number",
        "uq_accounting_v2_audit_id",
        "uq_accounting_v2_sequence",
    }
    assert required_unique <= by_name.keys()
    assert all(by_name[name][2].get("unique") is True for name in required_unique)


@pytest.mark.asyncio
async def test_startup_index_bootstrap_preserves_legacy_then_installs_v2(monkeypatch):
    import financial_provider_apps as apps

    calls = []

    async def legacy(db):
        calls.append(("legacy", db))

    async def v2(db):
        calls.append(("v2", db))

    monkeypatch.setattr(apps, "_ensure_legacy_financial_provider_app_indexes", legacy)
    monkeypatch.setattr(apps, "ensure_accounting_ledger_v2_indexes", v2)
    db = object()
    await apps.ensure_financial_provider_app_indexes(db)
    assert calls == [("legacy", db), ("v2", db)]


def test_v2_collection_names_are_absent_from_other_backend_production_modules():
    backend = Path(__file__).resolve().parents[1]
    protected = {
        "accounting_journal_groups_v2",
        "accounting_general_ledger_v2",
        "accounting_audit_log_v2",
        "accounting_ledger_sequences_v2",
    }
    offenders = []
    for path in backend.rglob("*.py"):
        if (
            path.name in {
                "accounting_ledger_v2.py",
                "check_accounting_ledger_v2_access.py",
            }
            or "tests" in path.parts
        ):
            continue
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in protected):
            offenders.append(path.relative_to(backend.parent).as_posix())
    assert offenders == []


def test_raw_access_guard_rejects_alias_reflection_and_private_writers(tmp_path):
    from scripts.check_accounting_ledger_v2_access import _violations

    source = tmp_path / "unsafe_consumer.py"
    source.write_text(
        "\n".join(
            [
                "import accounting_ledger_v2 as ledger",
                "alias = ledger",
                "raw_name = alias.GROUPS_COLLECTION",
                "private = ledger._insert_prepared_journal",
                "reflected = getattr(ledger, 'GENERAL_LEDGER_COLLECTION')",
                "from accounting_ledger_v2 import _post_prepared_v2",
            ]
        ),
        encoding="utf-8",
    )
    errors = _violations(source)
    assert any("GROUPS_COLLECTION" in error for error in errors)
    assert any("_insert_prepared_journal" in error for error in errors)
    assert any("reflective V2 core access" in error for error in errors)
    assert any("_post_prepared_v2" in error for error in errors)
