from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from accounting_settlement_currency_guard import (
    build_currency_guarded_create,
    currency_review_reasons,
    explicit_currency_register_item,
    install_accounting_settlement_currency_guard,
    normalize_settlement_currency,
    settlement_currency_from_file,
)


def test_currency_normalization_accepts_supported_sar_aliases():
    assert normalize_settlement_currency("SAR") == "SAR"
    assert normalize_settlement_currency("S.R.") == "SAR"
    assert normalize_settlement_currency("ر.س") == "SAR"
    assert normalize_settlement_currency("ريال سعودي") == "SAR"


def test_file_currency_is_explicit_and_unsupported_currency_fails_closed():
    assert settlement_currency_from_file({
        "provider": "tamara",
        "header": {"currency": "SAR"},
    }) == ("SAR", "header.currency")

    with pytest.raises(HTTPException) as exc:
        settlement_currency_from_file({
            "provider": "tabby",
            "header": {"currency": "USD"},
        })
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "unsupported_settlement_currency"
    assert exc.value.detail["currency"] == "USD"


def test_persisted_importer_provenance_is_preserved_on_the_draft():
    assert settlement_currency_from_file({
        "provider": "tabby",
        "currency": "SAR",
        "currency_source": "workbook.currency_column",
        "header": {"currency": "SAR"},
    }) == ("SAR", "workbook.currency_column")


def test_missing_parser_currency_uses_auditable_provider_contract_not_silent_default():
    currency, source = settlement_currency_from_file({
        "provider": "salla",
        "header": {},
    })
    assert currency == "SAR"
    assert source == "provider_contract_sar:salla"


def test_existing_drafts_without_explicit_supported_currency_are_blocked():
    assert currency_review_reasons({})[0]["code"] == "settlement_currency_missing"
    assert currency_review_reasons({"currency": "USD"})[0]["code"] == "settlement_currency_unsupported"
    assert currency_review_reasons({"currency": "SAR"}) == []


class _SettlementCollection:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update):
        self.calls.append((query, update))
        return SimpleNamespace(matched_count=1)


class _DB:
    def __init__(self):
        self.accounting_settlements_v2 = _SettlementCollection()


@pytest.mark.asyncio
async def test_guarded_create_persists_currency_and_provenance_on_draft():
    seen = {}

    async def original(
        db,
        *,
        owner_id,
        actor,
        file_doc,
        bank_account_id=None,
        notes=None,
    ):
        seen["file_doc"] = file_doc
        return {
            "id": "draft-1",
            "user_id": owner_id,
            "provider": file_doc["provider"],
            "source_snapshot": {"filename": "statement.xlsx"},
        }

    db = _DB()
    guarded = build_currency_guarded_create(original)
    result = await guarded(
        db,
        owner_id="owner-1",
        actor={"id": "user-1"},
        file_doc={"id": "file-1", "provider": "salla", "header": {}},
    )

    assert seen["file_doc"]["header"]["currency"] == "SAR"
    assert result["currency"] == "SAR"
    assert result["currency_source"] == "provider_contract_sar:salla"
    assert result["source_snapshot"]["currency"] == "SAR"
    assert db.accounting_settlements_v2.calls
    update = db.accounting_settlements_v2.calls[0][1]["$set"]
    assert update["currency"] == "SAR"
    assert update["source_snapshot.currency"] == "SAR"


def test_register_never_hides_a_missing_currency_as_sar():
    def legacy_register_item(_document):
        return {"id": "draft-1", "currency": "SAR"}

    missing = explicit_currency_register_item(legacy_register_item, {})
    supported = explicit_currency_register_item(legacy_register_item, {"currency": "SAR"})
    assert missing["currency"] is None
    assert missing["currency_supported"] is False
    assert supported["currency"] == "SAR"
    assert supported["currency_supported"] is True


def test_installer_combines_bank_and_currency_lifecycle_reasons():
    async def original_create(*_args, **_kwargs):
        return {"id": "draft"}

    routes_module = SimpleNamespace(_create_draft_from_file=original_create)
    lifecycle_module = SimpleNamespace(
        bank_match_review_reasons=lambda _draft: [{"code": "bank", "message": "bank"}],
    )
    register_module = SimpleNamespace(
        _register_item=lambda document: {"id": document.get("id"), "currency": "SAR"},
    )

    install_accounting_settlement_currency_guard(
        routes_module,
        lifecycle_module,
        register_module,
    )

    reasons = lifecycle_module.bank_match_review_reasons({})
    assert [reason["code"] for reason in reasons] == ["bank", "settlement_currency_missing"]
    assert register_module._register_item({"id": "x"})["currency"] is None
