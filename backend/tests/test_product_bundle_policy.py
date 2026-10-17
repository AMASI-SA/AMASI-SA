import pytest
import pytest_asyncio
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import product_bundle_policy_routes as policy
import os
import asyncio
from uuid import uuid4
from urllib.parse import urlsplit
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

@pytest_asyncio.fixture
async def db(monkeypatch):
    db = AsyncMongoMockClient().policy_test
    async def local(db, owner, callback, **kwargs): return await callback(db)
    monkeypatch.setattr(policy, 'operational_owner', local)
    await db.mezan_products_v2.insert_one({'user_id':'o','salla_product_id':'p','options':[{'id':'name','values':[]},{'id':'color','values':[{'id':'gold'}]}], 'variants':[{'id':'gold'}]})
    await db[policy.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id':'o','salla_product_id':'p','id':'bag','resource_id':'bag-r','quantity':1})
    await db.mezan_suppliers_v2.insert_one({'user_id':'o','id':'supplier'})
    await db[policy.COST_PROFILES].insert_one({'user_id':'o','salla_product_id':'p','base_cost':50})
    return db

def payload():
    return {'expected_revision':0,'composition_components':{'product_resource:bag':{'cost_treatment':'independent','assembly_origin':'inhouse','evidence_ref':'Owner verified packaging cost sheet'}},'sourcing':{'stock_first':True,'allow_split':True,'supplier_id':'supplier','rules':[{'rule_id':'gold','variant_id':'gold','conditions':[{'option_id':'color','value_id':'gold'}],'customization':{},'supplier_id':'supplier','allow_split':True}]}}

@pytest.mark.asyncio
async def test_policy_updates_existing_profiles_only_and_preserves_cost(db):
    before = await db[policy.PRODUCT_RESOURCE_BINDINGS].find_one({})
    result = await policy.save_policy(db,'o','p',payload(),'actor')
    assert result['revision']==1
    assert result['sourcing']['updated_by']=='actor'
    assert (await db[policy.COST_PROFILES].find_one({}))['base_cost']==50
    assert await db[policy.PRODUCT_RESOURCE_BINDINGS].find_one({}) == before
    assert (await policy.get_policy(db,'o','p'))['composition_components']['product_resource:bag']['cost_treatment']=='independent'
    with pytest.raises(HTTPException) as exc: await policy.save_policy(db,'o','p',payload(),'actor')
    assert exc.value.status_code==409

@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['evidence','supplier','binding','variant','option','text','stock_first'])
async def test_invalid_policy_cannot_write_profiles(db, change):
    data=payload()
    if change=='evidence': data['composition_components']['product_resource:bag']['evidence_ref']=''
    if change=='supplier': data['sourcing']['supplier_id']='other-owner-supplier'
    if change=='binding': data['composition_components']={'product_resource:foreign':data['composition_components']['product_resource:bag']}
    if change=='variant': data['sourcing']['rules'][0]['variant_id']='missing'
    if change=='option': data['sourcing']['rules'][0]['conditions']=[{'option_id':'color','value_id':'missing'}]
    if change=='text': data['sourcing']['rules'][0]['customization']={'color':'free text'}
    if change=='stock_first': data['sourcing']['stock_first']=False
    before=await db[policy.COST_PROFILES].find_one({})
    with pytest.raises(HTTPException): await policy.save_policy(db,'o','p',data,'actor')
    assert await db[policy.COST_PROFILES].find_one({})==before
    assert await db[policy.PRODUCT_OPERATION_PROFILES].count_documents({})==0

@pytest.mark.asyncio
async def test_policy_rejects_cross_owner_product(db):
    with pytest.raises(HTTPException) as exc: await policy.save_policy(db,'another','p',payload(),'actor')
    assert exc.value.status_code==404


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('cost_treatment', []), ('cost_treatment', {}), ('assembly_origin', []), ('assembly_origin', {}), ('variant_id', []), ('variant_id', {})])
async def test_malformed_policy_enums_are_controlled_validation_errors(db, field, value):
    data = payload()
    if field == 'variant_id':
        data['sourcing']['rules'][0][field] = value
    else:
        data['composition_components']['product_resource:bag'][field] = value
    before = await db[policy.COST_PROFILES].find_one({})
    with pytest.raises(HTTPException) as exc:
        await policy.save_policy(db, 'o', 'p', data, 'actor')
    assert exc.value.status_code == 422
    assert await db[policy.COST_PROFILES].find_one({}) == before
    assert await db[policy.PRODUCT_OPERATION_PROFILES].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('field_type', ['file', 'date', 'time', 'number'])
async def test_customization_policy_rejects_nontext_valueless_fields(db, field_type):
    await db.mezan_products_v2.update_one({'salla_product_id': 'p'}, {'$set': {'options.0.type': field_type}})
    data = payload()
    data['sourcing']['rules'][0]['customization'] = {'name': 'عبير'}
    with pytest.raises(HTTPException) as exc:
        await policy.save_policy(db, 'o', 'p', data, 'actor')
    assert exc.value.detail['code'] == 'bundle_policy_invalid_customization'


@pytest.mark.asyncio
@pytest.mark.parametrize('field_type', [None, 'text', 'textarea', 'long_text'])
async def test_customization_policy_accepts_text_and_historical_missing_type(db, field_type):
    await db.mezan_products_v2.update_one({'salla_product_id': 'p'}, {'$set': {'options.0.type': field_type}})
    data = payload()
    data['sourcing']['rules'][0]['customization'] = {'name': 'عبير'}
    result = await policy.save_policy(db, 'o', 'p', data, 'actor')
    assert result['sourcing']['rules'][0]['customization'] == {'name': 'عبير'}


