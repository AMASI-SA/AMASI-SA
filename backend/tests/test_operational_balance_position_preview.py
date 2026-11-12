"""Disposable loopback replica acceptance for the financial-position preview."""
import json
from pathlib import Path
import runpy

import pytest
from fastapi import HTTPException

# Stock CI runs pytest from backend/; the CLI is deliberately outside that runtime package.
cli = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts' / 'preview_operational_financial_position.py'))
bank_candidates, snapshot, main = cli['bank_candidates'], cli['snapshot'], cli['main']
from operational_balance_store import initial, STATES
from test_operational_balance_supplier_invoice_preview import database, run, contents
from test_operational_balance_open_customer_cod import order, NOW, START


def test_one_merchant_comparison_is_nonposting_and_replayable():
    async def scenario():
        async with database() as db:
            state = {**initial('owner'), 'revision': 2, 'started_at': START, 'status': 'active', 'engine': {'as_of': START},
                     'openings': {'p': {'id':'p','party_type':'provider','name':'Private account detail',
                         'party_id':'tamara','currency':'SAR','direction':'for_us','amount':'250.00'}}}
            await db[STATES].insert_one(state)
            good = order('PRIVATE-ORDER-NUMBER')
            raw = good['raw_by_source']['salla_direct']
            raw['customer'] = {'name':'Secret customer', 'email':'secret@example.test'}
            platform = order('PRIVATE-PAID', paid='23', status='under_review')
            payment = platform['raw_by_source']['salla_direct']
            payment.update(payment_method='tamara',paid_amount='23',remaining_amount='77')
            foreign = {**order('FOREIGN-PRIVATE', paid='0'), 'user_id':'other'}
            await db.unified_orders.insert_many([good, platform, foreign])
            before = await contents(db)
            result = await snapshot(db,'owner',NOW)
            assert result == await snapshot(db,'owner',NOW)
            assert await contents(db) == before
            assert result['openings_and_recorded_movements_equal'] and result['posting_allowed'] is False
            assert result['bank_movements_simulated'] is False
            assert sum(result['bank_preclaim_candidates'].values()) == 0
            old, new = (result[k]['SAR'] for k in ('saved','proposed'))
            assert old['providers']['parties']['tamara']['receivable'] == '250.00'
            provider = new['providers']['parties']['tamara']
            # Proved paid gross remains visible; its unknown commission is not
            # silently treated as zero when calculating a known net balance.
            assert provider['known_net'] == '250.00'
            assert provider['gross'] == '23.00' and provider['orders'] == 1
            assert provider['net'] is None and provider['receivable'] is None and provider['payable'] is None
            assert provider['fees'] is None
            assert not provider['complete']
            assert old['cod_expected']['known_subtotal'] is None
            # The independent COD source has complete identity, amount and
            # period coverage. Missing platform fees do not invalidate it.
            assert new['cod_expected'] == {
                'complete': True, 'known_subtotal': '77.00', 'reported_total': '77.00',
                'orders_with_amount': 1, 'rows_without_amount': 0}
            assert 'provider_fees_expected' not in new
            for private in ('PRIVATE-', 'Secret customer', 'Private account', 'secret@example'):
                assert private not in json.dumps(result)
            with pytest.raises(HTTPException):
                await snapshot(db,'other',NOW)
            assert await contents(db) == before
    run(scenario())


def test_cli_does_not_use_application_database_configuration(monkeypatch, capsys):
    monkeypatch.delenv('MEZAN_PREVIEW_READONLY_MONGO_URI', raising=False)
    monkeypatch.setenv('MONGO_URL','mongodb://private:password@invalid/')
    assert run(main('owner')) == 2
    captured = capsys.readouterr()
    assert not captured.out and 'private' not in captured.err and 'password' not in captured.err


@pytest.mark.parametrize('party_type', ['couriers', 'drivers'])
def test_post_cutover_delivery_is_previewed_without_persisting_a_fee_or_custody(party_type):
    from test_operational_balance_delivery_cutover import active, courier_data, driver_data, START, NOW

    async def scenario():
        async with database() as db:
            state = {**initial('owner'), **active(), 'revision': 1, 'engine': {'as_of': START}}
            await db[STATES].insert_one(state)
            data = courier_data() if party_type == 'couriers' else driver_data()
            for collection, rows in data.items():
                if rows:
                    await db[collection].insert_many(rows)
            before = await contents(db)
            result = await snapshot(db, 'owner', NOW)
            old, new = (result[k]['SAR'][party_type]['parties'] for k in ('saved','proposed'))
            assert not old or next(iter(old.values()))['payable'] == '0.00'
            assert list(new.values()) == [{'receivable':'200.00' if party_type=='couriers' else '240.00',
                                           'payable':'12.00'}]
            assert before == await contents(db)
    run(scenario())


