from copy import deepcopy
import pytest
import pytest_asyncio
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import bundle_catalog_v2 as b
from product_v2_routes import PRODUCTS


@pytest_asyncio.fixture
async def db(monkeypatch):
    db = AsyncMongoMockClient().bundle_catalog
    async def local_owner(db, owner, callback, **kwargs):
        return await callback(db)
    monkeypatch.setattr(b, 'operational_owner', local_owner)
    for key in ['root', 'nested', 'p1', 'p2', 'p3', 'p4']:
        await db[PRODUCTS].insert_one({'user_id': 'owner', 'salla_product_id': key,
            'mezan_product_id': 'mpv2_' + key, 'name': key, 'cost_price_from_salla': 10,
            'variants': []})
    for key in ['s1', 's2', 's3', 's4']:
        await db['mezan_suppliers_v2'].insert_one({'user_id': 'owner', 'id': key})
    return db


def item(key, **kwargs):
    return {'kind': 'product', 'product_id': key, 'quantity': '1', **kwargs}


async def save_v2(db, rows, revision=0):
    return await b.save_bundle(db, 'owner', 'root', {'expected_revision': revision, 'product_type': 'composite', 'schema_version': 2, 'items': rows}, 'actor')


async def native_component(db, product, rid, cost, policy=None):
    await db[b.RESOURCES].insert_one({'user_id': 'owner', 'id': rid, 'name': rid, 'unit_cost': cost, 'track_inventory': True, 'unit': 'piece'})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id': 'owner', 'salla_product_id': product, 'resource_id': rid, 'id': 'bind-' + rid, 'quantity': 1})
    if policy is not None:
        await db[b.COST_PROFILES].update_one({'user_id': 'owner', 'salla_product_id': product}, {'$set': {'composition_components.product_resource:bind-' + rid: policy}}, upsert=True)


def excluded(rid):
    return {'binding_kind': 'product_resource', 'binding_id': 'bind-' + rid, 'use': False}


@pytest.mark.asyncio
async def test_v2_hoodie_necklace_exclusions_and_frozen_evidence(db):
    independent = {'cost_treatment': 'independent', 'assembly_origin': 'inhouse', 'evidence_ref': 'owner-approved'}
    for product, rid, cost in [('p1', 'hoodie-bag', 2), ('p2', 'necklace-box', 3), ('p2', 'necklace-bag', 1)]:
        await native_component(db, product, rid, cost, independent)
    for product, cost in [('p1', 50), ('p2', 30)]:
        await db[b.COST_PROFILES].update_one({'user_id': 'owner', 'salla_product_id': product}, {'$set': {'base_cost': cost}})
    await db[b.RESOURCES].insert_one({'user_id': 'owner', 'id': 'wrap', 'name': 'wrap', 'unit_cost': 5, 'track_inventory': True})
    originals = await db[b.PRODUCT_RESOURCE_BINDINGS].find({}, {'_id': 0}).to_list(20)
    result = await save_v2(db, [item('p1', component_overrides=[excluded('hoodie-bag')]), item('p2', component_overrides=[excluded('necklace-bag')]), {'kind': 'resource', 'resource_id': 'wrap'}])
    expansion = result['expansion']
    assert b.number(expansion['estimated_cost']) == 88
    assert {x['resource_id'] for x in expansion['leaves'] if x['kind'] == 'resource'} == {'necklace-box', 'wrap'}
    assert len([x for x in expansion['component_decisions'] if not x['use']]) == 2
    assert await db[b.PRODUCT_RESOURCE_BINDINGS].find({}, {'_id': 0}).to_list(20) == originals
    await db[b.COST_PROFILES].update_many({}, {'$set': {'composition_components': {}, 'base_cost': 100}})
    replay = await b.expand_bundle(db, 'owner', 'root', frozen_definitions=expansion['frozen_definitions'])
    assert replay['component_decisions'] == expansion['component_decisions']
    assert b.number(replay['estimated_cost']) == 88


@pytest.mark.asyncio
async def test_v2_fixed_purchase_price_exclusion_no_extra_resource(db):
    await native_component(db, 'p1', 'bag', 2, {'cost_treatment': 'included_in_base', 'assembly_origin': 'purchased_complete', 'evidence_ref': 'fixed-quote'})
    result = await save_v2(db, [item('p1', component_overrides=[excluded('bag')])])
    assert b.number(result['expansion']['estimated_cost']) == 10
    assert [x['kind'] for x in result['expansion']['leaves']] == ['product']


@pytest.mark.asyncio
async def test_v2_missing_policy_is_unresolved_and_blocks_execution(db):
    await native_component(db, 'p1', 'bag', 2)
    result = await save_v2(db, [item('p1')])
    assert result['expansion']['estimated_cost'] is None
    assert result['expansion']['unresolved_components'][0]['reason'] == 'component_provenance_required'
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db, 'owner', 'root')
    assert exc.value.detail['code'] == 'bundle_component_policy_unresolved'


@pytest.mark.asyncio
async def test_v2_intentional_rules_sum_and_duplicate_rule_rejected(db):
    result = await save_v2(db, [item('p1', rule_id='one'), item('p1', rule_id='two')])
    assert result['expansion']['leaves'][0]['quantity'] == '2'
    with pytest.raises(HTTPException) as exc:
        await save_v2(db, [item('p1', rule_id='same'), item('p2', rule_id='same')], revision=1)
    assert exc.value.detail['code'] == 'bundle_duplicate_item'


