"""Customer-side payments and amounts due are separate from actual bank/custody movements."""
from copy import deepcopy

import pytest

from operational_balance_service import refresh, report
from operational_balance_store import read
from test_operational_balance_integration import NOW, START, run, started
from test_operational_balance_order_page_payment_flow import order_document


def tender(number, method, paid, remaining=None):
    doc = order_document(method, paid, remaining, number=number)
    raw = doc["raw_by_source"]["salla_direct"]
    raw["payment_methods"] = [{"payment_method": method, "amount": paid}]
    raw["payment_actions"]["refund_action"] = {"paid_amount": {"amount": paid, "currency": "SAR"}}
    return doc


def test_customer_payments_cover_all_scoped_orders_without_bank_or_cod_cash():
    async def scenario(db):
        await started(db)
        bank = tender("8110", "bank", "100.00")
        cod = tender("8111", "cash_on_delivery", "0")
        awaiting = tender("8112", "waiting", "0", "100")
        mixed = tender("8113", "tamara_installment", "100.00")
        mixed["raw_by_source"]["salla_direct"]["payment_methods"] = [
            {"payment_method":"tamara_installment", "amount":"85", "transaction_reference":"bnpl-verified"},
            {"payment_method":"customer_wallet", "amount":"15", "transaction_reference":"wallet-verified"},
        ]
        old = order_document("bank", "100", None, number="8114", created=START)
        future = order_document("bank", "100", None, number="8115", created="2026-10-08T12:00:00+00:00")
        foreign_owner = tender("8116", "bank", "100")
        foreign_owner["user_id"] = "other"
        await db.unified_orders.insert_many([bank, cod, awaiting, mixed, old, future, foreign_owner])
        before = await read(db, "owner")
        for _ in range(2):
            await refresh(db, "owner", clock=NOW)
            state = await read(db, "owner")
            projection = report(state)
            coverage = projection["details"]["customer_tender_coverage"]
            assert coverage["source_order_count"] == 4
            assert coverage["uninspected_source_orders"] == coverage["unknown_currency_orders"] == 0
            rows = {row["method"]:row for row in coverage["by_currency"]["SAR"]}
            assert {m:(r["orders"], r["paid"], r["remaining"]) for m,r in rows.items()} == {
                "bank":(1, "100.00", "0.00"), "cod":(1, "0.00", "100.00"),
                "awaiting":(1, "0.00", "100.00"), "wallet":(1, "15.00", "0.00"),
            }
            assert all(r["verified_orders"] == 1 and r["incomplete_orders"] == 0 for r in rows.values())
            assert state["openings"] == before["openings"] and state["movements"] == before["movements"]
            assert projection["summary"]["actual_liquidity"] == "1000.00"
            assert projection["details"]["provider_paid_totals"]["SAR"][0]["paid"] == "85.00"

        historical = deepcopy(state)
        del historical["engine"]["customer_tender_coverage"]
        assert "customer_tender_coverage" not in report(historical)["details"]
    run(scenario)


@pytest.mark.parametrize("fault", ["cross-currency", "cancelled", "wrong-paid", "unknown-method", "absent-paid"])
def test_incomplete_customer_amount_remains_unknown(fault):
    from operational_customer_tender_coverage import new_coverage, observe_nonprovider

    doc = tender("8119", "bank", "100.00")
    raw = doc["raw_by_source"]["salla_direct"]
    if fault == "cross-currency":
        raw["payment_methods"][0]["currency"] = "AED"
    elif fault == "cancelled":
        raw["status"] = {"slug":"cancelled"}
    elif fault == "wrong-paid":
        raw["payment_methods"][0]["amount"] = "40.00"
    elif fault == "unknown-method":
        raw["payment_methods"][0]["payment_method"] = raw["payment_method"] = "unrecognized"
    else:
        raw.pop("payment_actions")
    coverage = new_coverage(START, NOW)
    coverage.update(uninspected_source_orders=1, source_order_count=1)
    before = deepcopy(doc)
    observe_nonprovider(coverage, raw, doc, {"id":raw["id"],"currency":"SAR","order_number":doc["order_number"]},
                        raw["total"], None, "owner")
    row, = coverage["by_currency"]["SAR"]
    assert (row["orders"], row["verified_orders"], row["incomplete_orders"]) == (1, 0, 1)
    assert row["paid"] is row["remaining"] is None
    assert doc == before


