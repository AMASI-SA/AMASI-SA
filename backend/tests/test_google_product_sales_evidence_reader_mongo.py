"""Real Mongo replica tests; database is disposable and loopback only."""
import os, uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center.google_product_sales_evidence_reader import read_product_sales_evidence

@pytest_asyncio.fixture
async def replica_db():
    uri=os.getenv('GOOGLE_SALES_TEST_MONGO_URI')
    if not uri: pytest.skip('disposable loopback replica required')
    p=urlsplit(uri)
    if (p.scheme!='mongodb' or p.hostname not in {'localhost','127.0.0.1'} or p.port is None
        or p.username is not None or p.password is not None or ',' in p.netloc
        or p.path not in ('','/') or p.query or p.fragment):raise ValueError('loopback Mongo required')
    client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000)
    name='g_sales_test_'+uuid.uuid4().hex; db=client[name]
    try:
        hello=await client.admin.command('hello')
        assert hello.get('setName')=='gSalesTest' and hello.get('isWritablePrimary') is True
        assert type(hello.get('logicalSessionTimeoutMinutes')) is int
        yield db
    finally:
        await client.drop_database(name);client.close()

async def read(db,limit=3):
    return await read_product_sales_evidence(db,owner_id='owner',product_ids=['123'],date_from='2026-10-01',date_to='2026-10-26',as_of=datetime(2026,10,27,12,tzinfo=timezone.utc),limit=limit)

@pytest.mark.asyncio
async def test_real_owner_scope_timezone_unknown_and_no_financial_metrics(replica_db):
    await replica_db.unified_orders.insert_many([
        {'user_id':'owner','order_date':'2026-10-25','status':'cancelled','currency':'QAR','products':[{'product_id':'123','quantity':3}]},
        {'user_id':'other','order_date':'2026-10-25','status':'paid'},
        {'user_id':'owner','order_date':'2026-09-25','status':'paid'}])
    out=await read(replica_db)
    assert out['observed_documents']==1 and out['period']['source_timezone']=='unproven'
    assert out['products'][0]['units_sold'] is None and out['products'][0]['sales_native'] is None
    assert out['products'][0]['profitability'] is None and out['execution_authorized'] is False
    assert await replica_db.unified_orders.count_documents({})==3

@pytest.mark.asyncio
async def test_real_empty_and_limit_plus_one_remain_unknown(replica_db):
    out=await read(replica_db);assert out['observed_documents']==0 and out['completeness']=='unproven'
    await replica_db.unified_orders.insert_many([{'user_id':'owner','order_date':'2026-10-25'} for _ in range(4)])
    out=await read(replica_db,limit=3)
    assert out['truncated'] is True and out['observed_documents'] is None
    assert out['products'][0]['units_sold'] is None
