import asyncio
from copy import deepcopy

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import accounting_settlement_routes as settlement_routes
import accounting_settlement_service as service
from financial_provider_apps import make_financial_provider_apps_router


def _salla_amounts():
    return {
        "gross_sales": 1000,
        "refund_full": 80,
        "refund_partial": 20,
        "commission": 20,
        "commission_vat": 3,
        "settlement_fee": 0,
        "settlement_fee_vat": 0,
        "wallet_purchases": 10,
        "cancellation_amount": 0,
        "cancellation_fees": 0,
        "cancellation_fees_vat": 0,
        "other_deductions": 0,
        "rebates": 0,
        "reported_net": 867,
        "rounding_adjustment": 0,
        "statement_net_difference": 0,
    }


def test_salla_statement_equation_and_preview_are_balanced():
    calculation = service.calculate_settlement_totals(_salla_amounts())
    assert calculation["refunds_total"] == 100
    assert calculation["calculated_net"] == 867
    assert calculation["equation_difference"] == 0
    assert calculation["provider_receivable_close"] == 900

    preview = service.build_journal_preview(
        provider="salla",
        bank_account_id="bank-1",
        bank_account_name="الراجحي",
        amounts=_salla_amounts(),
    )
    assert preview["balanced"] is True
    assert preview["debit_total"] == 900
    assert preview["credit_total"] == 900
    assert {row["role"] for row in preview["entries"]} == {
        "bank_net",
        "commission",
        "commission_vat",
        "wallet_purchases",
        "provider_receivable",
    }


def test_rebate_is_preserved_as_separate_credit_leg():
    amounts = {
        **_salla_amounts(),
        "gross_sales": 1000,
        "refund_full": 100,
        "refund_partial": 0,
        "commission": 60,
        "commission_vat": 9,
        "wallet_purchases": 0,
        "rebates": 5,
        "reported_net": 836,
    }
    calculation = service.calculate_settlement_totals(amounts)
    assert calculation["calculated_net"] == 836
    assert calculation["provider_receivable_close"] == 900
    preview = service.build_journal_preview(
        provider="tabby",
        bank_account_id="bank-1",
        bank_account_name="الأهلي",
        amounts=amounts,
    )
    assert preview["balanced"] is True
    rebate = next(row for row in preview["entries"] if row["role"] == "rebates")
    assert rebate["side"] == "credit"
    assert rebate["amount"] == 5


def test_review_reasons_fail_closed_for_missing_or_unmatched_facts():
    file_doc = {
        "header": {"statement_id": "T-001"},
        "matched": 8,
        "unmatched": 2,
    }
    reasons = service.build_review_reasons(
        file_doc=file_doc,
        amounts={**_salla_amounts(), "reported_net": 866},
        bank_account_id=None,
        source_review_count=1,
    )
    codes = {row["code"] for row in reasons}
    assert {
        "missing_bank",
        "unmatched_rows",
        "statement_equation_difference",
        "source_requires_review",
    } <= codes
    assert service.has_blocking_reasons(reasons) is True


def test_statement_reference_period_and_provider_aliases_are_stable():
    doc = {
        "header": {
            "statement_id": "P0420741SA260822",
            "statement_period": "15/08/2026 - 21/08/2026",
            "statement_date_raw": "22/08/2026",
        }
    }
    assert service.statement_reference_from_file(doc) == "P0420741SA260822"
    assert service.period_from_file(doc) == (
        "2026-08-15",
        "2026-08-21",
        "2026-08-22",
    )
    assert service.canonical_provider("imkan") == "emkan"
    key1 = service.settlement_idempotency_key(
        user_id="u1",
        provider="imkan",
        statement_reference="ABC",
        source_hash="HASH",
    )
    key2 = service.settlement_idempotency_key(
        user_id="u1",
        provider="emkan",
        statement_reference="abc",
        source_hash="hash",
    )
    assert key1 == key2


class _Collection:
    def __init__(self, document=None):
        self.document = document
        self.inserted = []
        self.find_one_queries = []

    async def find_one(self, query, *_args, **_kwargs):
        self.find_one_queries.append(query)
        return self.document

    def aggregate(self, _pipeline):
        async def rows():
            yield {"_id": "debit", "total": 900, "count": 1}
        return rows()

    async def insert_one(self, document):
        self.inserted.append(document)