@pytest.mark.asyncio
async def test_v2_same_variant_different_options_keep_independent_supplier_resolution(db):
    from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES
    from bundle_fulfillment_v2 import allocate_leaves
    await db[PRODUCTS].update_one({'salla_product_id': 'p1'}, {'$set': {
        'variants': [{'id': 'same'}], 'options': [{'id': 'work', 'values': [{'id': 'a'}, {'id': 'b'}]}]}})
    await db[PRODUCT_OPERATION_PROFILES].insert_one({'user_id': 'owner', 'salla_product_id': 'p1',
        'bundle_sourcing': {'revision': 1, 'rules': [
            {'rule_id': value, 'conditions': [{'option_id': 'work', 'value_id': value}], 'supplier_id': supplier}
            for value, supplier in [('a', 's1'), ('b', 's2')]]}})
    def selected(value):
        return item('p1', variant_id='same', selected_options=[{'option_id': 'work', 'value_id': value}])
    result = await save_v2(db, [selected('a'), selected('b')])
    leaves = result['expansion']['leaves']
    assert len(leaves) == 2
    assert [leaf['quantity'] for leaf in leaves] == ['1', '1']
    resolved, _ = await allocate_leaves(db, 'owner', leaves, [], unit_id='synthetic-unit')
    assert {leaf['supplier_id']: leaf['supplier_quantity'] for leaf in resolved} == {'s1': '1', 's2': '1'}
    identical = await save_v2(db, [selected('a'), selected('a')], revision=1)
    assert len(identical['expansion']['leaves']) == 1
    assert identical['expansion']['leaves'][0]['quantity'] == '2'


@pytest.mark.asyncio
async def test_v2_supplier_omission_requires_evidence_and_foreign_override_rejected(db):
    await native_component(db, 'p1', 'bag', 2, {'cost_treatment': 'included_in_base', 'assembly_origin': 'supplier'})
    result = await save_v2(db, [item('p1', component_overrides=[excluded('bag')])])
    assert result['expansion']['unresolved_components'][0]['reason'] == 'supplier_omission_unconfirmed'
    with pytest.raises(HTTPException) as exc:
        await save_v2(db, [item('p2', component_overrides=[excluded('bag')])], revision=1)
    assert exc.value.detail['code'] == 'bundle_component_override_not_found'


@pytest.mark.asyncio
async def test_v2_extra_explicit_packaging_and_product_supplier_are_independent(db):
    await native_component(db, 'p1', 'bag', 2, {'cost_treatment': 'independent', 'assembly_origin': 'inhouse', 'evidence_ref': 'approved'})
    result = await save_v2(db, [item('p1', sourcing_mode='supplier', supplier_id='s1'), {'kind': 'resource', 'resource_id': 'bag'}])
    expansion = result['expansion']
    bag = next(x for x in expansion['leaves'] if x['kind'] == 'resource')
    assert bag['quantity'] == '2'
    assert bag['sourcing_mode'] == 'stock' and bag['supplier_id'] is None
    assert b.number(expansion['estimated_cost']) == 14


@pytest.mark.asyncio
async def test_v2_current_product_binding_scope_and_exclusion(db):
    await native_component(db, 'root', 'bag', 2, {'cost_treatment': 'independent', 'assembly_origin': 'inhouse', 'evidence_ref': 'approved'})
    result = await b.save_bundle(db, 'owner', 'root', {'expected_revision': 0, 'product_type': 'composite', 'schema_version': 2,
        'component_scopes': {'bag': 'per_unit'}, 'items': [
            {'kind': 'current_product', 'quantity': '2', 'rule_id': 'two'},
            {'kind': 'current_product', 'quantity': '1', 'rule_id': 'excluded', 'component_overrides': [excluded('bag')]}]}, 'actor')
    assert b.number(result['expansion']['estimated_cost']) == 34
    bag = next(x for x in result['expansion']['leaves'] if x['kind'] == 'resource')
    assert bag['quantity'] == '2'
    assert result['expansion']['current_product_units_per_selection'] == '3'


@pytest.mark.asyncio
async def test_v2_auto_policy_and_options_are_frozen(db):
    policy = {'revision': 1, 'stock_first': True, 'allow_split': True, 'supplier_id': 's1'}
    await db[b.PRODUCT_OPERATION_PROFILES].insert_one({'user_id': 'owner', 'salla_product_id': 'p1', 'bundle_sourcing': policy})
    result = await save_v2(db, [item('p1')])
    assert result['expansion']['leaves'][0]['sourcing_mode'] == 'auto'
    assert result['expansion']['leaves'][0]['sourcing_policy'] == policy
    await db[b.PRODUCT_OPERATION_PROFILES].update_one({'salla_product_id': 'p1'}, {'$set': {'bundle_sourcing': {}}})
    replay = await b.expand_bundle(db, 'owner', 'root', frozen_definitions=result['expansion']['frozen_definitions'])
    assert replay['leaves'][0]['sourcing_policy'] == policy


@pytest.mark.asyncio
async def test_v2_preview_projects_split_without_writing_or_mutating_snapshot(db, monkeypatch):
    from decimal import Decimal
    import bundle_fulfillment_v2 as fulfillment
    policy = {'revision': 1, 'stock_first': True, 'allow_split': True, 'supplier_id': 's1'}
    await db[b.PRODUCT_OPERATION_PROFILES].insert_one({'user_id': 'owner', 'salla_product_id': 'p1', 'bundle_sourcing': policy})
    await native_component(db, 'p1', 'unused-bag', 2, {'cost_treatment': 'independent', 'assembly_origin': 'inhouse', 'evidence_ref': 'approved'})
    expansion = (await save_v2(db, [item('p1', quantity='4', component_overrides=[excluded('unused-bag')])]))['expansion']
    original = deepcopy(expansion)
    lots = [{'location_id': 'a', 'lot_id': 'receipt', 'item': {'item_type': 'product', 'product_id': 'mpv2_p1', 'salla_product_id': 'p1', 'variant_id': None}, 'available': Decimal(2)}]
    async def read_lots(*args, **kwargs):
        assert kwargs['pin'] is False
        return lots
    monkeypatch.setattr(fulfillment, 'inventory_lots', read_lots)
    async def snapshot():
        return {name: await db[name].find({}).to_list(1000) for name in await db.list_collection_names()}
    before = await snapshot()
    projected = await b.annotate_inventory(db, 'owner', expansion)
    assert await snapshot() == before
    assert expansion == original and lots[0]['available'] == Decimal(2)
    assert projected['component_decisions'] == original['component_decisions']
    assert projected['estimated_cost'] == original['estimated_cost']
    leaf = projected['leaves'][0]
    assert leaf['preview_only'] is True
    assert leaf['reserved_quantity'] == '2' and leaf['supplier_quantity'] == '2'
    assert leaf['pending_quantity'] == '2' and leaf['state'] == 'awaiting_supplier'
    assert {s['sourcing_mode'] for s in leaf['sourcing_segments']} == {'stock', 'supplier'}


