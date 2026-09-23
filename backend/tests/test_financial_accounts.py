from __future__ import annotations

import pytest
from fastapi import HTTPException

from accounting_financial_accounts import (
    ACCOUNT_TYPES,
    OPENING_CATEGORIES,
    PERMISSIONS,
    AccountCreate,
    OpeningDraftCreate,
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
        self.mz2_writer_transition = _Rows(row)

    def __getitem__(self, _name):
        return self.mz2_writer_transition


def _row(state: str, *, revision: int = 1, contract_revision: int = 1):
    return {
        "_id": "owner-1",
        "user_id": "owner-1",
        "state": state,
        "state_revision": revision,
        "contract_revision": contract_revision,
        "activation_ref": "activation" if state == "v2_active" else None,
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


def test_new_permissions_are_never_implicit_for_owner_or_legacy_approve():
    owner = {"id": "owner-1", "role": "owner", "accounting_permissions": []}
    legacy = {
        "id": "employee-1",
        "role": "employee",
        "created_by": "owner-1",
        "accounting_permissions": ["accounting.opening_balances.approve"],
    }
    for permission in PERMISSIONS.values():
        assert permission not in accounting_permissions_for_user(owner)
        assert permission not in accounting_permissions_for_user(legacy)