class _Db:
    def __init__(self, *, bank=None, existing_ledger=None):
        self.accounts = _Collection(bank)
        self.general_ledger = _Collection(existing_ledger)
        self.accounting_audit_log = _Collection()


@pytest.mark.asyncio
async def test_post_snapshots_bank_and_uses_one_balanced_group(monkeypatch):
    db = _Db(bank={
        "id": "bank-1",
        "name": "الراجحي",
        "account_type": "bank",
    })
    captured = {}
    post_count = 0

    async def fake_post(*_args, **kwargs):
        nonlocal post_count
        post_count += 1
        captured.update(kwargs)
        db.general_ledger.document = {"txn_group_id": "group-1"}
        return {
            "txn_group_id": "group-1",
            "entries": [{"id": "entry-1"}],
            "debit_total": 900,
            "credit_total": 900,
        }

    monkeypatch.setattr(service, "post_txn_group", fake_post)

    result = await service.post_reviewed_settlement(
        db,
        owner_id="owner-1",
        actor={"id": "accountant-1", "name": "المحاسب"},
        draft={
            "id": "draft-1",
            "status": "reviewed",
            "provider": "salla",
            "bank_account_id": "bank-1",
            "statement_reference": "SALLA-001",
            "source_file_id": "file-1",
            "source_file_hash": "hash-1",
            "idempotency_key": "idem-1",
            "amounts": _salla_amounts(),
            "review_reasons": [],
        },
    )
    assert result["txn_group_id"] == "group-1"
    assert result["bank_snapshot"] == {
        "id": "bank-1",
        "name": "الراجحي",
        "account_type": "bank",
    }
    assert captured["txn_type"] == "provider_settlement_v2"
    assert captured["user_id"] == "owner-1"
    assert captured["metadata"]["operation_id"] == "MZ2-FIN-CUTOVER-001"
    assert captured["metadata"]["idempotency_key"] == "idem-1"
    assert captured["metadata"]["source_file_id"] == "file-1"
    assert captured["metadata"]["source_file_hash"] == "hash-1"
    assert captured["metadata"]["bank_snapshot"] == result["bank_snapshot"]
    assert db.accounts.find_one_queries == [{
        "user_id": "owner-1",
        "id": "bank-1",
        "account_type": {"$in": ["bank", "cash"]},
    }]
    assert len(db.accounting_audit_log.inserted) == 1
    assert db.accounting_audit_log.inserted[0]["user_id"] == "owner-1"
    assert round(sum(
        row["amount"] for row in captured["entries"]
        if row["side"] == "debit"
    ), 2) == round(sum(
        row["amount"] for row in captured["entries"]
        if row["side"] == "credit"
    ), 2)
    with pytest.raises(HTTPException) as retry_error:
        await service.post_reviewed_settlement(
            db,
            owner_id="owner-1",
            actor={"id": "accountant-1", "name": "المحاسب"},
            draft={
                "id": "draft-1",
                "status": "reviewed",
                "provider": "salla",
                "bank_account_id": "bank-1",
                "statement_reference": "SALLA-001",
                "source_file_id": "file-1",
                "source_file_hash": "hash-1",
                "idempotency_key": "idem-1",
                "amounts": _salla_amounts(),
                "review_reasons": [],
            },
        )
    assert retry_error.value.status_code == 409
    assert post_count == 1


@pytest.mark.asyncio
async def test_post_fails_closed_for_review_reasons_and_unbalanced_preview(monkeypatch):
    db = _Db(bank={"id": "bank-1", "name": "الراجحي", "account_type": "bank"})
    draft = {
        "id": "draft-1",
        "status": "reviewed",
        "provider": "salla",
        "bank_account_id": "bank-1",
        "idempotency_key": "idem-1",
        "amounts": _salla_amounts(),
    }

    with pytest.raises(HTTPException) as review_error:
        await service.post_reviewed_settlement(
            db, owner_id="owner-1", actor={"id": "accountant-1"},
            draft={**draft, "review_reasons": [{"code": "statement_equation_difference"}]},
        )
    assert review_error.value.status_code == 409

    monkeypatch.setattr(service, "build_journal_preview", lambda **_kwargs: {
        "balanced": False,
        "debit_total": 900,
        "credit_total": 899,
        "entries": [],
        "amounts": {},
    })
    with pytest.raises(HTTPException) as balance_error:
        await service.post_reviewed_settlement(
            db, owner_id="owner-1", actor={"id": "accountant-1"},
            draft={**draft, "review_reasons": []},
        )
    assert balance_error.value.status_code == 400
    assert "غير متوازنة" in str(balance_error.value.detail)


