from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from accounting_financial_accounts import (
    ACCOUNT_TYPES,
    EVIDENCE_SECTION_IDS,
    OPENING_CATEGORIES,
    PERMISSIONS,
    AccountCreate,
    OpeningDraftCreate,
    OpeningLine,
    _draft_content,
)
from accounting_module_contract import accounting_permissions_for_user
from accounting_writer_transition import assert_writer_allowed


class _Rows:
    def __init__(self, row=None):
        self.row = row

    async def find_one(self, *_args, **_kwargs):
        return self.row


class _DB:
    def __init__(self, row=None):
        self.mz2_atomic_owners = _Rows(row)

    def __getitem__(self, _name):
        return self.mz2_atomic_owners


def _row(state: str, *, revision: int = 1, contract_revision: int = 1):
    return {
        "_id": "owner-1",
        "ledger_backend_state": state,
        "ledger_backend_revision": revision,
        "ledger_backend_contract_revision": contract_revision,
        "ledger_backend_activation_ref": "activation" if state == "v2_active" else None,
    }


@pytest.mark.asyncio
async def test_transition_contract_is_fail_closed_and_one_writer_only():
    await assert_writer_allowed(_DB(), "owner-1", "legacy")
    with pytest.raises(HTTPException) as exc:
        await assert_writer_allowed(_DB(), "owner-1", "v2")
    assert exc.value.detail["code"] == "accounting_v2_not_active"

    for writer in ("legacy", "v2"):
        with pytest.raises(HTTPException) as exc:
            await assert_writer_allowed(_DB(_row("transition_blocked")), "owner-1", writer)
        assert exc.value.detail["code"] == "accounting_transition_blocked"

    await assert_writer_allowed(_DB(_row("v2_active", revision=2)), "owner-1", "v2")
    with pytest.raises(HTTPException) as exc:
        await assert_writer_allowed(_DB(_row("v2_active", revision=2)), "owner-1", "legacy")
    assert exc.value.detail["code"] == "accounting_legacy_writer_disabled"

    for invalid in (_row("future"), _row("v2_active", revision=2, contract_revision=99)):
        with pytest.raises(HTTPException) as exc:
            await assert_writer_allowed(_DB(invalid), "owner-1", "v2")
        assert exc.value.detail["code"] == "accounting_transition_contract_invalid"

    partial = {"_id": "owner-1", "ledger_backend_state": "legacy_active"}
    with pytest.raises(HTTPException) as exc:
        await assert_writer_allowed(_DB(partial), "owner-1", "legacy")
    assert exc.value.detail["code"] == "accounting_transition_contract_invalid"


def test_financial_account_and_opening_schemas_are_explicit():
    account = AccountCreate(
        name="صندوق المتجر",
        account_type="cash",
        currency="sar",
        idempotency_key="account-key-0001",
    )
    assert account.account_type in ACCOUNT_TYPES
    schema = str(OpeningDraftCreate.model_json_schema())
    assert all(category in schema for category in OPENING_CATEGORIES)


def test_owner_setup_permissions_do_not_grant_financial_execution():
    owner = {"id": "owner-1", "role": "owner", "accounting_permissions": []}
    legacy = {
        "id": "employee-1",
        "role": "employee",
        "created_by": "owner-1",
        "accounting_permissions": ["accounting.opening_balances.approve"],
    }
    setup = {
        "accounting.financial_accounts.view", "accounting.financial_accounts.manage",
        "accounting.opening_balances.view", "accounting.opening_balances.drafts.manage",
        "accounting.opening_balances.review",
    }
    sensitive = {
        "accounting.opening_balances.post", "accounting.ledger_transition.manage",
        "accounting.journals.reverse", "accounting.shipping.contracts.review",
    }
    assert setup <= set(accounting_permissions_for_user(owner))
    assert not sensitive.intersection(accounting_permissions_for_user(owner))
    for permission in setup | sensitive:
        assert permission not in accounting_permissions_for_user(legacy)
        assert permission not in accounting_permissions_for_user({"role": "employee", "is_owner": True})
        employee = {**legacy, "accounting_permissions": [permission]}
        assert accounting_permissions_for_user(employee) == [permission]


def _opening_line(**overrides):
    payload = {
        "category": "financial_account",
        "financial_account_id": "bank-1",
        "label": "بنك الاختبار",
        "meaning": "available_to_us",
        "original_amount": "115.00",
        "original_currency": "SAR",
        "fx_rate_to_sar": "1",
        "evidence_file_id": "evidence-banks",
    }
    payload.update(overrides)
    return payload


def _opening_draft(**overrides):
    sections = {section: f"evidence-{section}" for section in EVIDENCE_SECTION_IDS}
    payload = {
        "idempotency_key": "opening-request-0001",
        "cutover_at": "2026-09-15T00:00:00+03:00",
        "cutover_timezone": "Asia/Riyadh",
        "cutover_evidence_file_id": "cutover-evidence",
        "section_evidence_file_ids": sections,
        "lines": [_opening_line(evidence_file_id=sections["banks_cash"])],
    }
    payload.update(overrides)
    return payload


def test_opening_money_and_fx_are_decimal_strings_with_sar_parity():
    with pytest.raises(ValidationError):
        OpeningLine.model_validate(_opening_line(original_amount=115.0))
    with pytest.raises(ValidationError):
        OpeningLine.model_validate(_opening_line(fx_rate_to_sar=1.0))
    with pytest.raises(ValidationError):
        OpeningLine.model_validate(_opening_line(fx_rate_to_sar="1.15"))

    foreign = OpeningLine.model_validate(_opening_line(
        original_currency="USD",
        fx_rate_to_sar="3.75",
        fx_at="2026-09-14T21:00:00Z",
        fx_source="Saudi Central Bank",
    ))
    assert str(foreign.original_amount) == "115.00"
    assert str(foreign.fx_rate_to_sar) == "3.75"


def test_opening_requires_exact_evidence_sections_and_riyadh_cutover():
    missing = _opening_draft()
    missing["section_evidence_file_ids"].pop(next(iter(EVIDENCE_SECTION_IDS)))
    with pytest.raises(ValidationError):
        OpeningDraftCreate.model_validate(missing)

    draft = OpeningDraftCreate.model_validate(_opening_draft())
    assert _draft_content(draft)["cutover_at"] == "2026-09-14T21:00:00.000000Z"

    naive = OpeningDraftCreate.model_validate(_opening_draft(cutover_at="2026-09-15T00:00:00"))
    with pytest.raises(HTTPException) as exc:
        _draft_content(naive)
    assert exc.value.detail["code"] == "opening_cutover_timezone_required"

    utc = OpeningDraftCreate.model_validate(_opening_draft(cutover_at="2026-09-14T21:00:00Z"))
    with pytest.raises(HTTPException) as exc:
        _draft_content(utc)
    assert exc.value.detail["code"] == "opening_cutover_must_use_asia_riyadh"


def test_frontend_and_backend_explicit_authority_policies_match():
    from pathlib import Path
    import re
    from accounting_module_contract import ACCOUNTING_EXPLICIT_GRANT_KEYS
    source = (Path(__file__).resolve().parents[2] / "frontend/src/pages/accounting/accountingPages.js").read_text(encoding="utf-8")
    block = source.split("ACCOUNTING_EXPLICIT_GRANT_PERMISSIONS = new Set([", 1)[1].split("]);", 1)[0]
    assert set(re.findall(r'"(accounting\.[^"]+)"', block)) == ACCOUNTING_EXPLICIT_GRANT_KEYS