@pytest.mark.asyncio
async def test_v2_preview_unknown_source_reports_needs_source_without_writes(db):
    expansion = (await save_v2(db, [item('p1')]))['expansion']
    before = {name: await db[name].find({}).to_list(1000) for name in await db.list_collection_names()}
    projected = await b.annotate_inventory(db, 'owner', expansion)
    after = {name: await db[name].find({}).to_list(1000) for name in await db.list_collection_names()}
    assert {name: rows for name, rows in after.items() if rows} == {name: rows for name, rows in before.items() if rows}
    assert projected['leaves'][0]['state'] == 'needs_source'
    assert projected['leaves'][0]['pending_quantity'] == '1'


@pytest.mark.asyncio
async def test_v2_nested_composite_native_exclusion_is_local(db):
    await native_component(db, 'nested', 'outer-bag', 2, {'cost_treatment': 'independent', 'assembly_origin': 'inhouse', 'evidence_ref': 'approved'})
    await save(db, 'nested', [item('p1')])
    standalone = await b.expand_bundle(db, 'owner', 'nested')
    result = await save_v2(db, [item('nested', component_overrides=[excluded('outer-bag')])])
    assert b.number(result['expansion']['estimated_cost']) == 10
    assert result['expansion']['component_decisions'][0]['use'] is False
    assert (await b.expand_bundle(db, 'owner', 'nested')) == standalone


@pytest.mark.asyncio
async def test_v2_current_unknown_policy_saves_then_blocks_execution(db):
    await native_component(db, 'root', 'bag', 2)
    result = await b.save_bundle(db, 'owner', 'root', {'expected_revision': 0, 'product_type': 'composite', 'schema_version': 2,
        'component_scopes': {'bag': 'per_unit'}, 'items': [{'kind': 'current_product', 'quantity': '2'}]}, 'actor')
    assert result['definition']['revision'] == 1
    assert result['expansion']['estimated_cost'] is None
    assert result['expansion']['unresolved_components'][0]['quantity'] == '2'
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db, 'owner', 'root')
    assert exc.value.detail['code'] == 'bundle_component_policy_unresolved'


@pytest.mark.asyncio
async def test_v2_current_shared_override_is_applied_or_conflict_visible(db):
    await native_component(db, 'root', 'bag', 2, {'cost_treatment': 'independent', 'assembly_origin': 'inhouse', 'evidence_ref': 'approved'})
    payload = {'expected_revision': 0, 'product_type': 'composite', 'schema_version': 2, 'component_scopes': {'bag': 'per_bundle'},
               'items': [{'kind': 'current_product', 'quantity': '2', 'component_overrides': [excluded('bag')]}]}
    result = await b.save_bundle(db, 'owner', 'root', payload, 'actor')
    assert b.number(result['expansion']['estimated_cost']) == 20
    assert not any(x['kind'] == 'resource' for x in result['expansion']['leaves'])
    payload['expected_revision'] = 1
    payload['items'].append({'kind': 'current_product', 'quantity': '1'})
    result = await b.save_bundle(db, 'owner', 'root', payload, 'actor')
    assert result['expansion']['estimated_cost'] is None
    assert result['expansion']['unresolved_components'][0]['reason'] == 'shared_component_override_conflict'


async def mapping_catalog(db):
    await db[PRODUCTS].update_one({'salla_product_id': 'root'}, {'$set': {'options': [
        {'id': 'size', 'values': [{'id': 'm'}, {'id': 'l'}]},
        {'id': 'color', 'values': [{'id': 'silver'}, {'id': 'gold'}]}, {'id': 'gift', 'type': 'text'}]}})
    await db[PRODUCTS].update_one({'salla_product_id': 'p1'}, {'$set': {'options': [
        {'id': 'child-color', 'required': True, 'values': [{'id': 's'}, {'id': 'g'}]},
        {'id': 'engraving', 'type': 'text', 'required': True}], 'variants': [{'id': 'M'}, {'id': 'L'}]}})
    return item('p1', variant_rules=[{'conditions': [{'option_id': 'size', 'value_id': 'm'}], 'variant_id': 'M'}, {'conditions': [{'option_id': 'size', 'value_id': 'l'}], 'variant_id': 'L'}],
        option_mappings=[{'option_id': 'color', 'value_id': 'silver', 'target_option_id': 'child-color', 'target_value_id': 's'}, {'option_id': 'color', 'value_id': 'gold', 'target_option_id': 'child-color', 'target_value_id': 'g'}],
        customization_mappings=[{'option_id': 'gift', 'target_option_id': 'engraving'}])


@pytest.mark.asyncio
async def test_v2_maps_parent_size_color_and_text_to_existing_child(db):
    row = await mapping_catalog(db)
    result = await save_v2(db, [row])
    assert result['expansion']['needs_selection'] and result['expansion']['estimated_cost'] is None
    selected = [{'option_id': 'size', 'value_id': 'm'}, {'option_id': 'color', 'value_id': 'silver'}]
    expansion = await b.expand_bundle(db, 'owner', 'root', selected_options=selected, option_answers={'gift': 'عبير'}, present_option_ids=['gift'], filled_option_ids=['gift'])
    leaf = expansion['leaves'][0]
    assert leaf['variant_id'] == 'M'
    assert leaf['selected_options'] == [{'option_id': 'child-color', 'value_id': 's'}]
    assert leaf['customization'] == {'engraving': 'عبير'}
    assert leaf['sourcing_mode'] == 'auto'
    assert expansion['cost_complete'] is True
    assert (await db[PRODUCTS].find_one({'salla_product_id': 'p1'}))['variants'] == [{'id': 'M'}, {'id': 'L'}]