def test_exact_foreign_paid_apportioned_once_to_multiple_native_tenders(monkeypatch):
    from decimal import Decimal
    from operational_customer_payment_sar import value_customer_payment
    from salla_paid_sar_evidence import matching_sar_payment
    monkeypatch.setattr(matching_sar_payment.__module__ + ".matching_sar_payment",
                        lambda source, raw, *, owner, native_paid: Decimal("175.57") if native_paid == Decimal("14.53") else None)
    raw = {"currency":"KWD", "total":{"amount":"14.53","currency":"KWD"},
           "exchange_rate":{"base_currency":"SAR","exchange_currency":"KWD","rate":"12.12194678"}}
    amounts = {"tabby":Decimal("7.26"), "wallet":Decimal("7.27")}
    observed = value_customer_payment(raw, {}, "owner", "KWD", amounts)
    assert observed["paid"] == "175.57" and observed["basis"] == "salla_list_paid"
    assert observed["shares"] == {"tabby":"87.72", "wallet":"87.85"}
    assert sum(map(Decimal, observed["shares"].values())) == Decimal("175.57")
    assert value_customer_payment(raw, {}, "owner", "KWD", dict(reversed(list(amounts.items())))) == observed


def test_conversion_rate_requires_correct_pair_and_never_overrides_a_conflicting_list_proof():
    from decimal import Decimal
    from operational_customer_payment_sar import value_customer_payment, remaining_sar
    payload = {"currency":"QAR", "total":{"amount":"30.00","currency":"QAR"},
               "exchange_rate":{"base_currency":"SAR","exchange_currency":"QAR","rate":"1.03"}}
    native = {"salla":Decimal("20.00")}
    result = value_customer_payment(payload, {}, "owner", "QAR", native)
    assert result["basis"] == "salla_order_fx" and result["paid"] == result["shares"]["salla"] == "20.60"
    assert remaining_sar(payload, "QAR", Decimal(10)) == Decimal("10.30")
    assert value_customer_payment(payload, {"salla_paid_sar_evidence":{}}, "owner", "QAR", native) is None
    payload["exchange_rate"] = {"base_currency":"QAR","exchange_currency":"SAR","rate":"1.03"}
    assert value_customer_payment(payload, {}, "owner", "QAR", native) is None
    assert remaining_sar(payload,"QAR",Decimal(10)) is None
    assert value_customer_payment(payload, {}, "owner", "QAR", {"cod":Decimal(0)})["paid"] == "0.00"


def test_unknown_foreign_payment_stays_unknown_and_requests_backfill():
    from operational_customer_tender_coverage import new_coverage, observe_nonprovider, finish_coverage
    doc = tender("8121", "bank", "100.00")
    raw = doc["raw_by_source"]["salla_direct"]
    raw["currency"] = raw["total"]["currency"] = "QAR"
    for action in raw["payment_actions"].values():
        action["paid_amount"]["currency"] = "QAR"
    order = {"id":raw["id"], "order_number":doc["order_number"], "currency":"QAR"}
    coverage = new_coverage(START, NOW)
    coverage.update(source_order_count=2, uninspected_source_orders=2)
    observe_nonprovider(coverage, raw, doc, order, raw["total"], None, "owner")
    finish_coverage(coverage, ["uninspected-id"])
    summary = coverage["sar"]
    assert summary["paid"] is None and not summary["complete"]
    assert summary["missing_orders"] == 2 and summary["known_paid"] == "0.00"
    assert summary["missing_order_ids"] == [order["id"], "uninspected-id"]
    assert summary["proof_lookups"] == [{"order_id":order["id"],"order_number":order["order_number"]}]
    bank, = summary["by_method"]
    assert bank["paid"] is None and bank["known_paid"] == "0.00" and bank["remaining"] == "0.00"
    assert "customer_payment_sar" not in order


