"""Side-option supplier projections leave order answers and costs intact."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import pytest

import bundle_supplier_pieces as supplier_bundles
import preparation_piece_operations as pieces
from preparation_piece_line_services import (
    _line_service_context_for_batch, original_service_spec_fields,
)
from supplier_option_service_policy import supplier_service_option_bindings
from test_bundle_supplier_pieces import (
    template_fixture as supplier_bundle_template,
    unit_fixture as supplier_bundle_unit,
)


CONTEXTS = (pieces._service_context_for_batch, _line_service_context_for_batch)
PRODUCTS = 'mezan_products_v2'
OWNER = 'side-option-owner'
PRODUCT = 'salla-product'


def product(with_policy=True):
    row = {'user_id': OWNER, 'id': 'local-product', 'mezan_product_id': 'mezan-product',
           'salla_product_id': PRODUCT, 'name': 'قطعة', 'options': [
        {'id': 'gift', 'name': 'كرت إهداء', 'type': 'radio', 'required': False,
         'values': [{'id': 'yes', 'name': 'نعم'}, {'id': 'no', 'name': 'لا'}]},
        {'id': 'gift-text', 'name': 'نص الكرت', 'type': 'text', 'required': False,
         'show_if': [{'option_id': 'gift', 'value_id': 'yes'}]},
        {'id': 'engrave', 'name': 'نقش الاسم', 'type': 'radio', 'required': False,
         'values': [{'id': 'yes', 'name': 'نعم'}]},
        {'id': 'wrap', 'name': 'غلاف إضافي', 'type': 'radio', 'required': False,
         'values': [{'id': 'yes', 'name': 'نعم'}]},
    ]}
    if with_policy:
        row['mezan_inventory_option_policy'] = {'revision': 1, 'roles': {
            'gift': 'informational', 'gift-text': 'informational',
            'engrave': 'customization', 'wrap': 'informational',
        }}
    return row


def bindings():
    return [
        {'id': oid, 'user_id': OWNER, 'salla_product_id': PRODUCT, 'option_id': oid,
         'option_name': name, 'value_id': value_id, 'value_name': value,
         'mode': 'resource', 'resource_id': resource, 'quantity': 1}
        for oid, name, value_id, value, resource in (
            ('gift', 'كرت إهداء', 'yes', 'نعم', 'card-service'),
            ('gift-text', 'نص الكرت', 'filled', 'ذكرى جميلة', 'text-service'),
            ('engrave', 'نقش الاسم', 'yes', 'نعم', 'engraving-service'),
            ('wrap', 'غلاف إضافي', 'yes', 'نعم', 'paper-component'),
        )
    ]


def resources():
    return {identity: {'user_id': OWNER, 'id': identity, 'name': identity,
                       'kind': kind, 'unit_cost': price}
            for identity, kind, price in (
                ('product-service', 'service', 9), ('card-service', 'service', 3),
                ('text-service', 'service', 2), ('engraving-service', 'service', 7),
                ('paper-component', 'stock_component', 4),
            )}


def batch():
    return {'id': 'batch-side', 'lines': [{
        'order_number': 'synthetic-order', 'order_item_id': 'synthetic-line',
        'product_id': PRODUCT, 'product_name': 'قطعة', 'quantity': 1,
        'file_spec_fields': [{'name': row['option_name'], 'value': row['value_name']}
                             for row in bindings()],
    }]}


async def seed(with_policy=True, *, owner=OWNER, product_id=PRODUCT):
    db = AsyncMongoMockClient().supplier_side_projection
    await db[PRODUCTS].insert_one({**product(with_policy), 'user_id': owner, 'salla_product_id': product_id})
    await db[pieces.BINDINGS].insert_many([
        {**row, 'user_id': owner, 'salla_product_id': product_id} for row in bindings()])
    await db[pieces.RESOURCES].insert_many([{**row, 'user_id': owner} for row in resources().values()])
    await db[pieces.PRODUCT_RESOURCE_BINDINGS].insert_one({
        'user_id': owner, 'salla_product_id': product_id, 'resource_id': 'product-service', 'quantity': 1,
    })
    return db


async def cost_rows(db):
    return {name: await db[name].find({}, {'_id': 0}).to_list(100)
            for name in (pieces.BINDINGS, pieces.RESOURCES, pieces.PRODUCT_RESOURCE_BINDINGS)}


@pytest.mark.asyncio
async def test_projection_filters_services_by_stable_id_but_keeps_component_and_direct_costs():
    db = await seed()
    rows = bindings() + [{'option_id': 'gift', 'salla_product_id': PRODUCT,
                          'mode': 'direct', 'direct_amount': 12}]
    rows[2]['option_name'] = rows[0]['option_name']  # Labels cannot decide the role.
    before = deepcopy(rows)
    projected = await supplier_service_option_bindings(
        db, user_id=OWNER, bindings=rows, resources_by_id=resources())
    assert projected == [rows[2], rows[3], rows[4]]
    assert rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize('context', CONTEXTS, ids=('base', 'line_services'))
async def test_numeric_salla_product_identity_applies_policy_to_canonical_string_bindings(context):
    db = await seed()
    await db[PRODUCTS].update_one({}, {'$set': {'salla_product_id': 12345}})
    await db[pieces.BINDINGS].update_many({}, {'$set': {'salla_product_id': '12345'}})
    await db[pieces.PRODUCT_RESOURCE_BINDINGS].update_many({}, {'$set': {'salla_product_id': '12345'}})
    source = batch(); source['lines'][0]['product_id'] = '12345'
    plans = await context(db, user_id=OWNER, batch=source)
    assert {row['service_id'] for row in next(iter(plans.values()))['services']} == {
        'product-service', 'engraving-service'}


@pytest.mark.asyncio
async def test_leading_zero_binding_id_does_not_alias_a_different_numeric_product():
    db = await seed()
    await db[PRODUCTS].update_one({}, {'$set': {'salla_product_id': 12345}})
    rows = [{**binding, 'salla_product_id': '012345'} for binding in bindings()]
    assert await supplier_service_option_bindings(
        db, user_id=OWNER, bindings=rows, resources_by_id=resources()) == rows


@pytest.mark.asyncio
@pytest.mark.parametrize('context', CONTEXTS, ids=('base', 'line_services'))
@pytest.mark.parametrize('later_change', ('new_service_link', 'component_becomes_service'))
async def test_future_service_plans_keep_real_preparation_and_customer_answers(context, later_change):
    db = await seed()
    if later_change == 'component_becomes_service':
        await db[pieces.RESOURCES].update_one({'id': 'paper-component'}, {'$set': {'kind': 'service'}})
    else:
        await db[pieces.BINDINGS].delete_one({'id': 'gift'})
        await db[pieces.BINDINGS].insert_one(bindings()[0])
    source = batch(); before = deepcopy(source); costs = await cost_rows(db)
    plans = await context(db, user_id=OWNER, batch=source)
    plan = next(iter(plans.values()))
    assert {row['service_id'] for row in plan['services']} == {'product-service', 'engraving-service'}
    assert source == before
    assert await cost_rows(db) == costs
    identity = SimpleNamespace(options=[SimpleNamespace(**row) for row in source['lines'][0]['file_spec_fields']])
    specs = original_service_spec_fields(identity)
    assert {'name': 'نص الكرت', 'value': 'ذكرى جميلة'} in [
        {'name': row['name'], 'value': row['value']} for row in specs]
    documents = pieces.build_piece_documents(
        user_id=OWNER, registry={'file_number': 'file', 'responsible_employee_id': 'employee'},
        batch=source, services_by_product={PRODUCT: plan}, assigned_at=datetime.now(timezone.utc))
    assert documents[0]['specifications_snapshot'] == source['lines'][0]['file_spec_fields']


@pytest.mark.asyncio
@pytest.mark.parametrize('context', CONTEXTS, ids=('base', 'line_services'))
@pytest.mark.parametrize('catalog_state', ('no_policy', 'missing_product', 'foreign_policy'))
async def test_no_owner_policy_preserves_legacy_name_only_service_links(context, catalog_state):
    db = await seed(with_policy=False)
    if catalog_state != 'no_policy':
        await db[PRODUCTS].delete_many({})
    if catalog_state == 'foreign_policy':
        foreign = product(); foreign['user_id'] = 'different-owner'
        await db[PRODUCTS].insert_one(foreign)
    await db[pieces.BINDINGS].update_many({}, {'$unset': {'option_id': ''}})
    plans = await context(db, user_id=OWNER, batch=batch())
    assert {row['service_id'] for row in next(iter(plans.values()))['services']} == {
        'product-service', 'card-service', 'text-service', 'engraving-service'}


@pytest.mark.asyncio
@pytest.mark.parametrize('context', CONTEXTS, ids=('base', 'line_services'))
@pytest.mark.parametrize('invalid', ('missing_option_id', 'unknown_option_id', 'bad_policy', 'ambiguous_product'))
async def test_unresolved_local_policy_blocks_new_service_plan_clearly(context, invalid):
    db = await seed()
    if invalid == 'missing_option_id':
        await db[pieces.BINDINGS].update_one({'id': 'gift'}, {'$unset': {'option_id': ''}})
    elif invalid == 'unknown_option_id':
        await db[pieces.BINDINGS].update_one({'id': 'gift'}, {'$set': {'option_id': 'removed'}})
    elif invalid == 'bad_policy':
        await db[PRODUCTS].update_one({}, {'$set': {'mezan_inventory_option_policy.roles.gift': []}})
    else:
        duplicate = product(False); duplicate['mezan_product_id'] = 'duplicate-product'
        await db[PRODUCTS].insert_one(duplicate)
    before = await cost_rows(db)
    with pytest.raises(HTTPException) as error:
        await context(db, user_id=OWNER, batch=batch())
    assert error.value.status_code == 409
    assert error.value.detail['code'] in {
        'supplier_option_policy_binding_unresolved', 'supplier_option_policy_invalid',
        'supplier_option_policy_product_ambiguous',
    }
    assert await cost_rows(db) == before


@pytest.mark.asyncio
async def test_materialized_historical_piece_keeps_service_and_customer_snapshots():
    db = await seed(); source = batch()
    old_plan = {'services': pieces.inherit_required_services(
        line=source['lines'][0], product_links=[], option_bindings=bindings(), resources_by_id=resources())}
    arguments = dict(user_id=OWNER, registry={'file_number': 'file', 'responsible_employee_id': 'employee'},
                     batch=source, assigned_at=datetime.now(timezone.utc))
    old_piece = pieces.build_piece_documents(**arguments, services_by_product={PRODUCT: old_plan})[0]
    await db[pieces.PIECES].insert_one(deepcopy(old_piece))
    plans = await _line_service_context_for_batch(db, user_id=OWNER, batch=source)
    future_piece = pieces.build_piece_documents(**arguments, services_by_product={PRODUCT: next(iter(plans.values()))})[0]
    assert 'card-service' not in {row['service_id'] for row in future_piece['services']}
    # Exercise the existing insert-only boundary used by materialization replay.
    await db[pieces.PIECES].update_one({'piece_id': old_piece['piece_id']}, pieces._piece_upsert_update(future_piece), upsert=True)
    stored = await db[pieces.PIECES].find_one({'piece_id': old_piece['piece_id']})
    assert stored['services'] == old_piece['services']
    assert stored['specifications_snapshot'] == old_piece['specifications_snapshot']
    assert stored['service_count'] == old_piece['service_count']


@pytest.mark.asyncio
@pytest.mark.parametrize('context', CONTEXTS, ids=('base', 'line_services'))
async def test_informational_services_preserve_supplier_bundle_pieces_and_planned_cost(context):
    unit = supplier_bundles.prepare_supplier_leaf_unit(supplier_bundle_unit())
    owner = unit['user_id']
    db = await seed(owner=owner, product_id=unit['product_id'])
    await db[pieces.PRODUCT_RESOURCE_BINDINGS].delete_many({})
    await db[pieces.BINDINGS].delete_one({'id': 'engrave'})
    source = batch()
    source['lines'][0].update(product_id=unit['product_id'], order_number=unit['order_id'],
                              order_item_id=unit['order_line_id'])
    before_source, before_unit, costs = deepcopy(source), deepcopy(unit), await cost_rows(db)

    plan = next(iter((await context(db, user_id=owner, batch=source)).values()))
    assert plan['services'] == []
    physical = supplier_bundles.build_supplier_leaf_pieces(
        owner=owner, unit=unit, template={**supplier_bundle_template(), 'services': plan['services']})

    assert pieces.validate_materialized_piece_count(
        batch={**source, 'allocated_quantity': 1, 'physical_quantity': 2},
        registry={'allocated_quantity': 1, 'physical_quantity': 2}, pieces=physical) == 2
    assert len({piece['piece_id'] for piece in physical}) == 2
    assert [(piece[supplier_bundles.REFERENCE_FIELD]['leaf_id'],
             piece[supplier_bundles.REFERENCE_FIELD]['leaf_unit_index'],
             piece['quantity'], piece['unit_index'], piece['supplier_id']) for piece in physical] == [
        ('leaf-batman', 1, 1, 1, 'supplier'), ('leaf-catwoman', 1, 1, 1, 'supplier')]
    assert all(piece[supplier_bundles.REFERENCE_FIELD]['unit_id'] == unit['_id']
               and piece[supplier_bundles.REFERENCE_FIELD]['plan_id'] == unit['plan_id']
               and piece['services'] == [] for piece in physical)
    # Frozen catalog estimates are preserved; they are not receipt/payment proof.
    assert [Decimal(leaf['unit_cost']) for leaf in unit['bundle_leaves']] == [Decimal('50'), Decimal('50')]
    assert sum(Decimal(leaf['quantity']) * Decimal(leaf['unit_cost'])
               for leaf in unit['bundle_leaves']) == Decimal('100')
    assert all(leaf['actual_cost'] is None and not leaf.get('supplier_receipt_proofs')
               for leaf in unit['bundle_leaves'])
    assert source == before_source and unit == before_unit
    assert await cost_rows(db) == costs


@pytest.mark.asyncio
@pytest.mark.parametrize('context', CONTEXTS, ids=('base', 'line_services'))
async def test_real_services_still_block_supplier_bundle_materialization(context):
    unit = supplier_bundles.prepare_supplier_leaf_unit(supplier_bundle_unit())
    owner = unit['user_id']
    db = await seed(owner=owner, product_id=unit['product_id'])
    source = batch()
    source['lines'][0]['product_id'] = unit['product_id']
    before_unit, costs = deepcopy(unit), await cost_rows(db)
    plan = next(iter((await context(db, user_id=owner, batch=source)).values()))
    assert {row['service_id'] for row in plan['services']} == {'product-service', 'engraving-service'}

    with pytest.raises(HTTPException) as error:
        supplier_bundles.build_supplier_leaf_pieces(
            owner=owner, unit=unit, template={**supplier_bundle_template(), 'services': plan['services']})
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'bundle_supplier_materialization_context_invalid'
    assert unit == before_unit
    assert await cost_rows(db) == costs
