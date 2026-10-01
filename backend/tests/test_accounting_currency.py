"""Currency input validation, without changing opening valuation semantics."""
import pytest
from pydantic import ValidationError
from accounting_financial_accounts import AccountCreate, OpeningLine
from accounting_onboarding_contract import SectionSave


def account(currency):
    return AccountCreate(name="Synthetic account", account_type="bank", currency=currency,
                         idempotency_key="currency-test-001")


def line(**overrides):
    return OpeningLine(category="prepaid_expense", entity_id="synthetic",
                       meaning="available_to_us", original_amount="12.00",
                       evidence_file_id="synthetic-evidence", **overrides)


@pytest.mark.parametrize("currency", ["ZZZ", "AAA", "US", "USDD", "", None, 123, " SAR "])
def test_unlisted_currency_rejected_at_all_entry_boundaries(currency):
    with pytest.raises(ValidationError):
        account(currency)
    with pytest.raises(ValidationError):
        line(original_currency=currency)
    with pytest.raises(ValidationError):
        SectionSave(version=1, idempotency_key="currency-section-001", status="incomplete",
                    data={"lines": [{"original_currency": currency}]})


def test_sar_default_parity_and_existing_foreign_evidence_requirement():
    assert line().original_currency == "SAR"
    assert line().fx_rate_to_sar == 1
    with pytest.raises(ValidationError, match="opening_sar_fx_rate_must_equal_one"):
        line(fx_rate_to_sar="3.75")
    with pytest.raises(ValidationError, match="opening_fx_timestamp_required"):
        line(original_currency="USD", fx_rate_to_sar="3.75")
    value = line(original_currency="USD", fx_rate_to_sar="3.75",
                 fx_at="2026-09-30T00:00:00Z", fx_source="Synthetic evidence")
    assert str(value.fx_rate_to_sar) == "3.75"


def test_partial_setup_may_omit_currency_but_not_invent_one():
    payload = SectionSave(version=1, idempotency_key="currency-partial-001",
                          status="incomplete", data={"lines": [{"label": "Partial"}]})
    assert "original_currency" not in payload.data.lines[0]


def test_pinned_current_iso_vocabulary_and_case_compatibility():
    from accounting_currency import CURRENCY_CATALOG, CURRENCY_CODES
    assert CURRENCY_CATALOG["published"] == "2026-09-17"
    assert len(CURRENCY_CODES) == 178
    assert {"SAR", "USD", "EUR", "ZWG", "XCG"} <= CURRENCY_CODES
    assert "ZWL" not in CURRENCY_CODES
    for code in CURRENCY_CODES:
        assert account(code).currency == code
    assert account("sar").currency == "SAR"


@pytest.mark.asyncio
async def test_financial_account_v2_currency_remains_authoritative():
    from types import SimpleNamespace
    from fastapi import HTTPException
    from unittest.mock import AsyncMock, Mock
    from accounting_financial_accounts import OpeningDraftCreate, _compile_opening, EVIDENCE_SECTION_IDS
    rows = SimpleNamespace(to_list=AsyncMock(return_value=[{
        "id": "synthetic-usd", "account_type": "bank", "currency": "USD", "status": "active"
    }]))
    db = SimpleNamespace(mz2_financial_accounts=SimpleNamespace(find=Mock(return_value=rows)))
    draft = OpeningDraftCreate(idempotency_key="currency-draft-001",
        cutover_at="2026-10-01T00:00:00+03:00", cutover_evidence_file_id="synthetic-cutover",
        section_evidence_file_ids={key: "synthetic-" + key for key in EVIDENCE_SECTION_IDS},
        lines=[{"category": "financial_account", "financial_account_id": "synthetic-usd",
                "meaning": "available_to_us", "original_amount": "12.00", "original_currency": "SAR",
                "evidence_file_id": "synthetic-banks_cash"}])
    with pytest.raises(HTTPException) as error:
        await _compile_opening(db, owner="synthetic-owner", payload=draft)
    assert error.value.detail["code"] == "opening_account_currency_mismatch"


@pytest.mark.asyncio
async def test_invalid_currency_returns_http_422_before_request_handler():
    # Exercise FastAPI's request validation with the same public body models.
    # No database, fake currency source, or financial write is involved.
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    app = FastAPI()
    entered = []

    @app.post("/account")
    async def create_account(payload: AccountCreate):
        entered.append("account")
        return payload

    @app.put("/section")
    async def save_section(payload: SectionSave):
        entered.append("section")
        return payload

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/account", json={"name": "Synthetic account",
            "account_type": "bank", "currency": "ZZZ", "idempotency_key": "http-currency-001"})
        assert result.status_code == 422
        result = await client.put("/section", json={"version": 1,
            "idempotency_key": "http-currency-002", "status": "incomplete",
            "data": {"lines": [{"original_currency": "ZZZ"}]}})
        assert result.status_code == 422
    assert entered == []