@pytest.mark.parametrize(("method", "paid", "expected_due"), [
    ("bank", "100.00", "0.00"), ("cash_on_delivery", "0.00", "100.00"),
])
def test_exact_customer_payment_snapshot_survives_pending_fulfillment(method, paid, expected_due):
    from operational_customer_tender_coverage import new_coverage, observe_nonprovider, finish_coverage
    from salla_payment_collection import provider_order_page_current
    doc = tender("8130", method, paid)
    raw = doc["raw_by_source"]["salla_direct"]
    doc["g47_salla_snapshot"] = {"component_pending": True, "source_updated_at": raw["updated_at"]}
    order = {"id": raw["id"], "order_number": doc["order_number"], "currency": "SAR"}
    before = deepcopy(doc)
    coverage = new_coverage(START, NOW)
    coverage.update(source_order_count=1, uninspected_source_orders=1)
    observe_nonprovider(coverage, raw, doc, order, raw["total"], None, "owner")
    finish_coverage(coverage)
    assert coverage["sar"]["paid"] == paid and coverage["sar"]["missing_orders"] == 0
    assert coverage["sar"]["by_method"][0]["remaining"] == expected_due
    assert doc == before and "bank_credit" not in order
    assert not provider_order_page_current(doc, raw, owner="owner", order_number=doc["order_number"], provider_order_id=raw["id"])


@pytest.mark.parametrize("fault", ["owner", "order-id", "reference", "watermark", "status-version", "refresh", "cancelled", "amount", "future-version"])
def test_pending_fulfillment_never_masks_conflicting_payment_identity_or_amount(fault):
    from operational_customer_tender_coverage import new_coverage, observe_nonprovider, finish_coverage
    doc = tender("8131", "bank", "100.00")
    raw = doc["raw_by_source"]["salla_direct"]
    doc["g47_salla_snapshot"] = {"component_pending": True, "source_updated_at": raw["updated_at"]}
    order = {"id": raw["id"], "order_number": doc["order_number"], "currency": "SAR"}
    if fault == "owner":
        doc["user_id"] = "another-owner"
    elif fault == "order-id":
        doc["salla_order_id"] = "different-order"
    elif fault == "reference":
        doc["order_number"] = "different-reference"
    elif fault == "watermark":
        doc["g47_salla_snapshot"]["source_updated_at"] = START
    elif fault == "status-version":
        doc["last_salla_direct_status_source_updated_at"] = NOW
    elif fault == "refresh":
        doc["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    elif fault == "cancelled":
        doc["order_status_slug"] = "cancelled"
    elif fault == "future-version":
        raw["updated_at"] = doc["g47_salla_snapshot"]["source_updated_at"] = "2026-10-08T09:00:00Z"
    else:
        raw["payment_methods"][0]["amount"] = "50.00"
    coverage = new_coverage(START, NOW)
    coverage.update(source_order_count=1, uninspected_source_orders=1)
    observe_nonprovider(coverage, raw, doc, order, raw["total"], None, "owner")
    finish_coverage(coverage)
    assert coverage["sar"]["paid"] is None and coverage["sar"]["missing_orders"] == 1


@pytest.mark.parametrize(("bank_paid", "remaining", "paid"), [("85.00", "0.00", "100.00"), ("55.00", "30.00", "70.00")])
def test_verified_bank_wallet_split_reports_all_customer_money_once(bank_paid, remaining, paid):
    from operational_customer_tender_coverage import new_coverage, observe_nonprovider, finish_coverage
    doc = tender("8132", "bank", paid, remaining)
    raw = doc["raw_by_source"]["salla_direct"]
    raw["payment_methods"] = [
        {"payment_method": "bank", "amount": bank_paid, "transaction_reference": "bank-payment"},
        {"payment_method": "customer_wallet", "amount": "15.00", "transaction_reference": "wallet-payment"},
    ]
    doc["g47_salla_snapshot"] = {"component_pending": True, "source_updated_at": raw["updated_at"]}
    order = {"id": raw["id"], "order_number": doc["order_number"], "currency": "SAR"}
    coverage = new_coverage(START, NOW)
    coverage.update(source_order_count=1, uninspected_source_orders=1)
    observe_nonprovider(coverage, raw, doc, order, raw["total"], None, "owner")
    finish_coverage(coverage)
    assert coverage["sar"]["paid"] == paid and coverage["sar"]["missing_orders"] == 0
    methods = {row["method"]: row for row in coverage["sar"]["by_method"]}
    assert (methods["bank"]["paid"], methods["wallet"]["paid"]) == (bank_paid, "15.00")
    assert (methods["bank"]["remaining"], methods["wallet"]["remaining"]) == (remaining, "0.00")
    assert "bank_credit" not in order
