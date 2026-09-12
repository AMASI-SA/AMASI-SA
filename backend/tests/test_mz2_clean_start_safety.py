"""Public-seam regression tests for the Mezan 2 clean-start boundary.

No test here reaches MongoDB or creates a real financial record.  The fake
collections raise immediately if a denied route attempts any mutation.
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import accounting_clean_start_guard as clean_start_guard
import accounts_routes
import ad_account_routes
import ledger_routes
import migration_routes
from accounting_clean_start_guard import accounting_safe_active
from ledger_core import post_txn_group
from store_delivery_accounting import (
    financial_cutover_is_active,
    post_delivery_journal,
    post_settlement_journal,
)
from store_courier_domain import WORKFLOWS
from store_delivery_domain import (
    DELIVERY_STATUS_DELIVERED,
    DELIVERY_STATUS_OUT_FOR_DELIVERY,
)
from store_delivery_driver_app_routes import (
    DRIVER_COLLECTIONS,
    DRIVER_EARNINGS,
    make_store_delivery_driver_app_router,
)
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, EVENTS, ORDERS
from store_delivery_settlement_routes import make_store_delivery_settlement_router
from universal_accounting_routes import (
    _account_live_balance,
    _enforce_sufficient_funds,
    _ensure_opening_balance_seeded,
)


OPERATION_ID = "MZ2-FIN-CUTOVER-001"
LEGACY_DISABLED = {
    "detail": {
        "code": "legacy_financial_migration_disabled",
        "operation_id": OPERATION_ID,
        "message": (
            "ميزان 2 يبدأ بأرصدة افتتاحية موثقة عبر P07؛ "
            "ترحيل بيانات ميزان القديم معطل نهائيًا"
        ),
    },
}
P07_ONLY = {
    "detail": {
        "code": "opening_balance_p07_only",
        "operation_id": OPERATION_ID,
        "message": "إنشاء أو تعديل الرصيد الافتتاحي متاح فقط عبر مسار P07",
    },
}
NOT_SAFE_ACTIVE = {
    "detail": {
        "code": "accounting_cutover_not_safe_active",
        "operation_id": OPERATION_ID,
        "message": "الكتابة المحاسبية مقفلة حتى اكتمال P07 وتحقق safe_active",
    },
}
V2_DEDICATED_LEDGER_REQUIRED = {
    "detail": {
        "code": "accounting_v2_dedicated_ledger_required",
        "operation_id": OPERATION_ID,
        "message": (
            "قيود ميزان 2 تكتب فقط عبر دفتر V2 المخصص؛ "
            "دفتر ledger العام مخصص لميزان القديم"
        ),
    },
}


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
            if not any(_matches(row, clause) for clause in wanted):
                return False
            continue
        actual = _nested(row, key)
        if isinstance(wanted, dict):
            if "$ne" in wanted and actual == wanted["$ne"]:
                return False
            if "$in" in wanted and actual not in wanted["$in"]:
                return False
            continue
        if actual != wanted:
            return False
    return True


class _Cursor:
    def __init__(self, rows: list[dict]):
        self.rows = [deepcopy(row) for row in rows]
        self._index = 0

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, count: int):
        self.rows = self.rows[count:]
        return self

    def limit(self, count: int):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self.rows):
            raise StopAsyncIteration
        value = deepcopy(self.rows[self._index])
        self._index += 1
        return value


class _Collection:
    def __init__(self, db: "_DB", name: str, rows: list[dict] | None = None):
        self.db = db
        self.name = name
        self.rows = list(rows or [])

    async def find_one(self, query, _projection=None):
        self.db.reads.append((self.name, "find_one"))
        return next(
            (deepcopy(row) for row in self.rows if _matches(row, query)),
            None,
        )

    def find(self, query, _projection=None):
        self.db.reads.append((self.name, "find"))
        return _Cursor([row for row in self.rows if _matches(row, query)])

    def aggregate(self, _pipeline):
        self.db.reads.append((self.name, "aggregate"))
        return _Cursor([])

    def _mutation(self, operation: str):
        self.db.mutations.append((self.name, operation))
        raise AssertionError(f"unexpected mutation: {self.name}.{operation}")

    async def insert_one(self, *_args, **_kwargs):
        self._mutation("insert_one")

    async def update_one(self, *_args, **_kwargs):
        self._mutation("update_one")

    async def update_many(self, *_args, **_kwargs):
        self._mutation("update_many")

    async def delete_one(self, *_args, **_kwargs):
        self._mutation("delete_one")

    async def delete_many(self, *_args, **_kwargs):
        self._mutation("delete_many")

    async def find_one_and_update(self, *_args, **_kwargs):
        self._mutation("find_one_and_update")

    async def create_index(self, *_args, **_kwargs):
        self._mutation("create_index")


class _MutationResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count
        self.modified_count = matched_count


class _MutableCollection(_Collection):
    """Small in-memory collection for the allowed operational delivery seam."""

    async def create_index(self, *_args, **_kwargs):
        return "test-index"

    def aggregate(self, pipeline):
        self.db.reads.append((self.name, "aggregate"))
        rows = [deepcopy(row) for row in self.rows]
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if _matches(row, stage["$match"])]
                continue
            if "$group" not in stage:
                continue
            group = stage["$group"]
            if group.get("_id") == "$side":
                totals: dict[str, dict] = {}
                for row in rows:
                    side = row.get("side")
                    bucket = totals.setdefault(
                        side, {"_id": side, "total": 0.0, "count": 0},
                    )
                    bucket["total"] += float(row.get("amount") or 0)
                    bucket["count"] += 1
                rows = list(totals.values())
            elif group.get("_id") is None and "$max" in group.get("mx", {}):
                values = [row.get("entry_no") for row in rows]
                values = [value for value in values if value is not None]
                rows = [{"_id": None, "mx": max(values)}] if values else []
        return _Cursor(rows)

    async def insert_one(self, document, **_kwargs):
        self.db.mutations.append((self.name, "insert_one"))
        self.rows.append(deepcopy(document))
        return _MutationResult(1)

    async def update_one(self, query, update, **_kwargs):
        self.db.mutations.append((self.name, "update_one"))
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set") or {}))
                return _MutationResult(1)
        return _MutationResult(0)

    async def find_one_and_update(
        self, query, update, **_kwargs,
    ):
        self.db.mutations.append((self.name, "find_one_and_update"))
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set") or {}))
                return deepcopy(row)
        return None

    async def delete_one(self, query, **_kwargs):
        self.db.mutations.append((self.name, "delete_one"))
        for index, row in enumerate(self.rows):
            if _matches(row, query):
                self.rows.pop(index)
                return _MutationResult(1)
        return _MutationResult(0)


class _DB:
    def __init__(self, **seeded_rows):
        self.reads: list[tuple[str, str]] = []
        self.mutations: list[tuple[str, str]] = []
        self._collections: dict[str, _Collection] = {}
        for name, rows in seeded_rows.items():
            self._collections[name] = _Collection(self, name, rows)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collections.setdefault(name, _Collection(self, name))

    def __getitem__(self, name: str):
        return getattr(self, name)


class _OperationalDB(_DB):
    def __init__(self, **seeded_rows):
        super().__init__()
        for name, rows in seeded_rows.items():
            self._collections[name] = _MutableCollection(self, name, rows)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collections.setdefault(
            name, _MutableCollection(self, name),
        )


async def _fake_user(_request, _db):
    return {
        "id": "owner-1",
        "email": "owner@example.test",
        "name": "Owner",
        "role": "owner",
    }


def _build_app(db: _DB, monkeypatch) -> FastAPI:
    for module in (
        accounts_routes,
        ad_account_routes,
        ledger_routes,
        migration_routes,
    ):
        monkeypatch.setattr(module, "get_current_user_from_db", _fake_user)

    api = APIRouter(prefix="/api")
    api.include_router(migration_routes.make_migration_router(db))
    api.include_router(ledger_routes.make_ledger_router(db))
    accounts_routes.attach_accounts_routes(api, db)
    ad_account_routes.attach_ad_account_routes(api, db)
    app = FastAPI()
    app.include_router(api)
    return app


async def _assert_idempotent_denial(
    client: AsyncClient,
    db: _DB,
    method: str,
    path: str,
    expected: dict,
    *,
    payload: dict | None = None,
) -> None:
    before = list(db.mutations)
    for _ in range(2):
        response = await client.request(method, path, json=payload)
        assert response.status_code == 409, response.text
        assert response.json() == expected
        assert db.mutations == before


@pytest.mark.asyncio
async def test_legacy_migration_writers_fail_closed_without_mutation(monkeypatch):
    db = _DB()
    app = _build_app(db, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        cases = [
            (
                "POST",
                "/api/accounting/migration/run",
                {"cutoff_date": "2026-01-01", "dry_run": False},
            ),
            (
                "POST",
                "/api/accounting/migration/orphan-suppliers/legacy-1/write-off",
                None,
            ),
            (
                "POST",
                "/api/ad-accounts/migration/apply",
                {
                    "from_date": "2025-01-01",
                    "to_date": "2025-12-31",
                    "mode": "daily",
                    "account_ids": ["ad-1"],
                },
            ),
            (
                "POST",
                "/api/ad-accounts/migration/cleanup-duplicates?dry_run=false",
                None,
            ),
        ]
        for method, path, payload in cases:
            await _assert_idempotent_denial(
                client, db, method, path, LEGACY_DISABLED, payload=payload,
            )

    assert db.mutations == []


@pytest.mark.asyncio
async def test_legacy_migration_dry_run_stays_read_only(monkeypatch):
    async def _empty(*_args, **_kwargs):
        return []

    async def _empty_after(*_args, **_kwargs):
        return {
            "employees": [], "suppliers": [], "externals": [],
            "banks": [], "payment_platforms": [], "couriers": [],
        }

    for name in (
        "_legacy_employee_balances",
        "_legacy_supplier_balances",
        "_legacy_external_balances",
        "_legacy_bank_balances",
        "_legacy_payment_platform_balances",
        "_legacy_courier_balances",
    ):
        monkeypatch.setattr(migration_routes, name, _empty)
    monkeypatch.setattr(migration_routes, "_compute_after_balances", _empty_after)

    db = _DB()
    app = _build_app(db, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        for _ in range(2):
            response = await client.post(
                "/api/accounting/migration/run",
                json={"cutoff_date": "2026-01-01", "dry_run": True},
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "dry_run_ok"
            assert response.json()["applied_count"] == 0
            assert db.mutations == []


@pytest.mark.asyncio
async def test_opening_balance_bypasses_are_exact_and_zero_mutation(monkeypatch):
    opening_rows = [
        {
            "id": "opening-draft",
            "user_id": "owner-1",
            "txn_group_id": "opening-group",
            "entry_type": "opening_balance",
            "status": "draft",
            "side": "debit",
            "amount": 100,
        },
        {
            "id": "opening-draft-2",
            "user_id": "owner-1",
            "txn_group_id": "opening-group",
            "entry_type": "opening_balance",
            "status": "draft",
            "side": "credit",
            "amount": 100,
        },
        {
            "id": "opening-bank-2",
            "user_id": "owner-1",
            "txn_group_id": "opening-bank-2-group",
            "entity_type": "bank",
            "entity_id": "bank-2",
            "entry_type": "opening_balance",
            "status": "posted",
            "side": "debit",
            "amount": 50,
        },
    ]
    db = _DB(
        general_ledger=opening_rows,
        accounts=[
            {"id": "bank-1", "user_id": "owner-1"},
            {"id": "bank-2", "user_id": "owner-1"},
        ],
        account_transactions=[{
            "id": "opening-tx", "user_id": "owner-1",
            "account_id": "bank-1", "transaction_type": "opening_balance",
        }],
    )
    app = _build_app(db, monkeypatch)
    ledger_opening = {
        "entity_type": "bank",
        "entity_id": "bank-1",
        "entry_type": "opening_balance",
        "amount": 100,
        "side": "debit",
        "auto_post": True,
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        cases = [
            ("POST", "/api/ledger/entries", ledger_opening),
            (
                "POST", "/api/accounts",
                {
                    "name": "Legacy zero",
                    "account_type": "bank",
                    "opening_balance": 0,
                    "opening_balance_date": "2026-01-01",
                },
            ),
            (
                "PUT", "/api/accounts/bank-1",
                {"opening_balance": 0},
            ),
            (
                "POST", "/api/accounts/bank-1/transactions",
                {
                    "transaction_type": "opening_balance",
                    "amount": 100,
                    "direction": "in",
                    "transaction_date": "2026-01-01",
                },
            ),
            (
                "PUT", "/api/ad-accounts/ad-1/opening",
                {"opening_balance": 100},
            ),
            (
                "PATCH", "/api/ad-accounts/ad-1",
                {"opening_debt": 100},
            ),
            (
                "POST", "/api/ad-accounts",
                {
                    "name": "Legacy ad account", "ad_provider": "snapchat",
                    "opening_balance": 0, "opening_debt": 0,
                },
            ),
            ("DELETE", "/api/accounts/bank-1", None),
            ("DELETE", "/api/accounts/bank-2", None),
            (
                "DELETE",
                "/api/accounts/bank-1/transactions/opening-tx",
                None,
            ),
            ("POST", "/api/ledger/entries/opening-draft/post", None),
            (
                "POST", "/api/ledger/entries/opening-draft/reverse",
                {"reason_code": "correction"},
            ),
            (
                "POST", "/api/ledger/groups/opening-group/reverse",
                {"reason_code": "correction"},
            ),
        ]
        for method, path, payload in cases:
            await _assert_idempotent_denial(
                client, db, method, path, P07_ONLY, payload=payload,
            )

    assert db.mutations == []


@pytest.mark.asyncio
async def test_v2_scoped_generic_ledger_mutations_are_always_rejected(monkeypatch):
    async def forged_active(*_args, **_kwargs):
        return True

    # Generic legacy ledger entry points must remain closed to MZ2 even after
    # a future authoritative gate becomes active.
    monkeypatch.setattr(clean_start_guard, "accounting_safe_active", forged_active)
    v2_metadata = {"operation_id": OPERATION_ID}
    rows = [
        {
            "id": "v2-draft", "user_id": "owner-1",
            "txn_group_id": "v2-group", "entry_type": "adjustment",
            "status": "draft", "metadata": v2_metadata,
        },
        {
            "id": "v2-posted", "user_id": "owner-1",
            "txn_group_id": "v2-group", "entry_type": "adjustment",
            "status": "posted", "metadata": v2_metadata,
        },
        {
            "id": "v2-bank-leg", "user_id": "owner-1",
            "txn_group_id": "v2-bank-group",
            "entity_type": "bank", "entity_id": "bank-v2",
            "entry_type": "adjustment", "status": "posted",
            "metadata": v2_metadata,
        },
    ]
    db = _DB(
        general_ledger=rows,
        accounts=[{"id": "bank-v2", "user_id": "owner-1"}],
    )
    app = _build_app(db, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        cases = [
            (
                "POST", "/api/ledger/entries",
                {
                    "entity_type": "bank", "entity_id": "bank-1",
                    "entry_type": "adjustment", "amount": 10,
                    "side": "debit", "metadata": v2_metadata,
                },
            ),
            ("POST", "/api/ledger/entries/v2-draft/post", None),
            (
                "POST", "/api/ledger/entries/v2-posted/reverse",
                {"reason_code": "correction"},
            ),
            (
                "POST", "/api/ledger/groups/v2-group/reverse",
                {"reason_code": "correction"},
            ),
            ("DELETE", "/api/accounts/bank-v2", None),
        ]
        for method, path, payload in cases:
            await _assert_idempotent_denial(
                client,
                db,
                method,
                path,
                V2_DEDICATED_LEDGER_REQUIRED,
                payload=payload,
            )
    assert db.mutations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_type", "extra_metadata", "expected"),
    [
        ("spend", {"operation_id": OPERATION_ID}, V2_DEDICATED_LEDGER_REQUIRED),
        ("opening_balance", {}, P07_ONLY),
    ],
)
async def test_iter215_cleanup_prevalidates_protected_candidates_without_mutation(
    monkeypatch, entry_type, extra_metadata, expected,
):
    row = {
        "id": "iter215-v2-leg",
        "user_id": "owner-1",
        "txn_group_id": "iter215-v2-group",
        "entry_type": entry_type,
        "status": "posted",
        "metadata": {
            "iter": "iter215",
            "spend_date": "2025-01-01",
            **extra_metadata,
        },
    }
    db = _DB(general_ledger=[row])

    def aggregate(_pipeline):
        db.reads.append(("general_ledger", "aggregate"))
        return _Cursor([{
            "_id": "iter215-v2-group",
            "spend_date": "2025-01-01",
        }])

    def find(query, projection):
        assert projection.get("metadata") == 1
        assert projection.get("entry_type") == 1
        db.reads.append(("general_ledger", "find"))
        return _Cursor([row] if _matches(row, query) else [])

    monkeypatch.setattr(db.general_ledger, "aggregate", aggregate)
    monkeypatch.setattr(db.general_ledger, "find", find)
    app = _build_app(db, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        await _assert_idempotent_denial(
            client,
            db,
            "POST",
            "/api/ledger/admin/iter215/cleanup-backfill",
            expected,
        )
    assert db.mutations == []


@pytest.mark.asyncio
async def test_forged_flags_and_balanced_legacy_rows_cannot_unlock_book(monkeypatch):
    forged_settings = [{
        "user_id": "owner-1",
        "mezan2_financial_cutover": {
            "operation_id": OPERATION_ID,
            "status": "active",
            "cutover_at": "2026-01-01T00:00:00+03:00",
            "opening_balance_preview_id": "forged-preview",
            "opening_balance_preview_balanced": True,
            "opening_balance_approved_at": "2026-01-01T00:01:00+03:00",
            "opening_balance_approved_by": "owner-1",
            "opening_balance_txn_group_id": "legacy-balanced",
            "evidence_sheet_ref": "forged-ref",
            "evidence_sections": {
                "banks_cash": "x", "providers": "x", "couriers_cod": "x",
                "inventory": "x", "suppliers": "x",
                "payroll_obligations": "x", "equity": "x",
            },
        },
    }]
    forged_opening = [
        {
            "user_id": "owner-1", "txn_group_id": "legacy-balanced",
            "entry_type": "opening_balance", "status": "posted",
            "side": "debit", "amount": 500,
            "metadata": {"operation_id": OPERATION_ID},
        },
        {
            "user_id": "owner-1", "txn_group_id": "legacy-balanced",
            "entry_type": "opening_balance", "status": "posted",
            "side": "credit", "amount": 500,
            "metadata": {"operation_id": OPERATION_ID},
        },
    ]
    db = _DB(settings=forged_settings, general_ledger=forged_opening)
    assert await accounting_safe_active(db, user_id="owner-1") is False
    assert db.reads == []

    app = _build_app(db, monkeypatch)
    payload = {
        "entity_type": "bank", "entity_id": "bank-1",
        "entry_type": "adjustment", "amount": 25, "side": "debit",
        "metadata": {"operation_id": OPERATION_ID},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        await _assert_idempotent_denial(
            client, db, "POST", "/api/ledger/entries",
            V2_DEDICATED_LEDGER_REQUIRED, payload=payload,
        )
    assert db.reads == []


@pytest.mark.asyncio
async def test_legacy_seed_is_scoped_idempotent_and_prevents_repeat_overspend():
    db = _OperationalDB(
        accounts=[{
            "id": "legacy-bank",
            "user_id": "owner-1",
            "name": "Legacy Bank",
            "account_type": "bank",
            "current_balance": 100,
        }],
        general_ledger=[],
        accounting_audit_log=[],
    )

    for _ in range(2):
        assert await _ensure_opening_balance_seeded(
            db, user_id="owner-1", account_id="legacy-bank",
        ) is None
    assert len(db.general_ledger.rows) == 2
    for row in db.general_ledger.rows:
        assert row["entry_type"] == "opening_balance"
        assert row["metadata"]["book_scope"] == "legacy"
        assert "operation_id" not in row["metadata"]

    await post_txn_group(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        actor_name="Owner",
        txn_type="expense_record",
        notes="legacy cash out",
        metadata={"book_scope": "legacy"},
        entries=[
            {
                "entity_type": "expense",
                "entity_id": "general",
                "side": "debit",
                "amount": 60,
                "entry_type": "expense_record",
            },
            {
                "entity_type": "bank",
                "entity_id": "legacy-bank",
                "sub_account": "main",
                "side": "credit",
                "amount": 60,
                "entry_type": "expense_record",
            },
        ],
    )
    live, _account = await _account_live_balance(
        db, user_id="owner-1", account_id="legacy-bank",
    )
    assert live == 40.0
    with pytest.raises(HTTPException) as exc:
        await _enforce_sufficient_funds(
            db,
            user_id="owner-1",
            account_id="legacy-bank",
            amount=50,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "لا يمكن تنفيذ العملية، رصيد الحساب المختار غير كافٍ."
    )
    assert all(
        "operation_id" not in (row.get("metadata") or {})
        for row in db.general_ledger.rows
    )
    assert "accounting_general_ledger_v2" not in db._collections
    assert "accounting_journal_groups_v2" not in db._collections
    assert "accounting_audit_log_v2" not in db._collections


@pytest.mark.asyncio
async def test_legacy_account_transaction_stays_outside_opening_and_v2(monkeypatch):
    db = _OperationalDB(
        accounts=[{"id": "bank-1", "user_id": "owner-1"}],
        account_transactions=[],
        general_ledger=[],
    )

    async def no_recompute(*_args, **_kwargs):
        return 25.0

    monkeypatch.setattr(accounts_routes, "_recompute_balance", no_recompute)
    app = _build_app(db, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/accounts/bank-1/transactions",
            json={
                "transaction_type": "income", "amount": 25,
                "direction": "in", "transaction_date": "2026-01-02",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["transaction_type"] == "income"
    assert len(db.account_transactions.rows) == 1
    written = db.account_transactions.rows[0]
    assert written["transaction_type"] != "opening_balance"
    assert "operation_id" not in written
    assert db.general_ledger.rows == []


@pytest.mark.asyncio
async def test_store_delivery_financial_seams_stay_closed_before_any_mutation():
    forged_settings = [{
        "user_id": "merchant-1",
        "mezan2_financial_cutover": {
            "operation_id": OPERATION_ID,
            "status": "active",
            "cutover_at": "2025-01-01T00:00:00+03:00",
            "opening_balance_txn_group_id": "legacy-balanced",
        },
    }]
    forged_opening = [
        {
            "user_id": "merchant-1", "txn_group_id": "legacy-balanced",
            "entry_type": "opening_balance", "status": "posted",
            "side": "debit", "amount": 500,
            "metadata": {"operation_id": OPERATION_ID},
        },
        {
            "user_id": "merchant-1", "txn_group_id": "legacy-balanced",
            "entry_type": "opening_balance", "status": "posted",
            "side": "credit", "amount": 500,
            "metadata": {"operation_id": OPERATION_ID},
        },
    ]
    driver = {
        "id": "driver-1", "user_id": "merchant-1",
        "account_user_id": "driver-user-1", "status": "active",
        "name": "Driver One",
    }
    db = _DB(
        settings=forged_settings,
        general_ledger=forged_opening,
        **{STORE_DRIVERS: [driver]},
    )

    assert await financial_cutover_is_active(
        db,
        user_id="merchant-1",
        event_at="2026-01-01T00:00:00+03:00",
    ) is False
    assert db.reads == []

    direct_calls = [
        post_delivery_journal(
            db,
            user_id="merchant-1",
            actor_id="driver-user-1",
            actor_name="Driver One",
            driver=driver,
            assignment={
                "id": "assignment-1", "order_id": "order-1",
                "order_number": "1001",
            },
            cod_custody_amount=250,
            delivery_fee=20,
        ),
        post_settlement_journal(
            db,
            user_id="merchant-1",
            actor_id="owner-1",
            actor_name="Owner",
            settlement_id="settlement-1",
            driver=driver,
            account={"id": "bank-1", "name": "Bank"},
            settlement_type="cod_remittance",
            bank_amount=250,
        ),
    ]
    for call in direct_calls:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409
        assert {"detail": exc.value.detail} == NOT_SAFE_ACTIVE
        assert db.mutations == []
    assert db.reads == []

    async def driver_user():
        return {
            "id": "driver-user-1", "role": "store_driver",
            "created_by": "merchant-1", "name": "Driver One",
        }

    operational_db = _OperationalDB(
        settings=deepcopy(forged_settings),
        general_ledger=deepcopy(forged_opening),
        **{
            STORE_DRIVERS: [deepcopy(driver)],
            ASSIGNMENTS: [{
                "id": "assignment-1",
                "user_id": "merchant-1",
                "driver_id": "driver-1",
                "active": True,
                "status": DELIVERY_STATUS_OUT_FOR_DELIVERY,
                "order_id": "order-1",
                "order_number": "1001",
                "barcode": "ORDER-1001",
                "delivery_fee_snapshot": 20,
                "driver_name_snapshot": "Driver One",
            }],
            ORDERS: [{
                "user_id": "merchant-1",
                "order_id": "order-1",
                "order_number": "1001",
                "remaining_amount": 0,
            }],
            WORKFLOWS: [],
            EVENTS: [],
            DRIVER_EARNINGS: [],
            DRIVER_COLLECTIONS: [],
        },
    )
    driver_app = FastAPI()
    driver_app.include_router(
        make_store_delivery_driver_app_router(operational_db, driver_user),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=driver_app), base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/store-delivery/app/deliveries/status",
            json={
                "barcode": "ORDER-1001",
                "target_status": DELIVERY_STATUS_DELIVERED,
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["accounting_status"] == "cutover_pending"
    assert response.json()["ledger_txn_group_id"] is None
    assert "accounting_operation_id" not in response.json()
    for row in (
        operational_db[ASSIGNMENTS].rows[0],
        operational_db[DRIVER_EARNINGS].rows[0],
        operational_db[DRIVER_COLLECTIONS].rows[0],
    ):
        assert row["accounting_status"] == "cutover_pending"
        assert row["ledger_txn_group_id"] is None
        assert "accounting_operation_id" not in row
    assert operational_db.general_ledger.rows == forged_opening
    assert not any(
        name == "general_ledger" for name, _operation in operational_db.mutations
    )

    async def owner_user():
        return {"id": "merchant-1", "role": "owner", "name": "Owner"}

    settlement_app = FastAPI()
    settlement_app.include_router(
        make_store_delivery_settlement_router(db, owner_user), prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=settlement_app), base_url="http://test",
    ) as client:
        for endpoint, payload in (
            ("cod-remittance", {"amount": 10, "account_id": "bank-1"}),
            ("earning-payment", {"amount": 10, "account_id": "bank-1"}),
            (
                "net-settlement",
                {"amount": 10, "earning_offset": 5, "account_id": "bank-1"},
            ),
        ):
            await _assert_idempotent_denial(
                client,
                db,
                "POST",
                f"/api/store-delivery/settlements/driver/driver-1/{endpoint}",
                NOT_SAFE_ACTIVE,
                payload=payload,
            )
    assert db.mutations == []
