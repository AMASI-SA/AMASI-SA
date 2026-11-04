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
