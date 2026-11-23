"""Automatic bank recovery retains proved business time, separate from recording."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from operational_bank_credit_recognition import BankCreditRecognition
from operational_bank_order_milestone import HISTORY_MISSING, HISTORY_UNPROVEN, PROOF_FIELD, history_proof
from operational_bank_receipt_rollout import initialize_admission
import operational_balance_service as service
import operational_balance_store as store
import test_operational_balance_bank_refresh_plan as refresh_fixture


NOW = "2026-11-24T12:00:00+00:00"
CREATED = "2026-11-20T20:00:00+00:00"
EVENT_AT = "2026-11-20T22:30:00+00:00"  # 21 November in Riyadh.
UPDATED = "2026-11-23T12:00:00+00:00"
TIME_UNPROVEN = "operational_order_bank_occurrence_unproven"


def bank_source(*, with_history=True):
    raw = {"id": "a", "reference_id": "A", "date": CREATED, "updated_at": UPDATED,
           "status": "in_progress", "payment_method": "تحويل بنكي مصرف تجريبي",
           "currency": "SAR", "total": {"amount": "10.00", "currency": "SAR"}}
    source = {"user_id": "owner", "order_number": "A", "salla_order_id": "a",
              "raw_by_source": {"salla_direct": raw}, "g47_salla_snapshot": {"source_updated_at": UPDATED}}
    if with_history:
        source[PROOF_FIELD] = history_proof(raw, source, [{
            "id": "original-bank-admission", "type": "status_changed", "new_status": "in_progress",
            "created_at": EVENT_AT}], owner="owner", order_number="A", provider_order_id="a", as_of=NOW)
    return source


def bank_catalog():
    label = "مصرف تجريبي"
    key = hashlib.sha256(json.dumps(["owner", "salla.payment_method_bank", label],
                                    ensure_ascii=False).encode()).hexdigest()
    return {"mz2_bank_transfer_bindings": [{"_id": key, "user_id": "owner", "status": "active",
        "upstream_source": "salla.payment_method_bank", "upstream_value": label, "confirmed": True,
        "identity_contract_version": 1, "bank_account_source": "mz2_financial_accounts",
        "financial_account_id": "bank"}],
        "mz2_financial_accounts": [{"id": "bank", "account_type": "bank", "status": "active",
                                    "currency": "SAR", "name": "Synthetic Bank"}]}


async def candidate(db, source):
    state = db.data[store.STATES]["owner"]
    initialize_admission(state, state["started_at"])
    raw = source["raw_by_source"]["salla_direct"]
    order = {"id": "a", "order_number": "A", "created_at": CREATED, "status": "in_progress", "currency": "SAR"}
    reader = BankCreditRecognition(db, "owner", state, bank_catalog(), NOW)
    credit = await reader.candidate(raw, source, order, raw["payment_method"], raw["total"],
                                    Decimal("10.00"), None, [], [], False)
    return {**order, "bank_credit": credit}


async def recovered(monkeypatch, db, orders):
    monkeypatch.setattr(refresh_fixture, "NOW", NOW)
    refresh_fixture.isolate_sources(monkeypatch, orders)
    return await refresh_fixture.visible_refresh(db)


def test_delayed_bank_recovery_keeps_original_day_and_current_audit_then_replays_once(monkeypatch):
    async def scenario():
        db = refresh_fixture.CountedDB()
        order = await candidate(db, bank_source())
        assert order["bank_credit"]["occurred_at"] == EVENT_AT
        result = await recovered(monkeypatch, db, [order])
        movement, = result["movements"]
        assert movement["occurred_at"] == EVENT_AT
        assert movement["recorded_at"] == NOW
        assert movement["bank_preparation_evidence"]["source"] == "salla_order_history"
        assert result["audit"][0]["at"] == NOW
        statement = service.account_statement(result, {"kind": "bank", "id": "bank", "currency": "SAR", "name": "Bank"})
        assert statement["items"][0]["business_date"] == "2026-11-21"
        assert statement["items"][0]["occurred_at"] == EVENT_AT
        repeated = await recovered(monkeypatch, db, [order])
        assert repeated["movements"] == [movement]
        assert len(refresh_fixture.claim_rows(db)) == len(repeated["audit"]) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("bad_proof,code", [(None, HISTORY_MISSING), ("foreign", HISTORY_UNPROVEN)])
def test_snapshot_update_time_never_substitutes_for_missing_or_foreign_original_event(monkeypatch, bad_proof, code):
    async def scenario():
        db = refresh_fixture.CountedDB()
        source = bank_source(with_history=bad_proof is not None)
        if bad_proof:
            source[PROOF_FIELD]["owner"] = "another-owner"
        order = await candidate(db, source)
        result = await recovered(monkeypatch, db, [order])
        assert result["movements"] == []
        assert refresh_fixture.claim_rows(db) == [] and result["audit"] == []
        issue, = result["engine"]["issues"]
        assert issue["code"] == code
        assert (issue["source_id"], issue["order_number"], issue["party_type"], issue["party_id"], issue["status"]) == (
            "a", "A", "bank", "bank", "pending")
        assert issue["required_action"]
    asyncio.run(scenario())


@pytest.mark.parametrize("automatic", [False, True])
def test_existing_manual_or_automatic_match_is_untouched_when_original_time_is_unavailable(monkeypatch, automatic):
    async def scenario():
        db = refresh_fixture.CountedDB()
        saved = {"id": "existing", "request_id": "order-bank:a" if automatic else "manual-bank-a",
            "order_number": "A", "direction": "incoming", "bank_id": "bank", "amount": "10.00",
            "party_type": "bank", "party_id": "bank", "currency": "SAR", "kind": "collection",
            "occurred_at": "2026-11-21T08:00:00+00:00", "automatic_order_bank": automatic}
        db.data[store.STATES]["owner"]["movements"] = [deepcopy(saved)]
        order = await candidate(db, bank_source(with_history=False))
        result = await recovered(monkeypatch, db, [order])
        assert result["movements"] == [saved]
        assert result["audit"] == result["engine"]["issues"] == []
        assert refresh_fixture.claim_rows(db) == []
    asyncio.run(scenario())


@pytest.mark.parametrize("already_recorded", [False, True])
def test_order_history_cannot_date_a_later_mixed_bank_tender(monkeypatch, already_recorded):
    from test_operational_balance_bank_tender_proof import order as tender

    async def scenario():
        db = refresh_fixture.CountedDB()
        state = db.data[store.STATES]["owner"]
        initialize_admission(state, state["started_at"])
        source = bank_source()
        raw = tender(True)
        raw.update(id="a", reference_id="A", date=CREATED, updated_at=UPDATED)
        source["raw_by_source"]["salla_direct"] = raw
        order = {"id": "a", "order_number": "A", "created_at": CREATED, "status": "in_progress", "currency": "SAR"}
        reader = BankCreditRecognition(db, "owner", state, {}, NOW)
        reader._bank = AsyncMock(return_value={"id": "bank", "name": "Bank"})
        order["bank_credit"] = await reader.candidate(raw, source, order, raw["payment_method"],
            raw["amounts"]["total"], Decimal("1464.81"), None, [], [], False)
        assert order["bank_credit"]["amount"] == "150.66"
        if already_recorded:
            state["movements"] = [{"id": "saved", "request_id": "order-bank:a", "order_number": "A",
                "direction": "incoming", "bank_id": "bank", "party_type": "bank", "party_id": "bank",
                "amount": "150.66", "currency": "SAR", "automatic_order_bank": True, "occurred_at": EVENT_AT}]
        saved = deepcopy(state["movements"])
        result = await recovered(monkeypatch, db, [order])
        assert result["movements"] == saved and refresh_fixture.claim_rows(db) == []
        if already_recorded:
            assert result["engine"]["issues"] == []
        else:
            issue, = result["engine"]["issues"]
            assert issue["code"] == TIME_UNPROVEN and issue["status"] == "pending"
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["missing", "future", "before_start", "observation", "mismatch", "naive"])
def test_new_claim_refuses_unproved_original_time_before_financial_claim(monkeypatch, change):
    async def scenario():
        db = refresh_fixture.CountedDB()
        order = await candidate(db, bank_source())
        credit = order["bank_credit"]
        credit["occurred_at"] = EVENT_AT
        if change == "missing":
            credit.pop("occurred_at")
        elif change == "future":
            credit["occurred_at"] = credit["preparation_evidence"]["observed_at"] = "2026-11-25T00:00:00+00:00"
        elif change == "before_start":
            credit["occurred_at"] = credit["preparation_evidence"]["observed_at"] = "2026-10-01T00:00:00+00:00"
        elif change == "observation":
            credit["preparation_evidence"]["source"] = "salla_order_status"
        elif change == "mismatch":
            credit["occurred_at"] = "2026-11-22T12:00:00+00:00"
        else:
            credit["occurred_at"] = credit["preparation_evidence"]["observed_at"] = "2026-11-20T22:30:00"
        result = await recovered(monkeypatch, db, [order])
        assert result["movements"] == [] and refresh_fixture.claim_rows(db) == []
        assert result["audit"] == []
        assert result["engine"]["issues"][0]["code"] == TIME_UNPROVEN
    asyncio.run(scenario())