@pytest.mark.asyncio
async def test_v2_mapping_conflicts_preview_unresolved_and_strict_rejected(db):
    row = await mapping_catalog(db)
    row['variant_rules'].append({'conditions': [{'option_id': 'size', 'value_id': 'm'}], 'variant_id': 'L'})
    await save_v2(db, [row])
    selected = [{'option_id': 'size', 'value_id': 'm'}]
    preview = await b.expand_bundle(db, 'owner', 'root', selected_options=selected, strict=False)
    assert preview['missing_options'][0]['reason'] == 'variant_mapping_conflict'
    assert preview['estimated_cost'] is None
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db, 'owner', 'root', selected_options=selected)
    assert exc.value.detail['code'] == 'bundle_selection_required'


@pytest.mark.asyncio
async def test_v2_mapping_rejects_foreign_alternative_even_when_not_selected(db):
    row = await mapping_catalog(db)
    row['option_mappings'][1]['target_value_id'] = 'foreign'
    with pytest.raises(HTTPException) as exc:
        await save_v2(db, [row])
    assert exc.value.detail['code'] == 'bundle_option_value_not_found'


@pytest.mark.asyncio
@pytest.mark.parametrize('side,field_type', [('root', 'file'), ('root', 'date'), ('p1', 'file'), ('p1', 'date')])
async def test_v2_text_mapping_rejects_nontext_source_and_target(db, side, field_type):
    row = await mapping_catalog(db)
    product = await db[PRODUCTS].find_one({'salla_product_id': side})
    oid = 'gift' if side == 'root' else 'engraving'
    for option in product['options']:
        if option['id'] == oid:
            option['type'] = field_type
    await db[PRODUCTS].update_one({'salla_product_id': side}, {'$set': {'options': product['options']}})
    with pytest.raises(HTTPException) as exc:
        await save_v2(db, [row])
    assert exc.value.detail['code'] == 'bundle_invalid_customization_mapping'


@pytest.mark.asyncio
async def test_v2_text_mapping_preserves_historical_missing_field_type(db):
    row = await mapping_catalog(db)
    for key in ['root', 'p1']:
        product = await db[PRODUCTS].find_one({'salla_product_id': key})
        for option in product['options']:
            option.pop('type', None)
        await db[PRODUCTS].update_one({'salla_product_id': key}, {'$set': {'options': product['options']}})
    await save_v2(db, [row])
    result = await b.expand_bundle(db, 'owner', 'root', selected_options=[{'option_id': 'size', 'value_id': 'm'}, {'option_id': 'color', 'value_id': 'gold'}],
                                   option_answers={'gift': 'عبير'}, present_option_ids=['gift'], filled_option_ids=['gift'])
    assert result['leaves'][0]['customization'] == {'engraving': 'عبير'}


async def save(db, key, items, revision=0):
    return await b.save_bundle(db, 'owner', key, {'expected_revision': revision, 'product_type': 'composite', 'items': items}, 'actor')


@pytest.mark.asyncio
async def test_four_suppliers_and_nested_snapshot(db):
    result = await save(db, 'nested', [item('p'+str(i), sourcing_mode='supplier', supplier_id='s'+str(i)) for i in range(1,5)])
    assert len(result['expansion']['leaves']) == 4
    result = await save(db, 'root', [item('nested', quantity='2')])
    expansion = result['expansion']
    assert expansion['estimated_cost'] == '80.0'
    assert {x['supplier_id'] for x in expansion['leaves']} == {'s1','s2','s3','s4'}
    await save(db, 'nested', [item('p1')], revision=1)
    assert (await b.expand_bundle(db, 'owner', 'root'))['estimated_cost'] == '20.0'
    frozen = await b.expand_bundle(db, 'owner', 'root', frozen_definitions=expansion['frozen_definitions'])
    assert frozen['estimated_cost'] == '80.0'
    assert len(expansion['leaves']) == 4


@pytest.mark.asyncio
async def test_alias_cycle_and_revision_conflict(db):
    await save(db, 'nested', [item('root')])
    with pytest.raises(HTTPException) as exc:
        await save(db, 'root', [item('mpv2_nested')])
    assert exc.value.detail['code'] == 'bundle_cycle'
    with pytest.raises(HTTPException) as exc:
        await save(db, 'nested', [item('p1')])
    assert exc.value.detail['code'] == 'bundle_revision_conflict'


@pytest.mark.asyncio
async def test_validation_tenant_variants_and_limits(db):
    for rows, code in [([item('p1', variant_id='missing')], 'bundle_variant_not_found'),
                       ([item('p1', quantity='NaN')], 'bundle_invalid_number'),
                       ([item('p1')] * 101, 'bundle_item_limit'),
                       ([item('p1', sourcing_mode='supplier', supplier_id='foreign')], 'supplier_v2_identity_required')]:
        with pytest.raises(HTTPException) as exc:
            await save(db, 'root', rows)
        assert exc.value.detail['code'] == code
    await db[PRODUCTS].insert_one({'user_id': 'foreign', 'salla_product_id': 'secret'})
    with pytest.raises(HTTPException) as exc:
        await save(db, 'root', [item('secret')])
    assert exc.value.status_code == 404
    await db[PRODUCTS].update_one({'salla_product_id':'p1'}, {'$set':{'variants':[{'id':'v1','cost_price_from_salla':12}]}})
    assert (await save(db, 'root', [item('p1', variant_id='v1')]))['expansion']['estimated_cost'] == '12.0'


