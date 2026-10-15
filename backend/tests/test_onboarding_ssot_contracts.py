"""Disposable contract checks; no financial system or production connection."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from accounting_onboarding_ssot import (
    FACTS, INVOICES, OBLIGATIONS, POLICIES, PREPAIDS, FeePolicyCreate,
    PrepaidSelection, TypedFactCreate, create_fee_policy, create_typed_fact,
    list_prepaid_candidates, list_typed_facts, prepaid_calculation,
    resolve_fee_policy, save_prepaid_selection, verify_selected_contracts,
)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, size):
        return deepcopy(self.rows[:size])


class Collection:
    def __init__(self):
        self.rows = []

    def find(self, query, projection=None):
        rows = [deepcopy(r) for r in self.rows if all(r.get(k) == v for k, v in query.items())]
        if projection and projection.get("_id") == 0:
            for row in rows:
                row.pop("_id", None)
        return Cursor(rows)

    async def find_one(self, query, projection=None):
        rows = await self.find(query, projection).to_list(1)
        await asyncio.sleep(0)  # Exercise competing CAS readers.
        return rows[0] if rows else None

    async def insert_one(self, row):
        if "_id" in row and any(r.get("_id") == row["_id"] for r in self.rows):
            raise DuplicateKeyError("duplicate")
        self.rows.append(deepcopy(row))

    async def update_one(self, query, change):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                for k, v in change.get("$push", {}).items():
                    row.setdefault(k, []).append(deepcopy(v))
                for k, v in change.get("$inc", {}).items():
                    row[k] += v
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class DB:
    allowed = {FACTS, INVOICES, OBLIGATIONS, POLICIES, PREPAIDS}

    def __init__(self):
        self.collections = {}

    def __getitem__(self, key):
        assert key in self.allowed, f"Legacy/financial access forbidden: {key}"
        return self.collections.setdefault(key, Collection())


def run(value):
    return asyncio.run(value)


def policy(**changes):
    values = dict(provider="salla", percentage="2.5", fixed_amount="1", vat_treatment="exclusive",
                  effective_from="2026-01-01", effective_to="2026-09-30", currency="SAR", evidence="contract-file")
    return FeePolicyCreate(**{**values, **changes})


def test_fee_exact_effective_selection_missing_and_legacy_ignored():
    db = DB()
    first = run(create_fee_policy(db, "o", "actor", policy()))
    second = run(create_fee_policy(db, "o", "actor", policy(effective_from="2026-10-01", effective_to=None)))
    assert run(resolve_fee_policy(db, "o", "salla", "2026-09-30"))["id"] == first["id"]
    assert run(resolve_fee_policy(db, "o", "salla", "2026-10-01"))["id"] == second["id"]
    for owner, provider in (("foreign", "salla"), ("o", "tabby")):
        with pytest.raises(HTTPException, match="provider_fee_policy_missing"):
            run(resolve_fee_policy(db, owner, provider, "2026-10-01"))
    with pytest.raises(ValidationError):
        policy(provider="Salla")


def test_overlapping_concurrent_fee_writers_have_one_winner():
    db = DB()
    async def race():
        return await asyncio.gather(*(create_fee_policy(db, "o", "actor", policy()) for _ in range(5)), return_exceptions=True)
    outcomes = run(race())
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert all(isinstance(outcome, dict) or outcome.detail["code"] == "provider_fee_policy_overlap" for outcome in outcomes)
    envelope = db[POLICIES].rows[0]
    envelope["policies"].append(deepcopy(envelope["policies"][0]))
    with pytest.raises(HTTPException, match="provider_fee_policy_ambiguous"):
        run(resolve_fee_policy(db, "o", "salla", "2026-09-30"))


def invoice(**changes):
    return {**dict(id="inv", user_id="o", obligation_id="subscription", payment_status="paid", paid_date="2026-08-21",
                   period_start="2026-08-21", period_end="2027-08-21", amount=3660), **changes}


def test_annual_prepaid_native_inclusive_contract_calendar_days():
    result = prepaid_calculation(invoice(), "2026-10-01")
    assert result["total_days"] == 366
    assert result["consumed_days"] == 41
    assert result["remaining_days"] == 325
    assert result["consumed_before_cutover"] == "410.00"
    assert result["remaining_prepaid_after_cutover"] == "3250.00"


@pytest.mark.parametrize("changes,cutover,reason", [
    ({"payment_status": "unpaid"}, "2026-10-01", "invoice_unpaid"),
    ({"paid_date": None}, "2026-10-01", "payment_date_missing"),
    ({"paid_date": "2026-10-01"}, "2026-10-01", "not_paid_before_cutover"),
    ({}, "2027-08-22", "coverage_ended_before_cutover"),
    ({"paid_date": None}, "2027-08-22", "coverage_ended_before_cutover"),
])
def test_non_prepaid_is_never_capitalized(changes, cutover, reason):
    assert prepaid_calculation(invoice(**changes), cutover) == {"eligible": False, "reason": reason} or (
        prepaid_calculation(invoice(**changes), cutover)["eligible"] is False
        and prepaid_calculation(invoice(**changes), cutover)["reason"] == reason)


def seeded():
    db = DB()
    db[OBLIGATIONS].rows.append(dict(id="subscription", user_id="o", title="Salla subscription",
        expense_type="subscription", entity_type="business", entity_id="business", entity_name="Business",
        status="active", auto_renew=True))
    db[INVOICES].rows.append(invoice())
    return db


def selection():
    return PrepaidSelection(obligation_id="subscription", invoice_id="inv", cutover_date="2026-10-01",
                            currency="SAR", evidence="invoice-file")


def test_prepaid_selection_persists_and_changed_source_blocks():
    db = seeded()
    row = run(save_prepaid_selection(db, "o", "actor", selection()))
    refreshed = run(list_prepaid_candidates(db, "o", "2026-10-01"))
    assert refreshed["items"][0]["selection"] == row
    assert refreshed["items"][0]["title"] == "Salla subscription"
    assert run(save_prepaid_selection(db, "o", "actor", selection()))["id"] == row["id"]
    db[INVOICES].rows[0]["amount"] = 5000
    assert run(list_prepaid_candidates(db, "o", "2026-10-01"))["blockers"][0]["code"] == "prepaid_source_changed"
    with pytest.raises(HTTPException, match="recurring_identity_unresolved"):
        run(save_prepaid_selection(db, "foreign", "actor", selection()))


def fact(category="accrued_expense", **changes):
    return TypedFactCreate(**{**dict(category=category, display_name="Documented balance", reference="document-1",
        amount="100.00", currency="SAR", cutover_date="2026-10-01", evidence="balance-file"), **changes})


def test_typed_liability_receivable_tax_facts_no_netting_no_deposit():
    db = DB()
    categories = ["accrued_expense", "other_payable", "other_receivable", "sales_vat_payable", "input_vat"]
    for category in categories:
        run(create_typed_fact(db, "o", "actor", fact(category)))
    rows = run(list_typed_facts(db, "o"))
    assert len(rows) == 5
    assert len({row["entity_id"] for row in rows}) == 5
    assert {row["sub_account"] for row in rows} == set(categories)
    assert sum(Decimal(row["amount"]) for row in rows) == 500
    assert run(list_typed_facts(db, "foreign")) == []
    for changes in ({"category": "deposit"}, {"evidence": ""}, {"amount": "-1"}, {"category": "prepaid_expense"}):
        with pytest.raises(ValidationError):
            fact(**changes)
    exceptional = run(create_typed_fact(db, "o", "actor", fact("prepaid_expense", manual_contract="Exceptional signed contract")))
    assert exceptional["category"] == "prepaid_expense"
    assert OBLIGATIONS not in db.collections


from decimal import Decimal


def test_review_requires_exact_selected_fact_amount_currency_evidence():
    db = DB()
    row = run(create_typed_fact(db, "o", "actor", fact()))
    line = dict(category=row["category"], entity_id=row["id"], meaning="owed_by_us",
                original_amount="100.00", original_currency="SAR", evidence_file_id="balance-file")
    verified = run(verify_selected_contracts(db, "o", "2026-10-01T00:00:00+03:00", [], [], [row["id"]], [line]))
    assert verified["blockers"] == []
    for key, value in (("original_amount", "200"), ("original_currency", "USD"), ("evidence_file_id", "other")):
        result = run(verify_selected_contracts(db, "o", "2026-10-01", [], [], [row["id"]], [{**line, key: value}]))
        assert result["blockers"][0]["code"] == "opening_line_typed_fact_mismatch"
    result = run(verify_selected_contracts(db, "o", "2026-10-01", [], [], [], [line]))
    assert result["blockers"][0]["code"] == "opening_line_typed_fact_required"


def test_missing_paid_prepaid_selection_and_explicit_zero_identity_blockers():
    db = seeded()
    result = run(verify_selected_contracts(db, "o", "2026-10-01", [], [], [], []))
    assert {row["code"] for row in result["blockers"]} == {
        "prepaid_currency_missing", "prepaid_evidence_missing", "prepaid_candidate_not_selected"}
    db = DB()
    row = run(create_typed_fact(db, "o", "actor", fact(amount="0")))
    line = dict(category=row["category"], entity_id=row["id"], meaning="zero",
                original_amount="0", original_currency="SAR", evidence_file_id="balance-file")
    assert run(verify_selected_contracts(db, "o", "2026-10-01", [], [], [row["id"]], [line]))["blockers"] == []
    assert run(verify_selected_contracts(db, "o", "2026-10-01", [], [], [], [line]))["blockers"][0]["code"] == "opening_line_typed_fact_required"


def test_current_active_fact_cannot_be_omitted_with_empty_opening_section():
    db = DB()
    row = run(create_typed_fact(db, "o", "actor", fact()))
    run(create_typed_fact(db, "foreign", "actor", fact()))
    run(create_typed_fact(db, "o", "actor", fact(cutover_date="2026-09-01")))
    result = run(verify_selected_contracts(db, "o", "2026-10-01", [], [], [], []))
    assert result["blockers"] == [{"code": "opening_fact_not_selected", "id": row["id"]}]
    result = run(verify_selected_contracts(db, "o", "2026-10-01", [], [], [row["id"]], []))
    assert result["blockers"] == [{"code": "selected_opening_fact_line_missing", "id": row["id"]}]


# Real router + disposable replica checks for the pause boundary and persistence.
from tests.test_accounting_onboarding import api, mongo_db, OWNER, prepare, request, action, fingerprint


@pytest.mark.asyncio
async def test_setup_http_during_pause_persists_refs_and_blocks_changed_source(api):
    session, sections, _ = await prepare(api)
    await api.db[OBLIGATIONS].insert_one({**seeded()[OBLIGATIONS].rows[0], "user_id": OWNER})
    await api.db[INVOICES].insert_one({**invoice(), "user_id": OWNER})
    allowed = ("mz2_onboarding_sessions", POLICIES, PREPAIDS, FACTS)
    before = await fingerprint(api, exclude=allowed)
    policy_row = await request(api, "POST", "/fee-policies", policy(effective_to=None).model_dump(mode="json"))
    assert policy_row["confirmed_by"] == "full"
    await request(api, "POST", "/fee-policies", policy(effective_to=None).model_dump(mode="json"), status=409)
    evidence = sections["equity"]["source_file_id"]
    prepaid = await request(api, "POST", "/prepaid-selections", {**selection().model_dump(mode="json"), "evidence": evidence})
    liability = await request(api, "POST", "/typed-facts", fact(evidence=evidence).model_dump(mode="json"))
    await request(api, "POST", "/typed-facts", fact(evidence=evidence).model_dump(mode="json"), user="viewer", status=403)
    lines = [dict(category="prepaid_expense", entity_id=prepaid["id"], meaning="available_to_us",
        original_amount="3250.00", original_currency="SAR", fx_rate_to_sar="1", evidence_file_id=evidence),
        dict(category="accrued_expense", entity_id=liability["id"], meaning="owed_by_us",
        original_amount="100.00", original_currency="SAR", fx_rate_to_sar="1", evidence_file_id=evidence)]
    session = await request(api, "PUT", f"/sessions/{session['id']}/sections/equity", {
        "version": session["version"], "idempotency_key": "typed-equity-session-save", "status": "complete",
        "evidence_file_id": evidence, "data": {"lines": lines,
        "prepaid_selection_ids": [prepaid["id"]], "typed_fact_ids": [liability["id"]]}})
    loaded = await request(api, "GET", f"/sessions/{session['id']}")
    assert loaded["sections"]["equity"]["data"]["prepaid_selection_ids"] == [prepaid["id"]]
    session = await action(api, session, "preview")
    session = await action(api, session, "review")
    await action(api, session, "opening-draft", status=423)
    assert await fingerprint(api, exclude=allowed) == before
    await api.db[INVOICES].update_one({"id": "inv", "user_id": OWNER}, {"$set": {"amount": 4000}})
    readiness = await request(api, "GET", f"/sessions/{session['id']}/readiness")
    assert not readiness["source_ready"]
    assert "prepaid_source_changed" in str(readiness)


@pytest.mark.asyncio
async def test_real_mongo_concurrent_overlaps_and_no_legacy_collection_access(mongo_db):
    outcomes = await asyncio.gather(*(create_fee_policy(mongo_db, OWNER, "full", policy()) for _ in range(8)), return_exceptions=True)
    assert sum(isinstance(row, dict) for row in outcomes) == 1
    assert all(isinstance(row, dict) or isinstance(row, HTTPException) and row.detail["code"] == "provider_fee_policy_overlap" for row in outcomes)
    assert await mongo_db[POLICIES].count_documents({"user_id": OWNER}) == 1
