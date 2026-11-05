"""Order transition time earns a courier fee independently of a lagging waybill."""
from copy import deepcopy

import pytest

from operational_courier_transitions import WEBHOOK_PROOF, verified_webhook_transition
from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping
from test_operational_balance_courier_order_transition import COLLECTION, fixture, history
from test_operational_balance_delivery_cutover import DELIVERED, NOW, START, courier_data, fee_total
from test_operational_balance_verified_shipping_fees import calculated


def lagging_order(*, old=True, currency="SAR", proof="canonical"):
    data, source, raw, current = fixture(old)
    for shipping in (current, raw["shipping"], *raw["shipments"]):
        shipping["status"] = "delivering"
    raw["currency"] = raw["total"]["currency"] = currency
    if proof == "canonical":
        raw["status"] = {"slug": "delivered", "changed_at": DELIVERED}
    elif proof == "history":
        data[COLLECTION] = [history()]
    return data, source, raw, current


@pytest.mark.parametrize("old", [True, False])
@pytest.mark.parametrize("currency", ["SAR", "KWD"])
@pytest.mark.parametrize("proof", ["canonical", "history"])
def test_order_delivered_after_start_books_one_sar_fee_with_lagging_waybill(old, currency, proof):
    data, _, _, _ = lagging_order(old=old, currency=currency, proof=proof)
    before = deepcopy(data)
    result = calculated(data)
    assert fee_total(result) == 10
    fees = [row for row in result["engine"]["obligations"].values() if row["kind"] == "shipping"]
    assert all(row["currency"] == "SAR" for row in fees)
    event = result["engine"]["facts"]["delivery_event:100"]
    assert event["delivered_at"] == DELIVERED and event["currency"] == currency
    assert event["timestamp_source"] == "order_status_transition"
    assert "cod:100" not in result["engine"]["obligations"]
    if old:
        assert not any(row["kind"] in {"supplier", "provider"} for row in result["engine"]["obligations"].values())
    assert result["movements"] == []
    assert calculated(data, result) == result
    assert data == before


@pytest.mark.parametrize("at", [START, "2026-10-04T23:59:59.999999+00:00", None])
def test_only_actual_order_transition_after_start_can_confirm_fee(at):
    data, _, raw, _ = lagging_order(proof=None)
    if at:
        raw["status_changed_at"] = at
    result = calculated(data)
    assert fee_total(result) == (10 if at == START else 0)
    assert "cod:100" not in result["engine"]["obligations"]


def test_new_native_delivered_waybill_without_order_transition_does_not_supply_fee_date():
    data = courier_data()
    data["unified_orders"][0]["raw_by_source"]["salla_direct"].pop("status_changed_at", None)
    result = calculated(data)
    assert fee_total(result) == 0
    assert any(row["code"] == "operational_delivery_timestamp_missing" for row in result["engine"]["issues"])


def test_verified_order_status_webhook_preserves_lagging_waybill_and_earns_fee_once():
    data, source, raw, _ = lagging_order(proof=None)
    body = {"event": "order.status.updated", "merchant": "synthetic-store",
            "created_at": "Tue Oct 06 2026 15:00:00 GMT+0300",
            "data": {"status": "تم التوصيل", "order": deepcopy(raw)}}
    observed = extract_shipping(raw, event_name=body["event"])
    proof = verified_webhook_transition(body, raw, observed, "owner")
    assert proof is not None
    observed[WEBHOOK_PROOF] = proof
    accepted = accept_shipping(source, observed)
    source[CURRENT_SHIPPING] = accept_shipping({CURRENT_SHIPPING: accepted}, observed)
    assert source[CURRENT_SHIPPING]["status"] == "delivering"
    result = calculated(data)
    assert fee_total(result) == 10
    assert "cod:100" not in result["engine"]["obligations"]
    assert calculated(data, result) == result