@pytest.mark.asyncio
async def test_required_conditions_once_and_explicit_sum(db):
    await db[PRODUCTS].update_one({'salla_product_id': 'root'}, {'$set': {'options': [{'id':'o', 'required':True, 'values':[{'id':'v'}, {'id':'w'}]}]}})
    condition = {'option_id':'o','value_id':'v'}
    rows = [item('p1'), item('p1', condition=condition), item('p2', condition={'option_id':'o','value_id':'w'})]
    result = await save(db, 'root', rows)
    assert result['expansion']['needs_selection'] is True
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db, 'owner', 'root')
    assert exc.value.detail['code'] == 'bundle_selection_required'
    selected = await b.expand_bundle(db, 'owner', 'root', selected_options=[condition])
    assert len(selected['leaves']) == 1
    assert selected['leaves'][0]['quantity'] == '1'
    assert 'p2' in selected['frozen_definitions']
    rows[1]['repeat_policy'] = 'sum'
    await save(db, 'root', rows, revision=1)
    selected = await b.expand_bundle(db, 'owner', 'root', selected_options=[condition])
    assert selected['leaves'][0]['quantity'] == '2'


@pytest.mark.asyncio
async def test_resource_overlap_and_missing_cost(db):
    await db[b.RESOURCES].insert_one({'user_id':'owner', 'id':'r', 'name':'r', 'unit_cost':3})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner', 'salla_product_id':'p1', 'resource_id':'r', 'quantity':2})
    result = await save(db,'root',[item('p1')])
    assert result['expansion']['estimated_cost'] == '16.0'
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('p1'),{'kind':'resource','resource_id':'r'}],revision=1)
    assert exc.value.detail['code'] == 'bundle_resource_cost_overlap'
    await db[PRODUCTS].update_one({'salla_product_id':'p2'},{'$unset':{'cost_price_from_salla':''}})
    result = await save(db,'root',[item('p2')], revision=1)
    assert result['expansion']['estimated_cost'] is None


@pytest.mark.asyncio
async def test_depth_limit_and_many_items(db):
    for i in range(10):
        await db[PRODUCTS].insert_one({'user_id':'owner','salla_product_id':f'x{i}','cost_price_from_salla':1})
    result = await save(db,'root',[item(f'x{i}') for i in range(10)])
    assert len(result['expansion']['leaves']) == 10
    for i in range(5):
        await save(db,f'x{i}',[item(f'x{i+1}')])
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('x0')],revision=1)
    assert exc.value.detail['code'] == 'bundle_depth_limit'



@pytest.mark.asyncio
async def test_frozen_costs_bindings_and_unselected_alternatives(db):
    await db[PRODUCTS].update_one({'salla_product_id':'root'}, {'$set':{'options':[{'id':'o','values':[{'id':'v'},{'id':'w'}]}]}})
    choices = [{'option_id':'o','value_id':v} for v in ['v','w']]
    result = await save(db,'root',[item('p1',condition=choices[0]),item('p2',condition=choices[1])])
    frozen = result['expansion']['frozen_definitions']
    await db[b.COST_PROFILES].insert_one({'user_id':'owner','salla_product_id':'p2','base_cost':999})
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'new','unit_cost':99})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner','salla_product_id':'p2','resource_id':'new'})
    old = await b.expand_bundle(db,'owner','root',selected_options=[choices[1]],frozen_definitions=frozen)
    current = await b.expand_bundle(db,'owner','root',selected_options=[choices[1]])
    assert old['estimated_cost'] == '10.0'
    assert current['estimated_cost'] == '1098.0'
    assert len(old['leaves']) == 1


@pytest.mark.asyncio
async def test_hidden_required_salla_option(db):
    opts = [{'id':'o','required':True,'values':[{'id':'v'},{'id':'w'}]},
            {'id':'conditional','required':True,'values':[{'id':'yes'}], 'visibility':'on_condition',
             'visibility_condition_type':'=', 'visibility_condition_option':'o', 'visibility_condition_value':'v'}]
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':opts}})
    await save(db,'root',[item('p1',condition={'option_id':'conditional','value_id':'yes'})])
    result = await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'o','value_id':'w'}])
    assert result['leaves'] == []
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'o','value_id':'v'}])
    assert exc.value.detail['code'] == 'bundle_selection_required'
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'o','value_id':'w'},{'option_id':'conditional','value_id':'yes'}])
    assert exc.value.detail['code'] == 'bundle_hidden_option_selected'


@pytest.mark.asyncio
async def test_nested_optional_parent_and_quantity_conflict(db):
    await save(db,'nested',[item('p1')])
    result = await save(db,'root',[item('nested', required=False)])
    assert result['expansion']['leaves'][0]['required'] is False
    result = await save(db,'root',[item('nested'),item('p1',quantity='2')],revision=1)
    assert result['expansion']['leaves'][0]['quantity'] == '3'


@pytest.mark.asyncio
async def test_selected_option_cost_resource_and_direct(db):
    await db[PRODUCTS].update_one({'salla_product_id':'p1'},{'$set':{'options':[{'id':'o','values':[{'id':'v'}]}]}})
    await db[b.BINDINGS].insert_one({'user_id':'owner','salla_product_id':'p1','option_id':'o','value_id':'v','mode':'direct','direct_amount':4,'quantity':2})
    result = await save(db,'root',[item('p1', selected_options=[{'option_id':'o','value_id':'v'}])])
    assert result['expansion']['estimated_cost'] == '18.0'


@pytest.mark.asyncio
async def test_inactive_component_and_bad_condition(db):
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'inactive','status':'inactive','unit_cost':1})
    with pytest.raises(HTTPException):
        await save(db,'root',[{'kind':'resource','resource_id':'inactive'}])
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('p1',condition={'option_id':'missing','value_id':'v'})])
    assert exc.value.detail['code'] == 'bundle_option_value_not_found'


@pytest.mark.asyncio
async def test_shared_nested_branches_add_physical_quantities(db):
    await db[PRODUCTS].insert_one({'user_id':'owner','salla_product_id':'other','cost_price_from_salla':1})
    await save(db,'nested',[item('p1',quantity='2')])
    await save(db,'other',[item('nested',quantity='3')])
    result = await save(db,'root',[item('nested'),item('other')])
    assert len(result['expansion']['leaves']) == 1
    assert result['expansion']['leaves'][0]['quantity'] == '8'