@pytest.mark.asyncio
async def test_authoritative_post_route_denies_employee_without_mutation():
    class MutationTripwire:
        def __init__(self, name):
            self.name = name
            self.calls = 0

        def __getattr__(self, operation):
            async def fail(*_args, **_kwargs):
                self.calls += 1
                raise AssertionError(f"unexpected {self.name}.{operation} mutation")
            return fail

    class Users:
        def __init__(self):
            self.find_calls = 0

        async def find_one(self, query, *_args, **_kwargs):
            self.find_calls += 1
            assert query == {"id": "employee-1"}
            return {
                "id": "employee-1",
                "role": "employee",
                "created_by": "owner-1",
                "accounting_permissions": ["accounting.settlements.view"],
            }

    class Db:
        def __init__(self):
            self.users = Users()
            self.accounting_settlements_v2 = MutationTripwire("drafts")
            self.general_ledger = MutationTripwire("ledger")
            self.accounting_audit_log = MutationTripwire("audit")

    async def current_user():
        return {"id": "employee-1", "role": "employee"}

    db = Db()
    router = make_financial_provider_apps_router(db, current_user)
    matching_routes = [
        route for route in router.routes
        if route.path.endswith("/accounting-module/settlements/drafts/{draft_id}/post")
    ]
    assert len(matching_routes) >= 2
    assert matching_routes[0].endpoint.__module__ == "accounting_settlement_lifecycle_routes"
    assert matching_routes[0].endpoint.__name__ == "post_settlement"

    app = FastAPI()
    app.include_router(router, prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/financial-provider-apps/accounting-module/settlements/drafts/draft-1/post",
            json={},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "accounting_permission_required",
        "permission": "accounting.settlements.post",
        "message": "لا تملك الصلاحية المحاسبية المطلوبة",
    }
    assert db.users.find_calls == 1
    assert db.accounting_settlements_v2.calls == 0
    assert db.general_ledger.calls == 0
    assert db.accounting_audit_log.calls == 0


