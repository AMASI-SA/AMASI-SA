"""Carrier snapshot totals never derive from a visible ledger page."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_service import report
from operational_balance_store import STATES, initial, read
from operational_workspace_courier_summary import courier_summary
from tests.test_operational_balance_workspace import BASE, grant, resource, run


def carrier_resource(**access):
    return {"kind": "courier", "id": "aramex", "currency": "SAR", "name": "أرامكس",
            "access": {"balance": True, "movements": "all", **access}}


def claim(identity, kind, confirmed, *, expected="0.00", **metadata):
    return {"id": identity, "kind": kind, "party_type": "courier", "party_id": "aramex",
            "currency": "SAR", "direction": "receivable" if kind == "cod" else "payable",
            "confirmed": confirmed, "expected": expected, "business_date": "2026-11-02",
            "name": "أرامكس", "order_id": identity, "order_number": identity, **metadata}


def movement(identity, direction, amount, allocations=(), **metadata):
    return {"id": identity, "kind": "collection" if direction == "incoming" else "payment",
            "party_type": "courier", "party_id": "aramex", "name": "أرامكس", "currency": "SAR",
            "direction": direction, "amount": amount, "bank_kind": "bank", "bank_id": "bank",
            "bank_name": "البنك", "actor_id": "owner", "business_date": "2026-11-10",
            "occurred_at": "2026-11-10T12:00:00+00:00", "allocations": list(allocations), **metadata}


def fixture():
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00", revision=41)
    rows = [claim("cod:one", "cod", "1000.17"), claim("shipping:one", "shipping", "100.12"),
            claim("return:one", "shipping", "25.13", return_case_id="accepted-return"),
            claim("cod:future", "cod", "0.00", expected="300.19"),
            claim("shipping:future", "shipping", "0.00", expected="20.11"),
            claim("return:future", "shipping", "0.00", expected="15.15", salla_return_shipment_id="reverse-2"),
            claim("shipping:foreign", "shipping", "99999.00", party_id="foreign"),
            claim("shipping:usd", "shipping", "88888.00", currency="USD")]
    state["engine"] = {"as_of": "2026-11-20T12:00:00+00:00", "facts": {},
                       "obligations": {r["id"]: r for r in rows}}
    state["movements"] = [
        movement("cash-receipt", "incoming", "400.07", [{"obligation_id": "cod:one", "amount": "400.07"}]),
        movement("prepaid-fee", "outgoing", "50.02", [{"obligation_id": "shipping:one", "amount": "50.02"}],
                 automatic_prepaid_shipping=True),
    ]
    return state


def summary(state, **access):
    return courier_summary(state, report(state), carrier_resource(**access))


def test_exact_snapshot_totals_return_proof_currency_and_prepaid_allocation_without_writes():
    state = fixture()
    before = deepcopy(state)
    result = summary(state)
    assert {key: result[key] for key in ("shipping_confirmed", "return_confirmed", "fees_confirmed", "fees_paid",
            "fees_outstanding", "cod_confirmed", "cod_received", "cod_outstanding", "expected_shipping",
            "expected_return", "expected_cod", "receivable", "payable", "net_position")} == {
        "shipping_confirmed": "100.12", "return_confirmed": "25.13", "fees_confirmed": "125.25",
        "fees_paid": "50.02", "fees_outstanding": "75.23", "cod_confirmed": "1000.17",
        "cod_received": "400.07", "cod_outstanding": "600.10", "expected_shipping": "20.11",
        "expected_return": "15.15", "expected_cod": "300.19", "receivable": "600.10",
        "payable": "75.23", "net_position": "524.87"}
    assert result["complete"] and result["revision"] == 41
    assert result["cash_received"] == "400.07" and result["cash_paid"] == "50.02"
    assert result["customer_collection_confirmed"] is None
    assert result["customer_collection_status"] == "carrier_collection_not_verified"
    assert result["scope"] == "saved_snapshot_to_date" and result["source_coverage"] == "saved_snapshot_only"
    assert state == before


@pytest.mark.parametrize("marker", ["return_case_id", "salla_return_shipment_id"])
def test_each_canonical_return_identity_marks_reverse_cost_once(marker):
    state = fixture()
    row = state["engine"]["obligations"]["return:one"]
    row.pop("return_case_id")
    row[marker] = "proof"
    row["return_confirmed_manually"] = True
    assert summary(state)["return_confirmed"] == "25.13"


@pytest.mark.parametrize("direction,fields", [
    ("incoming", ("cod_received", "cod_outstanding")),
    ("outgoing", ("fees_paid", "fees_outstanding")),
])
def test_legacy_account_settlement_retains_authoritative_balance_but_unknown_claim_attribution(direction, fields):
    state = fixture()
    state["movements"].append(movement("account-settlement", direction, "20.00", settlement_scope="account"))
    result = summary(state)
    assert all(result[key] is None for key in fields)
    assert "carrier_account_settlement_unallocated" in result["reason_codes"]
    assert result["complete"] is False
    assert result["receivable"] == ("580.10" if direction == "incoming" else "600.10")
    assert result["payable"] == ("75.23" if direction == "incoming" else "55.23")
    assert result["cod_confirmed"] == "1000.17" and result["fees_confirmed"] == "125.25"


def test_advance_is_not_silently_allocated_to_cod_and_opening_is_not_shipping():
    state = fixture()
    state["movements"].append(movement("advance", "incoming", "12.34"))
    state["openings"]["carrier-start"] = {
        "id": "carrier-start", "party_type": "courier", "party_id": "aramex", "currency": "SAR",
        "direction": "for_party", "amount": "70.00", "name": "أرامكس"}
    result = summary(state)
    assert result["unallocated_received"] == "12.34"
    assert result["cod_received"] == "400.07" and result["cod_outstanding"] == "600.10"
    assert result["opening_payable"] == "70.00" and result["fees_confirmed"] == "125.25"
    assert result["payable"] == "157.57"


def test_account_correction_and_mixed_estimate_are_unknown_not_duplicate_or_zero():
    state = fixture()
    state["movements"].append(movement("adjust", "incoming", "1.00", kind="correction", bank_id=None))
    state["engine"]["obligations"]["shipping:one"]["expected"] = "100.12"
    result = summary(state)
    assert result["expected_shipping"] is None and result["expected_return"] == "15.15"
    assert all(result[key] is None for key in ("cod_received", "cod_outstanding", "fees_paid", "fees_outstanding"))
    assert set(result["reason_codes"]) == {"carrier_account_adjustment_unallocated", "carrier_fee_forecast_overlap"}


def test_prior_overpayment_becomes_separate_credit_not_double_settled_gross():
    state = fixture()
    state["engine"]["obligations"]["cod:one"]["confirmed"] = "350.00"
    state["engine"]["obligations"]["shipping:one"]["confirmed"] = "40.00"
    result = summary(state)
    assert result["cod_excess_received"] == "50.07" and result["fees_overpaid"] == "10.02"
    assert result["other_payable"] == "50.07" and result["other_receivable"] == "10.02"
    assert Decimal(result["cod_confirmed"]) == Decimal(result["cod_received"]) + Decimal(result["cod_outstanding"])
    assert Decimal(result["fees_confirmed"]) == Decimal(result["fees_paid"]) + Decimal(result["fees_outstanding"])
    assert result["cash_received"] == "400.07" and result["cash_paid"] == "50.02"


def pending_issue(party="aramex"):
    return {"code": "operational_delivery_timestamp_missing", "source_id": "pending", "currency": "SAR",
            "courier_fee_review": {"party_type": "courier", "party_id": party, "order_id": "pending", "currency": "SAR"}}


@pytest.mark.parametrize("issue", [pending_issue(), {"code": "operational_source_timeout"}, {"code": "shipping_setup_ambiguous"}])
def test_missing_carrier_evidence_cannot_claim_complete_zero_balance(issue):
    state = fixture()
    state["engine"].update(obligations={}, issues=[issue])
    state["movements"] = []
    result = summary(state)
    assert result["complete"] is False and "carrier_source_incomplete" in result["reason_codes"]
    assert result["fees_confirmed"] is None and result["payable"] is None and result["net_position"] is None


def test_scoped_pending_evidence_preserves_known_subtotals_without_leaking_other_party_details():
    state = fixture()
    state["engine"]["issues"] = [pending_issue()]
    result = summary(state)
    assert not result["complete"] and result["review_order_count"] == 1
    assert result["fees_confirmed"] == "125.25" and result["payable"] == "75.23"
    state["engine"]["issues"] = [pending_issue("other-carrier")]
    foreign = summary(state)
    assert foreign["complete"] and foreign["review_order_count"] == 0
    assert "other-carrier" not in str(foreign)
    state["engine"]["issues"] = [pending_issue(None)]
    unassigned = summary(state)
    assert not unassigned["complete"] and unassigned["unassigned_source_review"]
    assert unassigned["review_order_count"] == 0


@pytest.mark.parametrize("access", [{"balance": False}, {"movements": "own"}, {"movements": "none"}])
def test_aggregate_totals_require_balance_and_all_movement_permissions(access):
    assert summary(fixture(), **access) is None


@pytest.mark.parametrize("change", ["inactive", "missing_as_of"])
def test_unavailable_snapshot_does_not_look_like_zero(change):
    state = fixture()
    if change == "inactive":
        state["status"] = "draft"
    else:
        state["engine"].pop("as_of")
    result = summary(state)
    assert not result["complete"] and result["fees_confirmed"] is None and result["receivable"] is None


async def seed_carrier(db):
    state = fixture()
    await db[STATES].replace_one({"_id": "owner"}, state)
    await db.mz2_shipping_setup_v2.insert_one({"user_id": "owner", "couriers": [
        {"courier_key": "aramex", "name": "أرامكس", "confirmed_by": "owner",
         "confirmed_at": "2026-11-01T00:00:00+00:00"}]})


def test_ledger_summary_is_same_for_cached_pages_filters_and_period_and_never_mutates_snapshot():
    async def scenario(db, client):
        await seed_carrier(db)
        before = await read(db, "owner")
        summaries = []
        for params in ({}, {"page": 2, "page_size": 1}, {"q": "no-match"},
                       {"date_from": "2026-12-01", "date_to": "2026-12-02"},
                       {"page_only": True, "courier_history": True, "page_size": 1},
                       {"page_only": True, "courier_history": True, "page_size": 1, "page": 2}):
            response = await client.get(BASE + "/ledger/courier/aramex", params=params)
            assert response.status_code == 200, response.text
            summaries.append(response.json()["courier_summary"])
        assert all(item == summaries[0] for item in summaries)
        assert summaries[0]["revision"] == before["revision"] and summaries[0]["cod_received"] == "400.07"
        assert await read(db, "owner") == before
        assert await db.general_ledger.count_documents({}) == 0
        # A new snapshot invalidates the fast-path cache as one coherent result.
        await db[STATES].update_one({"_id": "owner"}, {"$inc": {"revision": 1},
            "$set": {"engine.as_of": "2026-11-21T12:00:00+00:00"}})
        updated = await client.get(BASE + "/ledger/courier/aramex", params={"page_only": True, "courier_history": True})
        assert updated.json()["courier_summary"]["revision"] == 42
        assert updated.json()["courier_summary"]["as_of"] == "2026-11-21T12:00:00+00:00"
    run(scenario)


@pytest.mark.parametrize("balance_visible,scope", [(False, "all"), (True, "own"), (True, "all")])
def test_http_totals_cannot_bypass_resource_permissions(balance_visible, scope):
    async def scenario(db, client):
        await seed_carrier(db)
        await grant(db, operations=[], resources=[resource("courier", "aramex", use=False,
            balance=balance_visible, movements=scope)])
        for params in ({}, {"page_only": True, "courier_history": True}):
            response = await client.get(BASE + "/ledger/courier/aramex", params=params, headers={"x-test-user": "staff"})
            assert response.status_code == 200, response.text
            result = response.json()["courier_summary"]
            assert (result is not None) == (balance_visible and scope == "all")
        hidden = await client.get(BASE + "/ledger/courier/foreign", headers={"x-test-user": "staff"})
        assert hidden.status_code == 404
    run(scenario)


def test_ad_platform_is_safe_identity_metadata_from_visible_catalog_not_external_account_secrets():
    from tests.test_operational_balance_integration import seed_ad_account

    async def scenario(db, client):
        await seed_ad_account(db)
        await grant(db, operations=[], resources=[resource("ad_account", "ad", use=False, movements="own")])
        for params in ({}, {"directory_only": True}):
            response = await client.get(BASE + "/context", params=params, headers={"x-test-user": "staff"})
            assert response.status_code == 200, response.text
            rows = response.json()["resources"]
            assert len(rows) == 1 and rows[0]["platform"] == "meta" and rows[0]["balance"] is None
            assert "platform_account_id" not in rows[0] and "integration_account_id" not in rows[0]
        owner = (await client.get(BASE + "/context", params={"directory_only": True})).json()
        assert all("platform" not in row for row in owner["resources"] if row["kind"] != "ad_account")
    run(scenario)
