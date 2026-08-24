import pytest
from fastapi import HTTPException

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

    async def find_one(self, *_args, **_kwargs):
        return self.document


class _Db:
    def __init__(self, *, bank=None, existing_ledger=None):
        self.accounts = _Collection(bank)
        self.general_ledger = _Collection(existing_ledger)


@pytest.mark.asyncio
async def test_post_snapshots_bank_and_uses_one_balanced_group(monkeypatch):
    db = _Db(bank={
        "id": "bank-1",
        "name": "الراجحي",
        "account_type": "bank",
    })
    captured = {}

    async def fake_balance(*_args, **_kwargs):
        return {"net_balance": 900}

    async def fake_post(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "txn_group_id": "group-1",
            "entries": [{"id": "entry-1"}],
            "debit_total": 900,
            "credit_total": 900,
        }

    async def fake_audit(*_args, **_kwargs):
        return "audit-1"

    monkeypatch.setattr(service, "compute_balance", fake_balance)
    monkeypatch.setattr(service, "post_txn_group", fake_post)
    monkeypatch.setattr(service, "write_audit", fake_audit)

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
    assert captured["metadata"]["operation_id"] == "MZ2-FIN-CUTOVER-001"
    assert captured["metadata"]["idempotency_key"] == "idem-1"
    assert round(sum(
        row["amount"] for row in captured["entries"]
        if row["side"] == "debit"
    ), 2) == round(sum(
        row["amount"] for row in captured["entries"]
        if row["side"] == "credit"
    ), 2)


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