def test_bank_candidates_distinguish_existing_conflicts_and_repeated_rows():
    move = {'request_id':'order-bank:old', 'order_number':'old', 'direction':'incoming', 'bank_id':'bank', 'amount':'8.00'}
    recorded = [move, {**move,'request_id':'different','order_number':'ambiguous'},
                {**move,'request_id':'another','order_number':'ambiguous'}]
    def bank(order, amount=8):
        return {'id':order,'order_number':order,'bank_credit':{'bank_id':'bank','amount':amount}}
    orders = [bank('old'), bank('old', 7), bank('ambiguous'), bank('new'), bank('new'), bank('new', 9), bank('bad', None), {}]
    assert bank_candidates(orders, recorded) == {'already_recorded':1,'conflicting':3,'prospective_new':1,
                                                'repeated_candidate':1,'unknown':1}
    assert len(recorded) == 3


def test_preview_uses_consolidated_provider_net_without_order_identity_or_fee_table():
    from test_operational_balance_configured_provider_fees import paid_state, settings

    state, _ = paid_state(terms=settings(rate='10', fixed='0', vat='0'))
    state['movements'].append(dict(id='private-payout', party_type='provider', party_id='tamara',
        currency='SAR', direction='incoming', kind='settlement', amount='47.00', actual_fee_amount='3.00',
        allocations=[dict(obligation_id='provider:one', amount='50.00')]))
    result = cli['summary']('owner', state, None)['SAR']
    row = result['providers']['parties']['tamara']
    assert (row['orders'], row['gross'], row['fees'], row['refunded'], row['net']) == (
        1, '100.00', '8.00', '0.00', '45.00')
    assert row['receivable'] == '45.00' and row['payable'] == '0.00'
    assert 'provider_fees_expected' not in result
    assert 'private-payout' not in json.dumps(result) and 'provider:one' not in json.dumps(result)


@pytest.mark.parametrize('role,expected', [('read', 0), ('readWrite', 1)])
def test_cli_requires_dedicated_read_role_on_intended_database(role, expected, monkeypatch, capsys):
    from os import environ
    from urllib.parse import urlsplit
    from uuid import uuid4
    from operational_balance_store import now

    async def scenario():
        async with database() as db:
            password = uuid4().hex
            await db.command('createUser', 'preview-probe', pwd=password, roles=[{'role':role,'db':db.name}])
            await db[STATES].insert_one({**initial('owner'),'status':'active','started_at':START,
                                         'revision':1, 'engine':{'as_of':now()}})
            local = urlsplit(environ['OPERATIONAL_TEST_MONGO_URI'])
            uri = f'mongodb://preview-probe:{password}@{local.netloc}/?{local.query}&authSource={db.name}'
            monkeypatch.setenv('MEZAN_PREVIEW_READONLY_MONGO_URI',uri)
            monkeypatch.setenv('MEZAN_PREVIEW_DB_NAME',db.name)
            before = await contents(db)
            assert await main('owner') == expected
            captured = capsys.readouterr()
            assert password not in captured.err + captured.out and uri not in captured.err + captured.out
            if expected:
                assert not captured.out
            else:
                assert json.loads(captured.out)['posting_allowed'] is False
            assert await contents(db) == before
    run(scenario())


def test_aggregate_provider_estimate_and_actual_settlement_fee_are_distinct():
    from accounting_onboarding_ssot import FeePolicyCreate, create_fee_policy, list_fee_policies
    from test_operational_balance_order_page_recognition import state, payout, BORN, T1
    from operational_balance_engine import reconcile
    from operational_payment_evidence import normalize_payment

    async def scenario():
        async with database() as db:
            policy = FeePolicyCreate(provider='tamara', percentage='2',fixed_amount='1', vat_treatment='exclusive',
                                     vat_rate='15',currency='SAR',effective_from='2026-01-01',evidence='fixture')
            await create_fee_policy(db,'shop','shop',policy)
            raw = {'payment_method':'tamara','payment':{'paid_amount':'100','paid_at':T1,'reference':'tx','status':'captured'}}
            pay, errors = normalize_payment(raw,'SAR','100',BORN,T1)
            assert not errors
            o = {'id':'1001','order_number':'123','created_at':BORN,'updated_at':T1,'currency':'SAR','payment':pay}
            s = reconcile(state(),{'orders':[o],'fee_policies':await list_fee_policies(db,'shop')},T1)
            s['movements'] = [{**payout('45.00','provider:1001'), 'id':'synthetic','bank_id':'bank',
                               'bank_name':'private-bank','bank_kind':'bank','name':'private-label'}]
            unstamped = cli['summary']('shop', s, None)['SAR']['providers']['parties']['tamara']
            assert unstamped['known_net'] == '53.10' and unstamped['net'] is None
            # The service stamps a completed reconciliation. The pure reducer
            # alone cannot certify the saved snapshot's coverage timestamp.
            s['engine']['as_of'] = T1
            result = cli['summary']('shop',s,None)['SAR']
            assert result['actual_fees_on_recorded_provider_settlements'] == {
                'tamara': {'known_subtotal':'2.00','settlements_without_fee_amount':0}}
            provider = result['providers']['parties']['tamara']
            assert provider['complete'] is True
            assert provider['fees'] == '3.90'
            assert provider['settled'] == '43.00' and provider['net'] == '53.10'
            assert 'provider_fees_expected' not in result
    run(scenario())


