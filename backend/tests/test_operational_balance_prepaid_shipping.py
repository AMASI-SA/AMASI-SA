"""The approved prepaid courier charge pays from its exact cashbox at completion."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_balance_sources import collect_sources
from operational_balance_store import read, initial, STATES
from operational_courier_tariffs import approve, catalog, terms
from operational_financial_position import build_financial_position
from test_operational_balance_courier_order_transition import history, COLLECTION
from test_operational_balance_delivery_cutover import START, NOW, courier_data
from test_operational_balance_sources import HardBoundaryDB

COMPLETED = "2026-10-06T09:00:00+00:00"
COMPANY = {"name": "Aramex", "payment_mode": "prepaid", "is_deferred": False,
           "cost_per_order": 30, "vat_percent": 15, "cod_fee_percent": 0,
           "cod_fee_fixed_per_order": 0, "cod_fee_vat_percent": 15}
CASH = {"id": "salla-cash", "user_id": "owner", "name": "محفظة سله", "currency": "SAR",
        "account_type": "cash", "status": "active"}


def fixture(*, old=True, current="completed", at=COMPLETED):
    data = courier_data()
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    raw["status"] = current
    raw.pop("status_changed_at", None)
    if at is not None:
        raw["status_changed_at"] = at
    if not old:
        raw["created_at"] = "2026-10-05T01:00:00+00:00"
    raw["payment"] = {"method": "cod"}
    source.update(order_number="10", salla_order_id="100", order_status_slug=current,
                  g47_salla_snapshot={"source_updated_at": NOW})
    current_shipping = source["salla_shipping_current"]
    current_shipping.update(status="delivering", company_code="aramex", company_name="Aramex", source_kind="order")
    raw["shipping"].update(company_code="aramex", company="Aramex", status="delivering")
    raw["shipments"][0].update(company={"code": "aramex"}, status="delivering")
    raw["shipping"].pop("delivered_at")
    raw["shipments"][0].pop("delivered_at")
    data["mz2_shipping_setup_v2"] = []
    data["mz2_financial_accounts"] = [deepcopy(CASH)]
    version = {"id": "aramex", "name": "Aramex", "keys": ["aramex", "Aramex"], "currency": "SAR",
               "terms": terms(COMPANY), "payment_mode": "prepaid", "cash_account": {
                   "id": CASH["id"], "name": CASH["name"], "currency": "SAR", "kind": "cash"},
               "version_id": "approved-aramex", "effective_from": START,
               "confirmed_by": "owner", "confirmed_at": START}
    state = {"owner_id": "owner", "status": "active", "started_at": START, "movements": [],
             "openings": {}, "engine": {'as_of': START}, "courier_tariffs": [version]}
    return data, state


def calculate(data, state):
    sources = asyncio.run(collect_sources(HardBoundaryDB(data), "owner", START, NOW, operational_state=state))
    return reconcile(state, sources, NOW)


@pytest.mark.parametrize("old", [True, False])
def test_completion_charges_approved_base_to_exact_salla_cash_once_without_cod_or_debt(old):
    data, prior = fixture(old=old)
    result = calculate(data, prior)
    movement, = result["movements"]
    assert (movement["amount"], movement["currency"], movement["direction"]) == ("34.50", "SAR", "outgoing")
    assert (movement["bank_id"], movement["bank_kind"]) == ("salla-cash", "cash")
    assert movement["allocations"] == [{"obligation_id": "shipping-prepaid:100", "amount": "34.50"}]
    assert "shipping:100" not in result["engine"]["facts"]
    assert "cod:100" not in result["engine"]["obligations"]
    assert not any(row["kind"] == "provider" for row in result["engine"]["obligations"].values())
    positions = report(result)
    courier = next(row for row in positions["parties"] if row["party_type"] == "courier")
    wallet = next(row for row in positions["parties"] if row["party_type"] == "cash")
    assert (courier["outstanding_receivable"], courier["outstanding_payable"]) == ("0.00", "0.00")
    assert courier['expense_paid'] == '34.50'
    assert wallet["actual"] == "-34.50"
    position = build_financial_position(result)['currencies']['SAR']
    assert position['metrics']['liquidity'] == position['metrics']['monetary_position'] == '-34.50'
    detail, = position['deliveries']['rows']
    assert detail['shipping_confirmed'] == detail['shipping_settled'] == '34.50'
    assert detail['shipping_outstanding'] == '0.00' and detail['completed_at'] == COMPLETED
    assert detail['delivered_at'] is None and detail['cash_account_id'] == 'salla-cash'
    assert result["openings"] == prior["openings"]
    assert calculate(data, result) == result


def test_prepaid_catalog_requires_an_explicit_owner_cash_binding_and_versions_it():
    async def scenario():
        db = AsyncMongoMockClient()["synthetic_prepaid_approval"]
        await db.settings.insert_one({"user_id": "owner", "shipping_companies": [COMPANY]})
        await db.mz2_financial_accounts.insert_many([deepcopy(CASH), {**CASH, "id": "bank", "account_type": "bank"},
            {**CASH, "id": "foreign", "user_id": "other"}, {**CASH, "id": "usd", "currency": "USD"}])
        preview = await catalog(db, "owner")
        candidate, = preview["items"]
        assert candidate["payment_mode"] == "prepaid" and candidate["cash_account_id"] is None
        assert preview["cash_accounts"] == [{"id": "salla-cash", "name": "محفظة سله", "kind": "cash", "currency": "SAR", "ready": True}]
        payload = {"party_id": candidate["id"], "source_hash": candidate["source_hash"], "effective_from": START,
                   "reason": "Owner approved prepaid contract", "confirmed": True, "request_id": "approve-prepaid"}
        with pytest.raises(HTTPException):
            await approve(db, "owner", "owner", payload)
        await approve(db, "owner", "owner", {**payload, "cash_account_id": "salla-cash"})
        stored = await read(db, "owner")
        assert stored["courier_tariffs"][0]["cash_account"]["id"] == "salla-cash"
        assert (await catalog(db, "owner"))["items"][0]["cash_account_id"] == "salla-cash"
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['missing_time', 'updated_only', 'before_start', 'future', 'inactive_cash',
    'foreign_cash', 'bank', 'missing_cash', 'not_ready', 'unnamed_cash', 'unapproved_tariff', 'future_tariff', 'wrong_owner'])
def test_incomplete_completion_or_cash_contract_never_creates_a_debit(failure):
    data, state = fixture()
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    cash = data['mz2_financial_accounts'][0]
    version = state['courier_tariffs'][0]
    if failure in {'missing_time', 'updated_only'}:
        raw.pop('status_changed_at')
        if failure == 'updated_only':
            data[COLLECTION] = [history(None, to_status='completed', updated_at=COMPLETED)]
    elif failure == 'before_start':
        raw['status_changed_at'] = '2026-10-04T23:59:59+00:00'
    elif failure == 'future':
        raw['status_changed_at'] = '2026-10-08T12:00:00+00:00'
    elif failure == 'inactive_cash':
        cash['status'] = 'inactive'
    elif failure == 'foreign_cash':
        cash['currency'] = 'USD'
    elif failure == 'bank':
        cash['account_type'] = 'bank'
    elif failure == 'missing_cash':
        data['mz2_financial_accounts'] = []
    elif failure == 'not_ready':
        cash['ready'] = False
    elif failure == 'unnamed_cash':
        cash['name'] = ''
    elif failure == 'unapproved_tariff':
        version['confirmed_by'] = 'someone-else'
    elif failure == 'future_tariff':
        version['effective_from'] = NOW
    elif failure == 'wrong_owner':
        data['unified_orders'][0]['user_id'] = 'someone-else'
    result = calculate(data, state)
    assert result['movements'] == []
    assert 'shipping-prepaid:100' not in result['engine']['obligations']


@pytest.mark.parametrize('enriched', [False, True])
def test_completed_history_after_cutover_pays_once_even_if_order_is_already_delivered(enriched):
    data, state = fixture(current='delivered', at='2026-10-06T12:00:00+00:00')
    event = history(COMPLETED, to_status='completed')
    if enriched:
        event['native_status_transition'].insert(0, {'delivered': False, 'at': COMPLETED})
    data[COLLECTION] = [event]
    result = calculate(data, state)
    assert len(result['movements']) == 1 and result['movements'][0]['amount'] == '34.50'
    assert result['engine']['facts']['shipping:100']['delivered_at'] == '2026-10-06T12:00:00+00:00'
    assert 'cod:100' not in result['engine']['obligations']
    assert calculate(data, result) == result
    detail, = build_financial_position(result)['currencies']['SAR']['deliveries']['rows']
    assert detail['completed_at'] == COMPLETED and detail['delivered_at'] == '2026-10-06T12:00:00+00:00'


def test_legacy_not_delivered_boolean_cannot_mean_order_completed():
    data, state = fixture(current='delivered', at='2026-10-06T12:00:00+00:00')
    event = history(COMPLETED, to_status='completed')
    event['native_status_transition'] = [{'delivered': False, 'at': COMPLETED}]
    data[COLLECTION] = [event]
    assert calculate(data, state)['movements'] == []


def test_pending_prepaid_order_without_a_completion_event_does_not_claim_missing_execution_evidence():
    data, state = fixture(old=False, current='pending', at=None)
    result = calculate(data, state)
    assert result['movements'] == []
    assert not any(row['code'].startswith('operational_prepaid_shipping_') for row in result['engine']['issues'])


@pytest.mark.parametrize('field', ['to_status', 'new_status'])
def test_customized_pending_target_cannot_inherit_completed_from_its_parent(field):
    data, state = fixture(current='pending', at=None)
    changes = {'to_status': None, field: {'slug': 'completed', 'customized': {'slug': 'pending'}}}
    event = history(COMPLETED, **changes)
    data[COLLECTION] = [event]
    assert not event['native_status_transition'][0].get('target_status')
    result = calculate(data, state)
    assert result['movements'] == [] and 'shipping-prepaid:100' not in result['engine']['obligations']


@pytest.mark.parametrize('change', ['completed_again', 'cancelled', 'cash_renamed', 'cash_disabled', 'tariff_changed'])
def test_frozen_prepaid_charge_and_cash_debit_survive_later_source_changes(change):
    data, state = fixture()
    first = calculate(data, state)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    if change == 'completed_again':
        raw['status_changed_at'] = NOW
    elif change == 'cancelled':
        raw['status'] = 'cancelled'
        data['unified_orders'][0]['order_status_slug'] = 'cancelled'
    elif change == 'cash_renamed':
        data['mz2_financial_accounts'][0]['name'] = 'Updated label'
    elif change == 'cash_disabled':
        data['mz2_financial_accounts'][0]['status'] = 'inactive'
    elif change == 'tariff_changed':
        first['courier_tariffs'].append({**first['courier_tariffs'][0], 'version_id': 'later', 'effective_from': NOW,
                                        'terms': terms({**COMPANY, 'cost_per_order': 50})})
    result = calculate(data, first)
    assert result['movements'] == first['movements']
    assert result['engine']['facts']['prepaid_shipping:100'] == first['engine']['facts']['prepaid_shipping:100']
    assert result['engine']['obligations']['shipping-prepaid:100'] == first['engine']['obligations']['shipping-prepaid:100']


@pytest.mark.parametrize('link', ['order_id', 'order_number', 'confirmed_charge', 'allocated_forecast'])
def test_existing_shipping_payment_or_confirmed_charge_holds_new_prepaid_debit_for_review(link):
    data, state = fixture()
    if link in {'order_id', 'order_number'}:
        state['movements'].append({'id': 'manual', 'party_type': 'courier', 'party_id': 'aramex',
            'direction': 'outgoing', 'kind': 'payment', 'amount': '34.50', 'currency': 'SAR',
            'bank_kind': 'cash', 'bank_id': 'salla-cash', 'allocations': [], link: '100' if link == 'order_id' else '10'})
    else:
        state['engine'] = {'facts': {}, 'obligations': {'shipping:100:base_shipping': {'id': 'shipping:100:base_shipping',
            'kind': 'shipping', 'party_type': 'courier', 'party_id': 'aramex', 'currency': 'SAR', 'direction': 'payable',
            'confirmed': '34.50' if link == 'confirmed_charge' else '0.00', 'expected': '0.00' if link == 'confirmed_charge' else '34.50',
            'business_date': '2026-10-06'}}}
        if link == 'allocated_forecast':
            state['movements'].append({'id': 'manual', 'party_type': 'courier', 'party_id': 'aramex',
                'direction': 'outgoing', 'kind': 'payment', 'amount': '34.50', 'currency': 'SAR', 'bank_kind': 'cash',
                'bank_id': 'salla-cash', 'allocations': [{'obligation_id': 'shipping:100:base_shipping', 'amount': '34.50'}]})
    result = calculate(data, state)
    assert result['movements'] == state['movements']
    assert 'shipping-prepaid:100' not in result['engine']['obligations']
    assert any(row['code'] == 'operational_prepaid_shipping_existing_charge_review_required' for row in result['engine']['issues'])


def test_idle_worker_never_posts_and_explicit_refresh_cas_records_completed_charge_once():
    from operational_balance_worker import tick
    from operational_balance_service import refresh
    async def scenario():
        db = AsyncMongoMockClient()['synthetic_prepaid_worker']
        data, state = fixture()
        for collection, rows in data.items():
            if rows:
                await db[collection].insert_many(deepcopy(rows))
        seeded = {**initial('owner'), **state, 'revision': 1}
        await db[STATES].insert_one(seeded)
        await asyncio.gather(tick(db), tick(db))
        assert (await read(db, 'owner'))['movements'] == []
        await refresh(db, 'owner', clock=NOW)
        saved = await read(db, 'owner')
        assert len(saved['movements']) == 1 and saved['movements'][0]['amount'] == '34.50'
        assert saved['engine']['obligations']['shipping-prepaid:100']['confirmed'] == '34.50'
        await tick(db)
        await refresh(db, 'owner', clock=NOW)
        assert (await read(db, 'owner'))['movements'] == saved['movements']
    asyncio.run(scenario())


@pytest.mark.parametrize('proof_kind,current_status', [('webhook', 'completed'), ('history', 'completed'),
    ('history', 'shipping'), ('history', 'shipped'), ('history', 'delivering')])
def test_verified_completion_producer_to_source_to_reconcile_keeps_lagging_label(monkeypatch, proof_kind, current_status):
    from operational_courier_transitions import COMPLETED_PROOF
    from operational_order_completed_evidence import verified_completed_webhook
    from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping
    from salla_integration import courier_delivery_sync as sync

    data, state = fixture(current=current_status, at=None)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    if proof_kind == 'webhook':
        body = {'event': 'order.status.updated', 'created_at': COMPLETED, 'merchant': 'synthetic',
                'data': {'status': 'تم التنفيذ', 'order': deepcopy(raw)}}
        observed = extract_shipping(raw, event_name=body['event'])
        proof = verified_completed_webhook(body, raw, observed, 'owner')
        assert proof is not None
        observed[COMPLETED_PROOF] = proof
        source[CURRENT_SHIPPING] = accept_shipping(source, observed)
        source[CURRENT_SHIPPING] = accept_shipping(source, observed)
    else:
        async def scenario():
            db = AsyncMongoMockClient()['synthetic_completed_acquisition']
            await db.unified_orders.insert_one(deepcopy(source))
            async def provider(_db, owner, method, path, *, params):
                assert owner == 'owner' and method == 'GET' and path == '/orders/100/histories'
                assert params == {'page': 1, 'per_page': 100}
                return {'success': True, 'data': [{'id': 'completed-1', 'order_id': '100',
                    'to_status': 'completed', 'created_at': COMPLETED}],
                    'pagination': {'total': 1, 'totalPages': 1, 'currentPage': 1}}
            monkeypatch.setattr(sync, 'call_salla', provider)
            await sync.acquire_missing(db, 'owner', [{'source_id': '100',
                'code': 'operational_prepaid_shipping_completion_timestamp_missing'}], started_at=START)
            return await db.unified_orders.find_one({'order_number': '10'}, {'_id': 0})
        source = data['unified_orders'][0] = asyncio.run(scenario())
    assert source[CURRENT_SHIPPING]['status'] == 'delivering'
    assert source[CURRENT_SHIPPING][COMPLETED_PROOF]['at'] == COMPLETED
    result = calculate(data, state)
    assert len(result['movements']) == 1 and result['movements'][0]['amount'] == '34.50'
    assert 'shipping:100' not in result['engine']['facts'] and 'cod:100' not in result['engine']['obligations']
    assert calculate(data, result) == result


def test_later_delivery_preserves_completed_webhook_and_does_not_pay_shipping_twice():
    from operational_courier_transitions import COMPLETED_PROOF
    from operational_order_completed_evidence import verified_completed_webhook
    from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping
    data, state = fixture(at=None)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    observed = extract_shipping(raw, event_name='order.status.updated')
    observed[COMPLETED_PROOF] = verified_completed_webhook({'event': 'order.status.updated', 'created_at': COMPLETED,
        'data': {'status': 'completed'}}, raw, observed, 'owner')
    source[CURRENT_SHIPPING] = accept_shipping(source, observed)
    first = calculate(data, state)
    raw.update(status='delivered', status_changed_at=NOW)
    source['order_status_slug'] = 'delivered'
    delivered = {**observed, 'status': 'delivered', 'provider_updated_at': NOW}
    delivered.pop(COMPLETED_PROOF)
    source[CURRENT_SHIPPING] = accept_shipping(source, delivered)
    for shipping in (raw['shipping'], *raw['shipments']):
        shipping['status'] = 'delivered'
    result = calculate(data, first)
    assert result['movements'] == first['movements']
    assert result['engine']['facts']['prepaid_shipping:100'] == first['engine']['facts']['prepaid_shipping:100']
    assert result['engine']['facts']['shipping:100']['delivered_at'] == NOW
    assert list(row['id'] for row in result['engine']['obligations'].values() if row['kind'] == 'shipping') == ['shipping-prepaid:100']


def test_later_deferred_period_does_not_inherit_an_expired_prepaid_mode():
    data, state = fixture(current='delivered', at=NOW)
    # This completion happened after the explicitly saved activation boundary;
    # it exercises new deferred work, not implicit historical admission.
    state['engine']['completed_shipping_admission'] = {'version': 1, 'owner_id': 'owner',
        'started_at': START, 'activated_at': START}
    data[COLLECTION] = [history('2026-10-06T11:00:00+00:00', to_status='completed')]
    later = {**state['courier_tariffs'][0], 'version_id': 'deferred-period', 'payment_mode': 'deferred',
             'effective_from': '2026-10-06T10:00:00+00:00'}
    later.pop('cash_account')
    state['courier_tariffs'].append(later)
    result = calculate(data, state)
    assert result['movements'] == []
    assert result['engine']['obligations']['shipping-completed:100']['confirmed'] == '34.50'
    assert result['engine']['obligations']['shipping-completed:100']['completed_at'] == '2026-10-06T11:00:00+00:00'
    assert 'shipping-prepaid:100' not in result['engine']['obligations']


def test_native_shipping_posting_prevents_second_prepaid_charge(monkeypatch):
    async def existing(db, owner, candidates, *, max_rows):
        return {row['order_number']: 'Existing shipping posting' for row in candidates}
    monkeypatch.setattr('operational_historical_shipping_fee.native_conflicts', existing)
    data, state = fixture()
    result = calculate(data, state)
    assert result['movements'] == []
    assert any(row['code'] == 'operational_prepaid_shipping_native_overlap' for row in result['engine']['issues'])


def test_prepaid_expense_is_atomic_and_independent_of_bad_customer_product_data():
    data, state = fixture(old=False)
    data['unified_orders'][0]['raw_by_source']['salla_direct']['items'][0]['quantity'] = 'invalid'
    result = calculate(data, state)
    assert len(result['movements']) == 1
    assert result['engine']['obligations']['shipping-prepaid:100']['confirmed'] == '34.50'
    assert any(row['code'] == 'mz2_product_cost_incomplete' for row in result['engine']['issues'])
    assert calculate(data, result) == result


def test_paid_prestart_order_does_not_keep_an_open_shipping_cost_estimate():
    from operational_open_cost_exposure import project
    data, state = fixture()
    result = calculate(data, state)
    exposure = asyncio.run(project(HardBoundaryDB(data), 'owner', result, as_of=NOW))
    assert not any(group['kind'] == 'courier' for group in exposure['groups'])
    assert not any(row['code'] == 'historical_shipping_quote_incomplete' for row in exposure['issues'])