def test_later_waybill_delivery_does_not_rewrite_or_duplicate_frozen_order_fee():
    data, _, raw, current = lagging_order()
    first = calculated(data)
    assert fee_total(first) == 10
    for shipping in (current, raw["shipping"], *raw["shipments"]):
        shipping.update(status="delivered", delivered_at=NOW)
    result = calculated(data, first)
    assert result["engine"]["facts"] == first["engine"]["facts"]
    assert result["engine"]["obligations"] == first["engine"]["obligations"]
    assert result["movements"] == first["movements"] == []
    assert fee_total(result) == 10
    assert any(row["code"] == "operational_delivery_confirmed_evidence_changed" for row in result["engine"]["issues"])


def test_native_delivery_after_start_cannot_replace_prestart_order_transition():
    data = courier_data()
    data["unified_orders"][0]["raw_by_source"]["salla_direct"]["status_changed_at"] = "2026-10-04T23:59:59+00:00"
    assert fee_total(calculated(data)) == 0


def test_matching_embedded_timestamp_cannot_prove_cash_while_current_waybill_is_delivering():
    data, _, raw, _ = lagging_order()
    for shipment in (raw["shipping"], *raw["shipments"]):
        shipment.pop("status")
        shipment["delivered_at"] = DELIVERED
    result = calculated(data)
    assert fee_total(result) == 10
    assert result["engine"]["facts"]["delivery_event:100"]["timestamp_source"] == "order_status_transition"
    assert "cod:100" not in result["engine"]["obligations"]


@pytest.mark.parametrize('conflict', ['cancelled', 'ملغي', 'returned', 'مرتجع', 'superseded', 'carrier'])
def test_order_transition_cannot_authorize_an_inactive_replaced_or_different_courier_waybill(conflict):
    data, _, raw, current = lagging_order()
    if conflict == 'superseded':
        current['superseded_shipment_ids'] = ['older-shipment']
    elif conflict == 'carrier':
        raw['shipments'][0]['company']['code'] = 'different-courier'
    else:
        current['status'] = conflict
    result = calculated(data)
    assert fee_total(result) == 0 and 'cod:100' not in result['engine']['obligations']
    assert result['movements'] == []


def test_existing_native_fee_retains_its_date_when_new_order_evidence_arrives():
    data = courier_data()
    raw = data["unified_orders"][0]["raw_by_source"]["salla_direct"]
    raw["status_changed_at"] = DELIVERED
    prior = calculated(data)
    assert fee_total(prior) == 12
    assert "timestamp_source" not in prior["engine"]["facts"]["delivery_event:100"]
    raw["status_changed_at"] = NOW
    result = calculated(data, prior)
    assert result["engine"]["facts"] == prior["engine"]["facts"]
    assert result["engine"]["obligations"] == prior["engine"]["obligations"]
    assert result["movements"] == prior["movements"]


def test_previously_unpriced_native_delivery_needs_order_transition_for_its_first_fee():
    data = courier_data()
    raw = data["unified_orders"][0]["raw_by_source"]["salla_direct"]
    rates = data["mz2_shipping_setup_v2"][0]["contracts"]
    data["mz2_shipping_setup_v2"][0]["contracts"] = []
    prior = calculated(data)
    assert fee_total(prior) == 0 and prior["engine"]["facts"]["shipping:100"]["cost"] is None
    raw.pop("status_changed_at")
    data["mz2_shipping_setup_v2"][0]["contracts"] = rates
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert result["engine"]["facts"] == prior["engine"]["facts"]
    assert any(row["code"] == "operational_delivery_timestamp_missing" for row in result["engine"]["issues"])


def test_grandfathered_fee_cannot_gain_cod_from_a_lagging_waybill():
    data = courier_data()
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    raw["shipping"]["shipment"] = raw.pop("shipments")[0]
    raw["shipping"].pop("delivered_at")
    prior = calculated(data)
    assert fee_total(prior) == 10 and "cod:100" not in prior["engine"]["obligations"]
    source[CURRENT_SHIPPING]["status"] = "delivering"
    raw["shipping"]["delivered_at"] = DELIVERED
    raw["shipping"].pop("status")
    raw["shipping"]["shipment"].pop("status")
    result = calculated(data, prior)
    assert result["engine"]["facts"] == prior["engine"]["facts"]
    assert result["engine"]["obligations"] == prior["engine"]["obligations"]
    assert "cod:100" not in result["engine"]["obligations"] and fee_total(result) == 10