@pytest.mark.asyncio
async def test_authoritative_post_route_atomically_claims_concurrent_retry(monkeypatch):
    class Result:
        def __init__(self, matched_count):
            self.matched_count = matched_count

    class Drafts:
        def __init__(self, document):
            self.document = deepcopy(document)
            self.lock = asyncio.Lock()
            self.initial_reads = 0
            self.both_read = asyncio.Event()

        async def find_one(self, query, *_args, **_kwargs):
            if query.get("id") != self.document["id"] or query.get("user_id") != "owner-1":
                return None
            if self.document["status"] == "reviewed" and self.initial_reads < 2:
                snapshot = deepcopy(self.document)
                self.initial_reads += 1
                if self.initial_reads == 2:
                    self.both_read.set()
                await self.both_read.wait()
                return snapshot
            return deepcopy(self.document)

        async def update_one(self, query, update, **_kwargs):
            async with self.lock:
                if any(self.document.get(key) != value for key, value in query.items()):
                    return Result(0)
                self.document.update(deepcopy(update.get("$set") or {}))
                return Result(1)

    class Accounts:
        async def find_one(self, query, *_args, **_kwargs):
            assert query["user_id"] == "owner-1"
            return {"id": "bank-1", "name": "بنك اختباري", "account_type": "bank"}

    class Ledger:
        def __init__(self):
            self.posted = None

        async def find_one(self, query, *_args, **_kwargs):
            if self.posted and query.get("metadata.idempotency_key") == "idem-concurrent-1":
                return {"txn_group_id": "group-concurrent-1"}
            return None

        def aggregate(self, _pipeline):
            async def rows():
                yield {"_id": "debit", "total": 900, "count": 1}
            return rows()

    class Audit:
        def __init__(self):
            self.inserted = []

        async def insert_one(self, document):
            self.inserted.append(document)

    class Db:
        def __init__(self):
            self.users = _Collection({"id": "owner-1", "role": "owner"})
            self.accounting_settlements_v2 = Drafts({
                "id": "draft-concurrent-1",
                "user_id": "owner-1",
                "status": "reviewed",
                "provider": "salla",
                "bank_account_id": "bank-1",
                "statement_reference": "SALLA-CONCURRENT-001",
                "source_file_id": "file-concurrent-1",
                "source_file_hash": "hash-concurrent-1",
                "idempotency_key": "idem-concurrent-1",
                "amounts": _salla_amounts(),
                "review_reasons": [],
                "source_snapshot": {"matched": 1, "unmatched": 0},
            })
            self.accounts = Accounts()
            self.general_ledger = Ledger()
            self.accounting_audit_log = Audit()

    db = Db()
    post_calls = []

    async def fake_post_txn_group(*_args, **kwargs):
        post_calls.append(kwargs)
        await asyncio.sleep(0)
        db.general_ledger.posted = deepcopy(kwargs)
        return {
            "txn_group_id": "group-concurrent-1",
            "entries": [{"id": "entry-concurrent-1"}],
            "debit_total": 900,
            "credit_total": 900,
        }

    monkeypatch.setattr(service, "post_txn_group", fake_post_txn_group)

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    router = make_financial_provider_apps_router(db, current_user)
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path.endswith("/accounting-module/settlements/drafts/{draft_id}/post")
    )
    assert endpoint.__module__ == "accounting_settlement_lifecycle_routes"

    results = await asyncio.gather(
        endpoint("draft-concurrent-1", settlement_routes.DraftActionIn(), await current_user()),
        endpoint("draft-concurrent-1", settlement_routes.DraftActionIn(), await current_user()),
        return_exceptions=True,
    )

    successes = [result for result in results if isinstance(result, dict)]
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == 1
    assert successes[0]["ledger_txn_group_id"] == "group-concurrent-1"
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert "بدأ مستخدم آخر" in str(conflicts[0].detail)
    assert len(post_calls) == 1
    assert post_calls[0]["user_id"] == "owner-1"
    assert post_calls[0]["metadata"]["idempotency_key"] == "idem-concurrent-1"
    assert db.accounting_settlements_v2.document["status"] == "posted"
    assert len(db.accounting_audit_log.inserted) == 2
    assert {row["user_id"] for row in db.accounting_audit_log.inserted} == {"owner-1"}


@pytest.mark.asyncio
async def test_post_rejects_insufficient_canonical_provider_receivable(monkeypatch):
    db = _Db(bank={
        "id": "bank-1",
        "name": "الراجحي",
        "account_type": "bank",
    })

    async def fake_balance(*_args, **_kwargs):
        return {"net_balance": 100}

    monkeypatch.setattr(service, "compute_balance", fake_balance)

    with pytest.raises(HTTPException) as error:
        await service.post_reviewed_settlement(
            db,
            owner_id="owner-1",
            actor={"id": "accountant-1"},
            draft={
                "id": "draft-1",
                "status": "reviewed",
                "provider": "salla",
                "bank_account_id": "bank-1",
                "statement_reference": "SALLA-001",
                "idempotency_key": "idem-1",
                "amounts": _salla_amounts(),
                "review_reasons": [],
            },
        )
    assert error.value.status_code == 409
    assert "ذمة سلة غير كافية" in str(error.value.detail)


def test_router_registers_full_p01_settlement_contract():
    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    router = make_financial_provider_apps_router(object(), current_user)
    paths = {route.path for route in router.routes}
    assert {
        "/financial-provider-apps/accounting-module/settlements/context",
        "/financial-provider-apps/accounting-module/settlements/bindings/{provider}",
        "/financial-provider-apps/accounting-module/settlements/drafts/upload",
        "/financial-provider-apps/accounting-module/settlements/drafts/from-file",
        "/financial-provider-apps/accounting-module/settlements/drafts",
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}",
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}/match-entry",
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}/submit",
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}/review",
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}/reject",
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}/post",
    } <= paths