@pytest.mark.asyncio
async def test_parent_components_packaging_retained(db):
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'pack','unit_cost':3,'track_inventory':True})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner','salla_product_id':'root','resource_id':'pack','quantity':2})
    result = await save(db,'root',[item('p1')])
    assert result['expansion']['estimated_cost'] == '16.0'
    assert next(l for l in result['expansion']['leaves'] if l['kind']=='resource')['track_inventory'] is True
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('p1'),{'kind':'resource','resource_id':'pack'}],revision=1)
    assert exc.value.detail['code'] == 'bundle_resource_cost_overlap'


@pytest.mark.asyncio
async def test_variant_required_and_same_scope_quantity_conflict(db):
    await db[PRODUCTS].update_one({'salla_product_id':'p1'},{'$set':{'variants_count':2}})
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('p1')])
    assert exc.value.detail['code'] == 'bundle_variant_required'
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':[{'id':'o','values':[{'id':'v'}]}]}})
    await save(db,'root',[item('p2'),item('p2',quantity='2',condition={'option_id':'o','value_id':'v'})])
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'o','value_id':'v'}])
    assert exc.value.detail['code'] == 'bundle_duplicate_quantity_conflict'


@pytest.mark.asyncio
async def test_same_component_required_by_distinct_products_adds(db):
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'r','unit_cost':2,'track_inventory':True})
    for key in ['p1','p2']:
        await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner','salla_product_id':key,'resource_id':'r','quantity':1})
    result = await save(db,'root',[item('p1'),item('p2')])
    leaf = next(l for l in result['expansion']['leaves'] if l['kind']=='resource')
    assert leaf['quantity'] == '2'
    assert result['expansion']['estimated_cost'] == '24.0'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['product','resource'])
async def test_missing_reference_cannot_match_missing_mongo_alias(db, kind):
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[{'kind':kind}])
    assert exc.value.detail['code'] == 'bundle_reference_required'


@pytest.mark.asyncio
async def test_root_variant_editor_preview_remains_available(db):
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'variants':[{'id':'v1'}]}})
    result = await save(db,'root',[item('p1')])
    assert result['expansion']['needs_selection'] is True
    assert result['expansion']['estimated_cost'] is None
    assert (await b.expand_bundle(db,'owner','root',variant_id='v1'))['estimated_cost'] == '10.0'


@pytest.mark.asyncio
async def test_parent_direct_option_cost_and_component_subtotal(db):
    choice = {'option_id':'o','value_id':'v'}
    await db[PRODUCTS].update_one({'salla_product_id':'root'}, {'$set':{'price':100,'options':[{'id':'o','values':[{'id':'v','additional_price':50}]}]}})
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'pack','unit_cost':3,'track_inventory':True})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner','salla_product_id':'root','resource_id':'pack','quantity':2})
    await db[b.BINDINGS].insert_one({'id':'cost-binding','user_id':'owner','salla_product_id':'root','option_id':'o','value_id':'v','option_name':'Choice','value_name':'V','mode':'direct','direct_amount':4,'quantity':2})
    await save(db,'root',[item('p1')])
    result = await b.expand_bundle(db,'owner','root',selected_options=[choice])
    assert result['estimated_cost'] == '24.0'
    assert len(result['leaves']) == 3
    cost = next(leaf for leaf in result['leaves'] if leaf['kind']=='direct_cost')
    assert cost['binding_id'] == 'cost-binding'
    assert cost['unit_cost'] == '4'
    assert cost['quantity'] == '2'
    assert cost['cost_only'] and not cost['track_inventory']
    await db[b.BINDINGS].update_one({'id':'cost-binding'},{'$set':{'direct_amount':999}})
    frozen = await b.expand_bundle(db,'owner','root',selected_options=[choice],frozen_definitions=result['frozen_definitions'])
    assert frozen['estimated_cost'] == '24.0'
    product = await db[PRODUCTS].find_one({'salla_product_id':'root'})
    assert product['price'] == 100
    assert product['options'][0]['values'][0]['additional_price'] == 50


@pytest.mark.asyncio
async def test_product_quantity_integral_and_total_quantity_bounded(db):
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('p1',quantity='0.5')])
    assert exc.value.detail['code'] == 'bundle_product_quantity_must_be_integral'
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('p1',quantity='60000'),item('p2',quantity='60000')])
    assert exc.value.detail['code'] == 'bundle_total_quantity_limit'
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'r','unit_cost':2})
    result = await save(db,'root',[{'kind':'resource','resource_id':'r','quantity':'0.5'}])
    assert result['expansion']['leaves'][0]['quantity'] == '0.5'


@pytest.mark.asyncio
async def test_inventory_annotation_is_read_only_and_scoped(db, monkeypatch):
    from decimal import Decimal
    import bundle_fulfillment_v2 as fulfillment
    calls = []
    async def lots(db_arg, owner, warehouses, *, pin=True):
        calls.append((owner, warehouses, pin))
        assert pin is False
        return [{'location_id':loc,'item':{'item_type':'product','product_id':'mpv2_p1','salla_product_id':'p1','variant_id':None},'available':Decimal(amount)}
                for loc, amount in [('a','3'),('b','4')]]
    monkeypatch.setattr(fulfillment, 'inventory_lots', lots)
    await db['mezan_suppliers_v2'].update_one({'id':'s1'},{'$set':{'company_name':'Supplier one'}})
    original = {'leaves':[{'kind':'product','product_id':'p1','mezan_product_id':'mpv2_p1','salla_product_id':'p1','location_id':'a','supplier_id':'s1'},
                          {'kind':'direct_cost','binding_id':'cost','cost_only':True},
                          {'kind':'resource','resource_id':'service','track_inventory':False}]}
    result = await b.annotate_inventory(db,'owner',original)
    assert result['leaves'][0]['available_quantity'] == '3'
    assert result['leaves'][0]['supplier_name'] == 'Supplier one'
    assert result['leaves'][1]['available_quantity'] is None
    assert result['leaves'][2]['available_quantity'] is None
    assert 'available_quantity' not in original['leaves'][0]
    assert calls == [('owner',[],False)]
    assert await b.annotate_inventory(db,'owner',None) is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_frozen_product_aliases_survive_catalog_removal(db):
    result = await save(db,'root',[item('p1')])
    frozen = result['expansion']['frozen_definitions']
    await db[PRODUCTS].delete_one({'user_id':'owner','salla_product_id':'p1'})
    await db[PRODUCTS].delete_one({'user_id':'owner','salla_product_id':'root'})
    expansion = await b.expand_bundle(db,'owner','mpv2_root',frozen_definitions=frozen)
    assert expansion['estimated_cost'] == '10.0'
    assert expansion['leaves'][0]['product_id'] == 'p1'


