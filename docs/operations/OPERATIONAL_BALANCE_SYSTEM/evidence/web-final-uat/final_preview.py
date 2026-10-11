"""UAT-only host for immutable candidate; synthetic account, loopback Mongo only."""
import asyncio
import json
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

ROOT = Path(__file__).parent
OWNER = 'web-final-uat-20261005'
DB_NAME = 'operational_balance_device_uat_20261005'

def record(kind, **data):
    with (ROOT / 'trace.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'kind':kind,**data},ensure_ascii=False,default=str)+'\n')

def guard(event, args):
    if event == 'socket.connect':
        address = args[1]
        host = address[0] if isinstance(address, tuple) else str(address)
        allowed = host in {'127.0.0.1','::1'}
        record('connect',address=address,allowed=allowed)
        if not allowed: raise RuntimeError('UAT forbids non-loopback connection')
    if event == 'import' and str(args[0]).startswith('accounting_'):
        record('accounting_import_attempt',module=args[0])
        raise RuntimeError('Accounting module import prohibited in this UAT')
sys.addaudithook(guard)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from operational_balance_routes import make_operational_balance_router
from operational_balance_worker import run

client = AsyncIOMotorClient('mongodb://127.0.0.1:27305',serverSelectionTimeoutMS=3000)
raw_db = client[DB_NAME]
WRITES = {'operational_balance_states_v1','operational_balance_operation_claims_v1','operational_balance_receipts_v1'}
class GuardedCollection:
    def __init__(self,name): self.name=name
    def __getattr__(self, method):
        target=getattr(raw_db[self.name],method)
        if self.name=='operational_balance_states_v1' and method=='find':
            def scoped(query,*args,**kwargs):
                return target({**query,'owner_id':OWNER},*args,**kwargs)
            return scoped
        if method.startswith(('insert','update','replace','delete','find_one_and','bulk','drop','create')):
            async def checked(*args,**kwargs):
                record('database_write',collection=self.name,method=method,allowed=self.name in WRITES)
                if self.name not in WRITES: raise RuntimeError('Nonoperational writer prohibited')
                return await target(*args,**kwargs)
            return checked
        return target
class GuardedDB:
    def __getitem__(self,name): return GuardedCollection(name)
    def __getattr__(self,name): return self[name]
db=GuardedDB()

@asynccontextmanager
async def lifespan(app):
    # Fixture seeding only, never production data or accounting state.
    await raw_db.users.update_one({'id':OWNER},{'$setOnInsert':{'id':OWNER,'role':'owner','name':'اختبار Web النهائي','is_active':True}},upsert=True)
    for identity,name in [('uat-unlinked-bank','بنك غير مرتبط — اختبار'),('uat-bound-bank','البنك المرتبط — اختبار')]:
        await raw_db.mz2_financial_accounts.update_one({'user_id':OWNER,'id':identity},{'$setOnInsert':{'user_id':OWNER,'id':identity,'name':name,'account_type':'bank','currency':'SAR','status':'active'}},upsert=True)
    await raw_db.mezan_suppliers_v2.update_one({'user_id':OWNER,'id':'uat-supplier'},{'$setOnInsert':{'user_id':OWNER,'id':'uat-supplier','name':'مورد UAT','status':'active'}},upsert=True)
    record('host_start',owner=OWNER,database=DB_NAME,backend='http://127.0.0.1:8135/api',candidate='0d8e4147a398a0ddd3928c5e04a66adf67a66379')
    task=asyncio.create_task(run(db,interval=2))
    try: yield
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        client.close()

inner=FastAPI(lifespan=lifespan)
inner.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5178'],allow_credentials=True,allow_methods=['GET','POST'],allow_headers=['*'])
async def fixture_user(): return {'id':OWNER}
inner.include_router(make_operational_balance_router(db,fixture_user),prefix='/api')

class TraceApp:
    async def __call__(self,scope,receive,send):
        if scope['type'] != 'http': return await inner(scope,receive,send)
        body=bytearray(); response=bytearray(); status=0
        async def recv():
            message=await receive()
            if message['type']=='http.request':body.extend(message.get('body',b''))
            return message
        async def sent(message):
            nonlocal status
            if message['type']=='http.response.start':status=message['status']
            if message['type']=='http.response.body':response.extend(message.get('body',b''))
            await send(message)
        try: await inner(scope,recv,sent)
        finally:
            record('http',method=scope['method'],path=scope['path'],status=status,
                   request=body.decode('utf-8',errors='replace'),response=response.decode('utf-8',errors='replace'))
app=TraceApp()
