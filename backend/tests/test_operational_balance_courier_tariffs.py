"""An approved saved tariff can price evidenced deliveries without touching cash."""
from copy import deepcopy
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import read
from operational_courier_tariffs import approve, catalog, charge, terms, with_tariffs
from test_operational_balance_delivery_cutover import START, NOW, active, courier_data, fee_total
from test_operational_balance_supplier_invoice_preview import database, run
from operational_balance_sources import collect_sources
from operational_balance_engine import reconcile
from test_operational_balance_delivery_cutover import HardBoundaryDB


async def calculate(data, state):
    sources = await collect_sources(HardBoundaryDB(data), "owner", START, NOW, operational_state=state)
    return reconcile(state, sources, NOW)

COMPANY = dict(name='سمسا', is_deferred=True, cost_per_order=15, vat_percent=15,
               cod_fee_percent=.01, cod_fee_fixed_per_order=2, cod_fee_vat_percent=15)
API = '/operational-balances/courier-tariffs'


def shipment():
    d = courier_data()
    d['mz2_shipping_setup_v2'] = []
    source = d['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    raw['shipments'][0]['company'] = {'code': 'SMSA'}
    raw['shipping'].update(company='سمسا', company_code='SMSA')
    source['salla_shipping_current'].update(company_code='SMSA', company_name='سمسا')
    return d


def test_preview_then_pin_delivery_at_approved_price_and_replay_without_new_cash_or_openings():
    async def scenario():
        async with database() as db:
            await db.settings.insert_one({'user_id': 'owner', 'shipping_companies': [COMPANY,
                dict(COMPANY, name='مندوب الرياض'), dict(COMPANY, name='Aramex', is_deferred=False)]})
            await db.settings.insert_one({'user_id': 'other', 'shipping_companies': [dict(COMPANY, cost_per_order=50)]})
            assert await db.operational_balance_states_v1.count_documents({}) == 0
            preview = await catalog(db, 'owner')
            assert [i['id'] for i in preview['items']] == ['smsa']
            assert await db.operational_balance_states_v1.count_documents({}) == 0
            args = dict(party_id='smsa', source_hash=preview['items'][0]['source_hash'], reason='signed rate',
                        effective_from=START, confirmed=True, request_id='synthetic-1')
            first = await approve(db, 'owner', 'owner', args)
            assert await approve(db, 'owner', 'owner', args) == first
            stored = await read(db, 'owner')
            assert len(stored['courier_tariffs']) == len(stored['audit']) == 1
            d = shipment()
            prior = {**active(), 'courier_tariffs': stored['courier_tariffs']}
            assert fee_total(await calculate(d, active())) == 0
            result = await calculate(d, prior)
            assert result['engine']['obligations']['shipping:100:base_shipping']['confirmed'] == '17.25'
            assert result['engine']['obligations']['shipping:100:cod_commission']['confirmed'] == '4.60'
            assert (await calculate(d, result))['engine']['obligations'] == result['engine']['obligations']
            assert result['movements'] == prior['movements'] and result['openings'] == prior['openings']
            historical = await calculate(d, {**prior, 'engine': {'as_of': NOW}})
            assert fee_total(historical) == 0
            assert any(i['code'] == 'courier_fee_historical_review_required' for i in historical['engine']['issues'])
            # A later mutable setting change cannot reprice the old October delivery.
            await db.settings.update_one({'user_id':'owner'}, {'$set': {'shipping_companies.0.cost_per_order':30}})
            with pytest.raises(HTTPException):
                await approve(db, 'owner', 'owner', {**args, 'request_id':'new-request'})
            later = (await catalog(db, 'owner'))['items'][0]
            await approve(db, 'owner', 'owner', {**args, 'request_id':'next-request',
                'source_hash':later['source_hash'], 'effective_from':NOW})
            rates = (await read(db, 'owner'))['courier_tariffs']
            assert len(rates) == 2 and fee_total(await calculate(d, {**active(), 'courier_tariffs': rates})) == Decimal('21.85')
            native = {'user_id':'owner','couriers':[{'courier_key':'custom', 'salla_carrier_keys':['SmSa']}]}
            projected, conflicts = with_tariffs(native,'owner',{'owner_id':'owner','courier_tariffs':rates})
            assert conflicts == ['smsa'] and not projected['couriers'] and not projected['contracts']
    run(scenario())


def test_request_authorization_and_stale_source():
    async def scenario():
        async with database() as db:
            principal = {'id':'employee'}
            await db.users.insert_many([{'id':'owner', 'role':'owner'},
                                        {'id':'employee','role':'employee','created_by':'owner'}])
            await db.settings.insert_one({'user_id':'owner','shipping_companies':[COMPANY]})
            api = FastAPI()
            api.include_router(make_operational_balance_router(db, lambda: principal))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as client:
                assert (await client.get(API)).status_code == 403
                principal['id'] = 'owner'
                row = (await client.get(API)).json()['items'][0]
                args = dict(party_id='smsa', source_hash=row['source_hash'], reason='signed rate',
                            effective_from=START, confirmed=True, request_id='synthetic-1')
                await db.settings.update_one({'user_id':'owner'},{'$set':{'shipping_companies.0.cost_per_order':50}})
                assert (await client.post(API,json=args)).status_code == 409
                assert await db.operational_balance_states_v1.count_documents({}) == 0
    run(scenario())


def test_incomplete_cod_is_not_zero_and_unmatched_band_does_not_hide_base():
    source = deepcopy(COMPANY)
    del source['cod_fee_vat_percent']
    for t in (terms(source), terms(dict(COMPANY, cod_fee_tiers=[dict(min_amount=500, commission_percent=.02)]))):
        q = charge({'terms':t}, Decimal('200'), False)
        assert q['cost'] == '17.25' and not q['fee_complete']
        assert q['fee_components'][1]['amount'] is None
    with pytest.raises(ValueError):
        terms(dict(COMPANY, vat_rate=.05))
