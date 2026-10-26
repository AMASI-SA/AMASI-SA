"""Real disposable Mongo replica acceptance for catalog read isolation."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center.google_product_catalog_reader import read_catalog_products

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)

@pytest_asyncio.fixture
async def replica_db():
    uri = os.getenv('GOOGLE_CATALOG_TEST_MONGO_URI')
    if not uri:
        pytest.skip('disposable loopback replica required')
    parsed = urlsplit(uri)
    if (parsed.scheme != 'mongodb' or parsed.hostname not in {'127.0.0.1', 'localhost'}
            or parsed.port is None or parsed.username is not None or parsed.password is not None
            or ',' in parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('only an isolated loopback Mongo is permitted')
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = 'g_catalog_test_' + uuid.uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command('hello')
        assert hello.get('setName') == 'gCatalogTest' and hello.get('isWritablePrimary') is True
        assert type(hello.get('logicalSessionTimeoutMinutes')) is int
        async with await client.start_session() as session:
            session.start_transaction()
            await db.replica_probe.insert_one({'probe': True}, session=session)
            await session.abort_transaction()
        assert await db.replica_probe.count_documents({}) == 0
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def product(pid, owner='owner', **kw):
    base = {'user_id':owner,'salla_product_id':pid,'mezan_product_id':'mpv2_'+pid,
        'source':'salla','source_revision':'a'*64,'last_synced_at':NOW,
        'status':'active','archived':False,'quantity':1,'variants_count':0}
    base.update(kw)
    return base

async def read(db, ids):
    return await read_catalog_products(db, owner_id='owner', product_ids=ids, as_of=NOW, max_age_seconds=3600)

@pytest.mark.asyncio
async def test_real_mongo_owner_scope_projection_and_no_mutation(replica_db):
    await replica_db.mezan_products_v2.insert_many([product('12', confidential='secret sentinel'),product('12',owner='other')])
    result = await read(replica_db,['12'])
    assert len(result['products']) == 1
    assert result['products'][0].status == 'unknown'
    assert result['products'][0].cost_quality == 'unknown'
    assert 'secret sentinel' not in str(result)
    assert result['source_writes'] == 0 and result['execution_authorized'] is False
    assert await replica_db.mezan_products_v2.count_documents({}) == 2

@pytest.mark.asyncio
async def test_real_mongo_duplicate_owner_identity_fails_closed(replica_db):
    await replica_db.mezan_products_v2.insert_many([product('12'),product('12'),product('12',owner='other')])
    with pytest.raises(ValueError, match='ambiguous catalog identity'):
        await read(replica_db,['12'])

@pytest.mark.asyncio
async def test_real_mongo_bounds_freshness_missing_and_states(replica_db):
    await replica_db.mezan_products_v2.insert_many([
        product('1',last_synced_at=NOW-timedelta(hours=2)),product('2',status='inactive'),
        product('3',status='out_of_stock'),product('4',last_synced_at=NOW+timedelta(seconds=1)),
        product('5',source_revision='invalid')])
    result = await read(replica_db,['1','2','3','4','5','6'])
    assert result['missing_or_unverified_product_ids'] == ['1','4','5','6']
    assert [p.status for p in result['products']] == ['hidden','out_of_stock']
    with pytest.raises(ValueError): await read(replica_db,[str(x) for x in range(101)])
