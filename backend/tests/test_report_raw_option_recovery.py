"""Provider shape observed in production; all identifiers/text are synthetic."""
from copy import deepcopy

from order_cost_authority import calculate_catalog_line_cost, line_fingerprint
from report_option_recovery import attach_report_option_evidence, report_cost_item


def fixture():
    item = {'order_item_id': 'line1', 'product_id': 'product1', 'quantity': 2, 'options': []}
    order = {'order_number': 'order1', 'products': [item]}
    raw = {'order_number': 'order1', 'raw_by_source': {'salla_direct': {
        'reference_id': 'order1', 'items': [{'id': 'line1', 'product_id': 'product1',
            'quantity': 2, 'options': [{'id': 'checkout-row', 'product_option_id': 'emb',
                'name': 'Renamed question', 'value': {'id': 'yes', 'name': 'Renamed answer'}}]}]}}}
    return order, raw


def cost(item):
    return calculate_catalog_line_cost(item,
        product={'options': [{'id': 'emb', 'name': 'Old question', 'values': [
            {'id': 'yes', 'name': 'Old answer'}, {'id': 'no', 'name': 'No'}]}]},
        profile={'base_cost': 15}, product_bindings=[{'resource_id': 'bag'}],
        option_bindings=[{'id': 'binding', 'option_id': 'emb', 'value_id': 'yes',
                         'mode': 'resource', 'resource_id': 'emb'}],
        resources={'bag': {'unit_cost': 1.2}, 'emb': {'unit_cost': 8}})


def test_exact_raw_line_restores_report_cost_without_changing_historical_identity():
    order, raw = fixture()
    fingerprint = line_fingerprint(order['products'][0])
    attach_report_option_evidence([order], [raw])
    assert line_fingerprint(order['products'][0]) == fingerprint
    assert order['products'][0]['options'] == []
    result = cost(order['products'][0])
    assert result['line_total'] == 48.4
    assert result['mezan_cost_complete'] is True


def test_recovery_never_uses_sku_or_another_order_or_quantity():
    for field, value in [('id', 'different'), ('product_id', 'other'), ('quantity', 3)]:
        order, raw = fixture()
        raw['raw_by_source']['salla_direct']['items'][0][field] = value
        attach_report_option_evidence([order], [raw])
        assert cost(order['products'][0])['mezan_cost_complete'] is False
    order, raw = fixture()
    raw['raw_by_source']['salla_direct']['reference_id'] = 'other-order'
    attach_report_option_evidence([order], [raw])
    assert cost(order['products'][0])['mezan_cost_complete'] is False


def test_duplicate_provider_or_canonical_lines_do_not_guess():
    for side in ['provider', 'canonical']:
        order, raw = fixture()
        rows = raw['raw_by_source']['salla_direct']['items'] if side == 'provider' else order['products']
        rows.append(deepcopy(rows[0]))
        attach_report_option_evidence([order], [raw])
        assert cost(order['products'][0])['mezan_cost_complete'] is False


def test_existing_choices_are_never_overwritten():
    order, raw = fixture()
    order['products'][0]['options'] = [{'option_id': 'emb', 'value_id': 'no'}]
    attach_report_option_evidence([order], [raw])
    assert cost(order['products'][0])['line_total'] == 32.4


import pytest


@pytest.mark.asyncio
async def test_dashboard_and_sold_http_read_provider_choices_without_writes():
    import httpx
    from fastapi import FastAPI
    from mongomock_motor import AsyncMongoMockClient
    from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
    from dashboard_v2_routes import _filtered_orders, build_mezan_v2_product_cost, build_mezan_v2_product_cost_cooperative, make_dashboard_v2_router
    from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
    from product_option_cost_routes import BINDINGS, RESOURCES
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS
    from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB

    db = AsyncMongoMockClient().raw_option_reports
    owner = 'synthetic-owner'
    await db.settings.insert_one({'user_id': owner, 'payment_methods': deepcopy(DEFAULT_PAYMENT_METHODS),
        'payment_fee_defaults_version': PAYMENT_FEE_DEFAULTS_VERSION})
    order, raw = fixture()
    order.update(user_id=owner, order_date='2026-11-05', order_status='تم التنفيذ',
                 total_amount=200, currency='SAR', raw_by_source=raw['raw_by_source'])
    order['products'][0].update(total=200, sku='SYNTHETIC')
    await db.unified_orders.insert_one(deepcopy(order))
    # Same order identity in another tenant must never supply recovery evidence.
    await db.unified_orders.insert_one({**deepcopy(order), 'user_id': 'other-owner'})
    await db[PRODUCTS].insert_one({'user_id': owner, 'salla_product_id': 'product1', 'sku': 'SYNTHETIC',
        'options': [{'id': 'emb', 'name': 'Old question', 'values': [{'id': 'yes', 'name': 'Old answer'}, {'id': 'no', 'name': 'No'}]}]})
    await db[COST_PROFILES].insert_one({'user_id': owner, 'salla_product_id': 'product1', 'base_cost': 15})
    await db[PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id': owner, 'salla_product_id': 'product1', 'id': 'bag-binding', 'resource_id': 'bag'})
    await db[BINDINGS].insert_one({'user_id': owner, 'salla_product_id': 'product1', 'id': 'emb-binding', 'option_id': 'emb', 'value_id': 'yes', 'mode': 'resource', 'resource_id': 'emb'})
    await db[RESOURCES].insert_many([{'user_id': owner, 'id': 'bag', 'unit_cost': 1.2}, {'user_id': owner, 'id': 'emb', 'unit_cost': 8}])
    readonly = ReadOnlyDB(db)
    orders = await _filtered_orders(readonly, owner, from_date='2026-11-05', to_date='2026-11-05', payment_methods=None, shipping_companies=None)
    assert len(orders) == 1
    for builder in (build_mezan_v2_product_cost, build_mezan_v2_product_cost_cooperative):
        result = await builder(readonly, owner, orders)
        assert result['total'] == 48.4, result

    async def user():
        return {'id': owner, 'role': 'owner'}
    async def unused(**kwargs):
        raise AssertionError('Legacy report not needed')
    app = FastAPI()
    app.include_router(make_dashboard_v2_router(readonly, user, unused, lambda current: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/dashboard-v2/sold-products', params={'from_date': '2026-11-05', 'to_date': '2026-11-05'})
    assert response.status_code == 200, response.text
    assert response.json()['totals']['total_cost'] == 48.4
    assert response.json()['incomplete_count'] == 0
    stored = await db.unified_orders.find_one({'user_id': owner})
    assert stored['products'][0]['options'] == []
    assert '_report_options_raw' not in stored['products'][0]