def test_supplier_snapshot_compares_booked_debt_with_separate_service_and_same_payment():
    from operational_balance_engine import reconcile
    from operational_balance_sources import collect_sources
    from test_operational_supplier_invoice_cutover import active, native_data, native_invoice, START, APPROVED, NOW

    async def scenario():
        async with database() as db:
            base = native_invoice(identity='private-base', piece_ids=('p1',))
            data = native_data()
            data['mezan_supplier_invoices_v2'] = [base]
            for collection, rows in data.items():
                if rows:
                    await db[collection].insert_many(rows)
            sources = await collect_sources(db, 'owner', START, APPROVED)
            saved = reconcile({**initial('owner'), **active(), 'revision': 1}, sources, APPROVED)
            saved['engine']['as_of'] = APPROVED
            saved['openings']['bank'] = dict(id='bank', party_type='bank', party_id='private-bank',
                currency='SAR', direction='for_us', name='Private bank', amount='100.00')
            booked = next(key for key in saved['engine']['obligations'] if ':receipt:' in key)
            saved['movements'] = [dict(id='private-payment', party_type='supplier', party_id='supplier',
                currency='SAR', direction='outgoing', kind='payment', amount='10.00', bank_kind='bank',
                bank_id='private-bank', actual_fee_amount='0.00', allocations=[dict(obligation_id=booked, amount='10.00')])]
            await db[STATES].insert_one(saved)
            service = native_invoice(identity='private-service', piece_ids=('p1',), unit=0, approved=NOW)
            service.update(subtotal_halalas=500, total_halalas=500)
            service['lines'][0].update(product_charge_eligible=False, total_halalas=500,
                services=[dict(service_id='private-stitch', quantity_per_piece=1, unit_price_halalas=500, total_halalas=500)])
            await db.mezan_supplier_invoices_v2.insert_one(service)
            before = await contents(db)
            preview = await snapshot(db, 'owner', NOW)
            assert await contents(db) == before
            assert preview['openings_and_recorded_movements_equal']
            old, new = [preview[side]['SAR']['suppliers'] for side in ('saved','proposed')]
            assert old['parties'].keys() == new['parties'].keys()
            assert list(old['parties'].values()) == [dict(receivable='0.00', payable='130.00')]
            assert list(new['parties'].values()) == [dict(receivable='0.00', payable='135.00')]
            assert 'private-' not in json.dumps(preview)
    run(scenario())


def test_supplier_forecasts_preserve_partial_amounts_and_distinct_order_counts():
    from test_operational_supplier_invoice_cutover import active, NOW

    state = {**initial('owner'), **active(), 'revision': 1, 'engine': {'as_of': NOW}}
    def group(stage, amount):
        return dict(period='poststart', kind='supplier', stage=stage, party_id='private-supplier',
                    currency='SAR', name='private-name', amount=amount, orders=1)

    exposure = dict(status='preview', complete=False,
        bucket_completeness=dict(version=1, products_unreceived=True, products_received_uninvoiced=False),
        currencies={'SAR': dict(poststart_supplier_unreceived='45.00', poststart_supplier_received_uninvoiced='7.00')},
        groups=[group('awaiting_review','40.00'), group('under_fulfillment','5.00')],
        distinct_order_counts={'SAR': {'products_unreceived': 1}})
    visible = cli['summary']('owner', state, exposure)['SAR']
    assert visible['products_unreceived'] == dict(
        complete=True, reported_total='45.00', known_subtotal='45.00', distinct_orders=1)
    assert visible['products_received_uninvoiced'] == dict(
        complete=False, reported_total=None, known_subtotal='7.00', distinct_orders=None)
    hidden = cli['summary']('owner', state, None)['SAR']['products_unreceived']
    assert hidden == dict(complete=False, reported_total=None, known_subtotal=None, distinct_orders=None)
    assert 'private-' not in json.dumps(visible)
