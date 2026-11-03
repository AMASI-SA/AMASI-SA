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
            assert new['providers']['parties']['tamara']['receivable'] == '273.00'
            assert old['cod_expected']['known_subtotal'] is None
            assert new['cod_expected']['known_subtotal'] == '77.00'
            assert new['cod_expected']['reported_total'] is None
            assert new['provider_fees_expected']['known_subtotal'] is None
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
            result = cli['summary']('shop',s,None)['SAR']
            assert result['actual_fees_on_recorded_provider_settlements'] == {
                'tamara': {'known_subtotal':'2.00','settlements_without_fee_amount':0}}
            assert result['provider_fees_expected']['known_by_platform']['tamara'] == '1.90'
    run(scenario())