@pytest_asyncio.fixture
async def real_policy_db():
    uri = os.environ.get('MZ2_TEST_MONGO_URI')
    if not uri:
        pytest.skip('requires isolated loopback replica set')
    assert urlsplit(uri).hostname in {'127.0.0.1', 'localhost', '::1'}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = 'mz2_bundle_policy_test_' + uuid4().hex
    db = client[name]
    try:
        assert (await db.command('hello')).get('setName')
        await db.mz2_atomic_owners.insert_one({'_id': 'o', 'revision': 0, 'writes_paused': False, 'control_revision': 0})
        await db.mezan_products_v2.insert_one({'user_id': 'o', 'salla_product_id': 'p', 'options': [
            {'id': 'name', 'values': []}, {'id': 'color', 'values': [{'id': 'gold'}]}], 'variants': [{'id': 'gold'}]})
        await db[policy.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id': 'o', 'salla_product_id': 'p', 'id': 'bag', 'resource_id': 'bag-r', 'quantity': 1})
        await db.mezan_suppliers_v2.insert_one({'user_id': 'o', 'id': 'supplier'})
        await db[policy.COST_PROFILES].insert_one({'user_id': 'o', 'salla_product_id': 'p', 'base_cost': 50, 'variant_costs': {'gold': 70}})
        yield db
    finally:
        assert name.startswith('mz2_bundle_policy_test_') and name == db.name
        await client.drop_database(name)
        client.close()


async def stored_rows(db):
    return {name: await db[name].find({}).sort('_id', 1).to_list(1000)
            for name in await db.list_collection_names() if not name.startswith('system.')}


@pytest.mark.asyncio
async def test_real_policy_transaction_commits_both_profiles_preserves_product_data(real_policy_db):
    db = real_policy_db
    original_binding = await db[policy.PRODUCT_RESOURCE_BINDINGS].find_one({})
    original_product = await db.mezan_products_v2.find_one({})
    result = await policy.save_policy(db, 'o', 'p', payload(), 'owner-actor')
    costs = await db[policy.COST_PROFILES].find_one({})
    operations = await db[policy.PRODUCT_OPERATION_PROFILES].find_one({})
    assert costs['composition_components'] == result['composition_components']
    assert operations['bundle_sourcing'] == result['sourcing']
    assert operations['bundle_policy_revision'] == result['revision'] == 1
    assert costs['composition_policy_evidence']['revision'] == 1
    assert costs['base_cost'] == 50 and costs['variant_costs'] == {'gold': 70}
    assert await db[policy.PRODUCT_RESOURCE_BINDINGS].find_one({}) == original_binding
    assert await db.mezan_products_v2.find_one({}) == original_product
    assert (await db.mz2_atomic_owners.find_one({'_id': 'o'}))['revision'] == 1
    assert await db.mezan_journal_entries.count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_policy_conflict_rolls_back_owner_fence_and_preserves_profiles(real_policy_db):
    db = real_policy_db
    await policy.save_policy(db, 'o', 'p', payload(), 'owner-actor')
    before = await stored_rows(db)
    with pytest.raises(HTTPException) as exc:
        await policy.save_policy(db, 'o', 'p', payload(), 'another-actor')
    assert exc.value.detail['code'] == 'bundle_policy_revision_conflict'
    assert await stored_rows(db) == before


@pytest.mark.asyncio
async def test_real_policy_foreign_supplier_rejection_has_zero_writes(real_policy_db):
    db = real_policy_db
    await db.mezan_suppliers_v2.insert_one({'user_id': 'foreign', 'id': 'foreign-supplier'})
    data = payload()
    data['sourcing']['supplier_id'] = 'foreign-supplier'
    before = await stored_rows(db)
    with pytest.raises(HTTPException) as exc:
        await policy.save_policy(db, 'o', 'p', data, 'owner-actor')
    assert exc.value.detail['code'] == 'supplier_v2_identity_required'
    assert await stored_rows(db) == before


@pytest.mark.asyncio
async def test_real_policy_second_profile_failure_rolls_back_first_profile(real_policy_db):
    db = real_policy_db
    # Database rejects the second write after the cost profile update succeeds
    # inside the real transaction, proving multi-document atomicity.
    await db.create_collection(policy.PRODUCT_OPERATION_PROFILES, validator={'required_test_field': {'$exists': True}})
    before = await stored_rows(db)
    with pytest.raises(OperationFailure) as exc:
        await policy.save_policy(db, 'o', 'p', payload(), 'owner-actor')
    assert exc.value.code == 121
    assert await stored_rows(db) == before


@pytest.mark.asyncio
async def test_real_policy_concurrent_same_revision_has_single_winner(real_policy_db):
    db = real_policy_db
    results = await asyncio.gather(
        policy.save_policy(db, 'o', 'p', payload(), 'actor-a'),
        policy.save_policy(db, 'o', 'p', payload(), 'actor-b'), return_exceptions=True)
    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == len(failures) == 1
    assert failures[0].detail['code'] == 'bundle_policy_revision_conflict'
    costs = await db[policy.COST_PROFILES].find_one({})
    operations = await db[policy.PRODUCT_OPERATION_PROFILES].find_one({})
    assert costs['composition_policy_evidence']['updated_by'] == operations['bundle_sourcing']['updated_by']
    assert operations['bundle_policy_revision'] == 1
    assert (await db.mz2_atomic_owners.find_one({'_id': 'o'}))['revision'] == 1