@pytest.mark.asyncio
async def test_text_presence_visibility_required_fill_and_no_stock_leaf(db):
    opts = [{'id':'choice','values':[{'id':'gift'},{'id':'plain'}]},
            {'id':'message','type':'text','required':True,'values':[], 'visibility':'on_condition',
             'visibility_condition_type':'=', 'visibility_condition_option':'choice','visibility_condition_value':'gift'}]
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':opts}})
    await save(db,'root',[item('p1')])
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'choice','value_id':'plain'}],present_option_ids=['message'])
    assert exc.value.detail['code'] == 'bundle_hidden_option_selected'
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'choice','value_id':'gift'}],present_option_ids=['message'])
    assert exc.value.detail['code'] == 'bundle_selection_required'
    result = await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'choice','value_id':'gift'}],present_option_ids=['message'],filled_option_ids=['message'])
    assert len(result['leaves']) == 1 and result['leaves'][0]['product_id'] == 'p1'
    assert result['present_option_ids'] == ['message']
    assert result['filled_option_ids'] == ['message']
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db,'owner','root',present_option_ids=['unknown'])
    assert exc.value.detail['code'] == 'bundle_unknown_option_presence'


@pytest.mark.asyncio
async def test_circular_option_visibility_fails_closed(db):
    opts = [{'id':key,'values':[{'id':'yes'}], 'visibility':'on_condition','visibility_condition_type':'=',
             'visibility_condition_option':parent,'visibility_condition_value':'yes'} for key,parent in [('a','b'),('b','a')]]
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':opts}})
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[item('p1')])
    assert exc.value.detail['code'] == 'bundle_option_visibility_cycle'


async def save_current(db, items, *, scopes=None, revision=0):
    return await b.save_bundle(db,'owner','root',{'expected_revision':revision,'product_type':'composite','items':items,'component_scopes':scopes or {}},'actor')


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[1,2,3,4,10])
async def test_current_terminal_units_and_packaging_once(db,count):
    await db[b.COST_PROFILES].insert_one({'user_id':'owner','salla_product_id':'root','base_cost':50})
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'pack','unit_cost':20,'track_inventory':True})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner','salla_product_id':'root','resource_id':'pack','quantity':1})
    result = await save_current(db,[{'kind':'current_product','quantity':str(count)}],scopes={'pack':'per_bundle'})
    expansion = result['expansion']
    assert b.number(expansion['estimated_cost']) == count*50+20
    assert expansion['current_product_units_per_selection'] == str(count)
    assert expansion['units_per_selection'] == str(count)
    assert len([l for l in expansion['leaves'] if l.get('current_product_reference')]) == count
    assert next(l for l in expansion['leaves'] if l['kind']=='resource')['quantity'] == '1'


@pytest.mark.asyncio
async def test_current_components_require_explicit_scope_and_per_unit(db):
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'r','unit_cost':3,'track_inventory':True})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner','salla_product_id':'root','resource_id':'r','quantity':2})
    with pytest.raises(HTTPException) as exc:
        await save_current(db,[{'kind':'current_product','quantity':'2'}])
    assert exc.value.detail['code'] == 'bundle_component_scope_required'
    result = await save_current(db,[{'kind':'current_product','quantity':'2'}],scopes={'r':'per_unit'})
    assert next(l for l in result['expansion']['leaves'] if l['kind']=='resource')['quantity'] == '4'
    assert b.number(result['expansion']['estimated_cost']) == 32


@pytest.mark.asyncio
async def test_current_slots_distinct_variants_and_supplier_customization(db):
    options=[{'id':'first','values':[{'id':'m'},{'id':'xl'}]}, {'id':'second','values':[{'id':'m'},{'id':'xl'}]}, {'id':'note','type':'text','values':[]}]
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':options,'variants':[{'id':'M','cost_price_from_salla':50},{'id':'XL','cost_price_from_salla':60}]}})
    slots=[{'slot_id':str(i+1),'variant_rules':[{'conditions':[{'option_id':oid,'value_id':val}],'variant_id':variant} for val,variant in [('m','M'),('xl','XL')]]} for i,oid in enumerate(['first','second'])]
    slots[1]['customization_option_ids']=['note']
    result = await save_current(db,[{'kind':'current_product','quantity':'2','sourcing_mode':'supplier','supplier_id':'s1','slots':slots}])
    assert result['expansion']['needs_selection']
    selected=[{'option_id':'first','value_id':'m'},{'option_id':'second','value_id':'xl'}]
    result = await b.expand_bundle(db,'owner','root',selected_options=selected,option_answers={'note':'  gift  '})
    assert [(l['slot_id'],l['variant_id']) for l in result['leaves']] == [('1','M'),('2','XL')]
    assert result['leaves'][1]['customization']=={'note':'gift'}
    assert b.number(result['estimated_cost'])==110
    frozen=result['frozen_definitions']
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'variants':[],'options':[]}})
    repeated=await b.expand_bundle(db,'owner','root',selected_options=selected,option_answers={'note':'gift'},frozen_definitions=frozen)
    assert repeated['leaves']==result['leaves']