def test_existing_order_and_waybill_time_difference_is_stable_on_identical_replay():
    data = courier_data()
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    for shipping in (source[CURRENT_SHIPPING], raw["shipping"], *raw["shipments"]):
        shipping["delivered_at"] = NOW
    first = calculated(data)
    assert fee_total(first) == 10
    assert calculated(data, first) == first


@pytest.mark.parametrize("race", [False, True])
@pytest.mark.parametrize('earlier_dispatch', [False, True])
def test_complete_order_history_acquisition_needs_no_delivered_shipment_and_keeps_cas(monkeypatch, race, earlier_dispatch):
    import asyncio
    from mongomock_motor import AsyncMongoMockClient
    from salla_integration import courier_delivery_sync as sync

    data, source, raw, current = lagging_order(proof=None)
    source.update(order_number="10", salla_order_id="100", order_status_slug="delivered",
                  order_status="تم التوصيل", g47_salla_snapshot={"source_updated_at": NOW})
    calls = []

    async def scenario():
        db = AsyncMongoMockClient()["synthetic_order_transition"]
        await db.unified_orders.insert_one(deepcopy(source))

        async def native(_db, owner, method, path, *, params):
            assert owner == "owner" and method == "GET" and params == {"page": 1, "per_page": 100}
            assert path == "/orders/100/histories"
            calls.append(path)
            if race:
                await db.unified_orders.update_one({"order_number": "10"}, {"$set": {"order_status_slug": "returned"}})
            events = [{"id": "delivered-1", "order_id": "100", "to_status": "delivered", "created_at": DELIVERED}]
            if earlier_dispatch:
                events.append({'id': 'dispatch-1', 'type': 'activity', 'status': 'delivering', 'note': 'delivering',
                    'created_at': '2026-10-06T10:00:00+00:00', 'created_by': {'id': 'synthetic-publisher', 'name': 'Carrier'}})
            return {"success": True, "data": events,
                "pagination": {"total": len(events), "totalPages": 1, "currentPage": 1}}

        monkeypatch.setattr(sync, "call_salla", native)
        await sync.acquire_missing(db, "owner", [{"source_id": "100", "code": "operational_delivery_timestamp_missing"}],
                                   started_at=START)
        saved = await db.unified_orders.find_one({"order_number": "10"}, {"_id": 0})
        assert saved[CURRENT_SHIPPING]["status"] == "delivering"
        assert (WEBHOOK_PROOF in saved[CURRENT_SHIPPING]) is not race
        return saved

    data["unified_orders"] = [asyncio.run(scenario())]
    assert calls == ["/orders/100/histories"]
    result = calculated(data)
    assert fee_total(result) == (0 if race else 10)
    assert "cod:100" not in result["engine"]["obligations"]
    assert result["movements"] == []


@pytest.mark.parametrize('change', [None, 'date', 'state', 'target'])
def test_legacy_completed_history_enrichment_preserves_the_delivery_proof_without_accepting_conflicts(change):
    data, _, _, _ = lagging_order(proof='history')
    at = '2026-10-06T09:00:00+00:00'
    completed = history(at, to_status='completed', id='completion-1')
    completed['native_status_transition'].insert(0, {'delivered': False, 'at': at})
    enriched = completed['native_status_transition'][1]
    if change == 'date':
        enriched['at'] = '2026-10-06T08:00:00+00:00'
    elif change == 'state':
        enriched['delivered'] = True
    elif change == 'target':
        enriched['target_status'] = 'cancelled'
    data[COLLECTION].append(completed)
    result = calculated(data)
    assert fee_total(result) == (10 if change is None else 0)
    assert 'cod:100' not in result['engine']['obligations']
