"""Operational-only courier recognition at the owner's real cutover.

Dated deliveries of previously created orders remain eligible. Never import
pre-opening sales, payments or supplier expenses, and never duplicate old fees.
"""
from copy import deepcopy
from decimal import Decimal

from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources
from test_operational_balance_delivery_cutover import active, courier_data, fee_total
from test_operational_balance_sources import HardBoundaryDB, run

OWNER_START = "2026-10-03T00:00:00+03:00"
BEFORE_SNAPSHOT = "2026-11-20T12:00:00+00:00"
DELIVERY = "2026-11-22T12:00:00+00:00"
LATEST = "2026-11-26T20:36:00+00:00"
BEFORE_START_DELIVERY = "2026-10-02T12:00:00+00:00"


def _data(*, delivered_at=DELIVERY, missing_date=False):
    data = courier_data()
    row = data["unified_orders"][0]
    raw = row["raw_by_source"]["salla_direct"]
    raw["updated_at"] = LATEST
    raw["status_changed_at"] = delivered_at
    raw["shipping"]["delivered_at"] = delivered_at
    raw["shipments"][0]["delivered_at"] = delivered_at
    if missing_date:
        raw.pop("status_changed_at", None)
        raw["shipping"].pop("delivered_at", None)
        raw["shipments"][0].pop("delivered_at", None)
    return data


def _state():
    state = active()
    state["started_at"] = OWNER_START
    state["engine"] = {"as_of": BEFORE_SNAPSHOT}
    return state


def _sources(data, prior):
    preserved = deepcopy(data)
    result = run(collect_sources(HardBoundaryDB(data), "owner", OWNER_START,
                                 LATEST, operational_state=prior))
    assert data == preserved
    return result


def test_preopening_order_delivered_after_cutover_enters_courier_payable_once():
    prior = _state()
    before = deepcopy(prior)
    sources = _sources(_data(), prior)
    assert sources["orders"] == [], "do not reimport an old sale"
    assert len(sources["delivery_events"]) == 1, "post-cutover delivery needs a dated event"
    event = sources["delivery_events"][0]
    assert event["id"] == "100"
    assert event["delivery_event"]["delivered_at"] == DELIVERY
    result = reconcile(deepcopy(prior), deepcopy(sources), LATEST)
    assert fee_total(result) == Decimal("12.00")
    assert result["engine"]["obligations"]["shipping:100:base_shipping"]["business_date"] == "2026-11-22"
    assert not any(v["kind"] in {"supplier", "provider"} for v in result["engine"]["obligations"].values())
    assert result["movements"] == prior["movements"] == []
    assert result["openings"] == prior["openings"]
    assert reconcile(deepcopy(result), deepcopy(sources), LATEST) == result
    assert prior == before


def test_delivery_before_operational_cutover_never_recreates_old_courier_fee():
    prior = _state()
    sources = _sources(_data(delivered_at=BEFORE_START_DELIVERY), prior)
    assert sources["orders"] == []
    assert sources["delivery_events"] == []
    result = reconcile(prior, sources, LATEST)
    assert fee_total(result) == Decimal("0")
    assert result["openings"] == prior["openings"]
    assert result["movements"] == []


def test_preopening_order_without_trusted_delivery_time_is_not_backdated():
    prior = _state()
    sources = _sources(_data(missing_date=True), prior)
    assert sources["orders"] == []
    assert sources["delivery_events"] == []
    result = reconcile(prior, sources, LATEST)
    assert fee_total(result) == Decimal("0")
    assert result["movements"] == []