@pytest.mark.asyncio
async def test_current_free_text_supplier_only_and_old_self_cycles(db):
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':[{'id':'size','type':'text','values':[]}]}})
    row={'kind':'current_product','quantity':'1','slots':[{'slot_id':'1','customization_option_ids':['size']}]}
    await save_current(db,[row])
    with pytest.raises(HTTPException) as exc:
        await b.expand_bundle(db,'owner','root',option_answers={'size':'XL'})
    assert exc.value.detail['code']=='bundle_stock_customization_requires_variant'
    await save_current(db,[{**row,'sourcing_mode':'supplier','supplier_id':'s1'}],revision=1)
    result=await b.expand_bundle(db,'owner','root',option_answers={'size':'XL'})
    assert result['leaves'][0]['variant_id'] is None
    assert result['leaves'][0]['customization']=={'size':'XL'}
    with pytest.raises(HTTPException) as exc:
        await save_current(db,[item('mpv2_root')],revision=2)
    assert exc.value.detail['code']=='bundle_cycle'


@pytest.mark.asyncio
async def test_current_conditional_mixed_supplier_stock_and_explicit_resource_scope(db):
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':[{'id':'count','values':[{'id':'one'},{'id':'two'}]}, {'id':'gift','values':[{'id':'yes'},{'id':'no'}]}]}})
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'r','unit_cost':2})
    rows=[{'kind':'current_product','quantity':qty,'sourcing_mode':'supplier','supplier_id':'s1','condition':{'option_id':'count','value_id':val}} for qty,val in [('1','one'),('2','two')]]
    rows += [item('p1',sourcing_mode='supplier',supplier_id='s2',condition={'option_id':'gift','value_id':'yes'}),{'kind':'resource','resource_id':'r','quantity':'1','component_scope':'per_unit'}]
    await save_current(db,rows)
    result=await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'count','value_id':'two'},{'option_id':'gift','value_id':'yes'}])
    assert result['units_per_selection']=='3'
    assert result['current_product_units_per_selection']=='2'
    assert next(l for l in result['leaves'] if l['kind']=='resource')['quantity']=='2'
    assert {l['supplier_id'] for l in result['leaves'] if l['kind']=='product'}=={'s1','s2'}
    no_gift=await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'count','value_id':'one'},{'option_id':'gift','value_id':'no'}])
    assert no_gift['units_per_selection']=='1'


@pytest.mark.asyncio
async def test_ordinary_product_ignores_option_answers(db):
    assert await b.expand_bundle(db,'owner','p1',option_answers={'not-a-field':'anything'}) is None


@pytest.mark.asyncio
async def test_current_safe_limit_100_and_101(db):
    result=await save_current(db,[{'kind':'current_product','quantity':'100'}])
    assert result['expansion']['current_product_units_per_selection']=='100'
    assert len(result['expansion']['leaves'])==100
    with pytest.raises(HTTPException) as exc:
        await save_current(db,[{'kind':'current_product','quantity':'101'}],revision=1)
    assert exc.value.detail['code']=='bundle_current_product_quantity_limit'


@pytest.mark.asyncio
@pytest.mark.parametrize('first_policy,second_policy',[('sum','once'),('once','sum')])
async def test_current_sum_policy_scales_native_components_exactly(db,first_policy,second_policy):
    await db[PRODUCTS].update_one({'salla_product_id':'root'},{'$set':{'options':[{'id':'o','values':[{'id':'v'}]}]}})
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'r','unit_cost':3})
    await db[b.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'owner','salla_product_id':'root','resource_id':'r','quantity':1})
    await save_current(db,[{'kind':'current_product','quantity':'1','repeat_policy':first_policy},
        {'kind':'current_product','quantity':'1','repeat_policy':second_policy,'condition':{'option_id':'o','value_id':'v'}}],scopes={'r':'per_unit'})
    result=await b.expand_bundle(db,'owner','root',selected_options=[{'option_id':'o','value_id':'v'}])
    assert result['current_product_units_per_selection']=='2'
    assert next(l for l in result['leaves'] if l['kind']=='resource')['quantity']=='2'
    assert b.number(result['estimated_cost'])==26


@pytest.mark.asyncio
async def test_inventory_annotation_matches_customization_specifications(db,monkeypatch):
    from decimal import Decimal
    import bundle_fulfillment_v2 as fulfillment
    async def lots(*args,**kwargs):
        return [{'location_id':'a','item':{'item_type':'product','product_id':'mpv2_p1','salla_product_id':'p1','variant_id':'M','specifications':spec},'available':Decimal('5')} for spec in [{'size':'M'},{'size':'XL'}]]
    monkeypatch.setattr(fulfillment,'inventory_lots',lots)
    expansion={'leaves':[{'kind':'product','product_id':'p1','mezan_product_id':'mpv2_p1','salla_product_id':'p1','variant_id':'M','customization':{'size':'M'}}]}
    assert (await b.annotate_inventory(db,'owner',expansion))['leaves'][0]['available_quantity']=='5'


@pytest.mark.asyncio
async def test_supplier_nonstock_service_fails_before_save(db):
    await db[b.RESOURCES].insert_one({'user_id':'owner','id':'service','unit_cost':5,'track_inventory':False})
    with pytest.raises(HTTPException) as exc:
        await save(db,'root',[{'kind':'resource','resource_id':'service','sourcing_mode':'supplier','supplier_id':'s1'}])
    assert exc.value.detail['code']=='bundle_nonstock_supplier_requires_service_receipt'
    assert await db[b.BUNDLES].find_one({'user_id':'owner','product_id':'root'}) is None
    result=await save(db,'root',[{'kind':'resource','resource_id':'service','sourcing_mode':'stock'}])
    assert result['expansion']['leaves'][0]['track_inventory'] is False
