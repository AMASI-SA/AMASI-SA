"""Regression evidence for the final owner clarifications; no production IO."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_engine import reconcile


START = "2026-10-05T00:00:00+00:00"
NOW = "2026-10-07T12:00:00+00:00"


def state(start=START):
    return {"status": "active", "started_at": start, "movements": []}


def test_verified_utc_partial_start_day_spend_is_not_dropped_by_riyadh_date():
    initial = state("2026-10-04T22:00:00+00:00")
    snapshot = {"account_id": "utc", "date": "2026-10-04", "timezone": "UTC",
                "observed_at": "2026-10-05T03:00:00+00:00", "amount": "140", "currency": "SAR",
                "funding_type": "postpaid", "covers_since_start": True, "closed": True,
                "complete": True, "day_ended": True}
    result = reconcile(initial, {"ad_snapshots": [snapshot]}, snapshot["observed_at"])
    assert result["engine"]["obligations"]["advertising:utc:2026-10-04"]["confirmed"] == "140.00"
    assert result["engine"]["issues"] == []
    assert reconcile(result, {"ad_snapshots": [snapshot]}, snapshot["observed_at"]) == result


@pytest.mark.parametrize("zone,before_midnight,midnight,two_am", [
    ("UTC", "2026-10-06T23:59:00+00:00", "2026-10-07T00:00:00+00:00", "2026-10-07T02:00:00+00:00"),
    ("Asia/Riyadh", "2026-10-06T20:59:00+00:00", "2026-10-06T21:00:00+00:00", "2026-10-06T23:00:00+00:00"),
])
def test_account_timezone_midnight_and_two_am_closing_boundaries(zone, before_midnight, midnight, two_am):
    snapshot = {"account_id": "a", "date": "2026-10-06", "timezone": zone, "amount": "450", "currency": "SAR",
                "funding_type": "postpaid", "closed": True, "complete": True, "day_ended": True}
    current = state()
    key = "advertising:a:2026-10-06"
    for observed in (before_midnight, midnight):
        current = reconcile(current, {"ad_snapshots": [{**snapshot, "observed_at": observed}]}, observed)
        assert current["engine"]["obligations"][key]["expected"] == "450.00"
        assert current["engine"]["obligations"][key]["confirmed"] == "0.00"
    current = reconcile(current, {"ad_snapshots": [{**snapshot, "observed_at": two_am}]}, two_am)
    assert current["engine"]["obligations"][key]["expected"] == "0.00"
    assert current["engine"]["obligations"][key]["confirmed"] == "450.00"
    assert reconcile(current, {"ad_snapshots": [{**snapshot, "observed_at": two_am}]}, two_am) == current


def test_unverified_partial_ad_start_stays_incomplete_and_previous_day_is_excluded():
    initial = state("2026-10-04T22:00:00+00:00")
    snapshot = {"account_id": "a", "date": "2026-10-04", "timezone": "UTC", "amount": "450", "currency": "SAR",
                "funding_type": "postpaid", "observed_at": NOW, "closed": True, "complete": True, "day_ended": True}
    output = reconcile(initial, {"ad_snapshots": [snapshot]}, NOW)
    assert output["engine"]["obligations"] == {}
    assert output["engine"]["issues"][0]["code"] == "advertising_start_day_coverage_required"
    output = reconcile(initial, {"ad_snapshots": [{**snapshot, "date": "2026-10-03", "covers_since_start": True}]}, NOW)
    assert output["engine"]["obligations"] == {}


def test_incomplete_close_does_not_finalize_zero_or_add_cumulative_observations():
    base = {"account_id": "a", "date": "2026-10-06", "timezone": "UTC", "currency": "SAR", "funding_type": "postpaid"}
    reads = [{**base, "amount": str(value), "observed_at": f"2026-10-06T{hour}:00:00+00:00"}
             for hour, value in ((10, 200), (11, 300), (12, 450))]
    current = reconcile(state(), {"ad_snapshots": reads}, NOW)
    assert current["engine"]["obligations"]["advertising:a:2026-10-06"]["expected"] == "450.00"
    current = reconcile(current, {"ad_snapshots": [{**base, "amount": "450", "observed_at": NOW, "closed": True, "complete": False, "day_ended": True}]}, NOW)
    assert current["engine"]["obligations"]["advertising:a:2026-10-06"]["confirmed"] == "0.00"


def test_supplier_net_residual_is_owner_requested_but_full_gross_is_confirmed():
    sources = {"orders": [{"id": "o", "created_at": "2026-10-06T08:00:00+00:00", "currency": "SAR", "status": "in_progress",
                            "items": [{"id": "piece", "cost": "100", "quantity": 1, "supplier_id": "s"}]}],
               "supplier_receipts": [{"id": "invoice:piece:product", "order_id": "o", "item_id": "piece", "supplier_id": "s",
                                      "amount": "92", "net_amount": "80", "tax_amount": "12", "gross_amount": "92", "accepted_at": NOW,
                                      "tax_evidence": {"confirmed": True, "evidence_file_id": "native-tax-proof"}}]}
    result = reconcile(state(), sources, NOW)
    rows = result["engine"]["obligations"]
    assert rows["supplier:o:piece"]["expected"] == "20.00"
    confirmed = rows["supplier:o:piece:receipt:invoice:piece:product"]
    assert (confirmed["net_amount"], confirmed["tax_amount"], confirmed["gross_amount"], confirmed["confirmed"]) == ("80.00", "12.00", "92.00", "92.00")
    assert reconcile(result, sources, NOW) == result
    sources["orders"][0]["status"] = "cancelled"
    cancelled = reconcile(result, sources, NOW)
    assert cancelled["engine"]["obligations"]["supplier:o:piece"]["expected"] == "0.00"
    assert cancelled["engine"]["obligations"][confirmed["id"]]["confirmed"] == "92.00"


@pytest.mark.parametrize("method,custody", [("cash", "300"), ("bank_transfer", "0"), ("card_terminal", "0")])
def test_driver_cod_custody_uses_native_method_and_clears_previous_expected(method, custody):
    order = {"id": "o", "created_at": "2026-10-06T08:00:00+00:00", "currency": "SAR", "items": [], "status": "in_progress",
             "carrier": {"id": "driver", "party_type": "store_driver", "cost": "20"}, "cod_amount": "300"}
    current = reconcile(state(), {"orders": [order]}, NOW)
    order.update(status="delivered", cod_amount=custody,
                 cod={"gross": "300", "collected": "300", "outstanding": "0", "custody_amount": custody,
                      "payment_method": method, "evidence_ids": ["collection"]})
    output = reconcile(current, {"orders": [order]}, NOW)
    obligation = output["engine"]["obligations"]["cod:o"]
    assert obligation["expected"] == "0.00"
    assert Decimal(obligation["confirmed"]) == Decimal(custody)
    assert obligation["payment_method"] == method
    assert output["engine"]["cod_reports"]["cod:o"]["gross"] == "300"
    assert output["movements"] == []
    assert reconcile(output, {"orders": [order]}, NOW) == output


def test_recurring_invoice_alone_is_expected_until_actual_payment_evidence():
    source = {"recurring": [{"id": "rent:2026-10-01:2026-10-31", "amount": "5000", "currency": "SAR", "party_id": "rent",
                             "party_type": "operating_expense", "due_at": "2026-10-05", "confirmed": True,
                             "invoice_id": "native-invoice", "period_start": "2026-10-01", "period_end": "2026-10-31"}]}
    key = "recurring:rent:2026-10-01:2026-10-31"
    first = reconcile(state(), source, NOW)
    assert (first["engine"]["obligations"][key]["expected"], first["engine"]["obligations"][key]["confirmed"]) == ("5000.00", "0.00")
    paid = deepcopy(first)
    paid["engine"]["facts"]["recurring_payment:paid:" + key] = {"obligation_id": key, "amount": "2000", "movement_id": "paid"}
    paid["movements"] = [{"id": "paid", "allocations": [{"obligation_id": key, "amount": "2000"}]}]
    paid = reconcile(paid, source, NOW)
    assert (paid["engine"]["obligations"][key]["expected"], paid["engine"]["obligations"][key]["confirmed"]) == ("3000.00", "2000.00")
    assert reconcile(paid, source, NOW) == paid
    assert len(paid["movements"]) == 1

def test_cod_survives_missing_fee_and_components_confirm_independently_once():
    order={'id':'o','created_at':'2026-10-06T01:00:00+00:00','status':'delivered','currency':'SAR','items':[],
           'carrier':{'id':'c','kind':'courier','cost':None,'fee_complete':False},'cod_amount':'200',
           'cod':{'gross':'300','collected':'100','outstanding':'200','custody_amount':'200'}}
    result=reconcile(state(),{'orders':[order]},NOW)
    assert result['engine']['obligations']['cod:o']['confirmed']=='200.00'
    assert 'shipping:o' not in result['engine']['obligations']
    order['carrier'].update(cost='10',fee_components=[{'id':'base_shipping','amount':'10','complete':True},
                                                   {'id':'cod_commission','amount':None,'complete':False}])
    result=reconcile(result,{'orders':[order]},NOW)
    assert result['engine']['obligations']['shipping:o:base_shipping']['confirmed']=='10.00'
    assert 'shipping:o:cod_commission' not in result['engine']['obligations']
    order['carrier']['fee_components'][1].update(amount='2',complete=True)
    result=reconcile(result,{'orders':[order]},NOW)
    assert result['engine']['obligations']['shipping:o:cod_commission']['confirmed']=='2.00'
    assert sum(Decimal(r['confirmed']) for r in result['engine']['obligations'].values() if r['kind']=='shipping')==Decimal('12')
    assert reconcile(result,{'orders':[order]},NOW)==result
    order['carrier']['id']='different-executor'
    order['carrier']['fee_components'][0]['amount']='999'
    result=reconcile(result,{'orders':[order]},NOW)
    assert result['engine']['obligations']['shipping:o:base_shipping']['party_id']=='c'
    assert result['engine']['obligations']['shipping:o:base_shipping']['confirmed']=='10.00'

    assert sum(Decimal(r['confirmed']) for r in result['engine']['obligations'].values() if r['kind']=='shipping')==Decimal('12')
