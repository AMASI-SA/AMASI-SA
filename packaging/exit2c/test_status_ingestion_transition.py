"""Actual status refresh/map/merge/upsert followed by actual route reconciliation.
Transport and Mongo operations are in-memory adapters; no acceptance flow change.
"""
import ast
import asyncio
import builtins
import copy
from datetime import datetime,timezone,timedelta
import hashlib
import json
import re
from types import SimpleNamespace
import unittest
import uuid
from urllib.parse import urlencode
from source_paths import backend_root
from test_supplier_file_visibility import environment,compare,MemoryCollection
from salla_http_simulator import Fixture,ORDER_IDS,STATUS


def load_source(relative,extra=None):
    tree=ast.parse((backend_root()/relative).read_text(encoding='utf-8'))
    scope={'datetime':datetime,'timezone':timezone,'timedelta':timedelta,'deepcopy':copy.deepcopy,
           'hashlib':hashlib,'json':json,'re':re,'uuid':uuid,**(extra or {})}
    # Runtime imports are disallowed; upsert's optional BNPL propagation stays unexecuted.
    def no_import(name,*args,**kwargs):
        if name in ('_strptime','time'):return builtins.__import__(name,*args,**kwargs)
        raise ImportError('OFFLINE_IMPORT_BLOCKED')
    scope['__builtins__']={**vars(builtins),'__import__':no_import}
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
    code=ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[]))
    import __future__
    exec(compile(code,relative,'exec',flags=__future__.annotations.compiler_flag),scope)
    for node in tree.body:
        if isinstance(node,ast.Assign) and all(isinstance(x.func,ast.Attribute) and isinstance(x.func.value,ast.Name) and x.func.value.id=='re' and x.func.attr=='compile' for x in ast.walk(node) if isinstance(x,ast.Call)):
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),relative,'exec'),scope)
    return scope

class IngestionCollection(MemoryCollection):
    def matches(self,row,q):
        for key,value in q.items():
            if key=='$or':
                if not any(self.matches(row,part) for part in value):return False
            elif isinstance(value,dict) and '$regex' in value:
                if not re.search(value['$regex'],str(row.get(key,'')),re.I if value.get('$options')=='i' else 0):return False
            elif not super().matches(row,{key:value}):return False
        return True
    async def find_one(self,q,projection=None,sort=None):
        rows=[r for r in self.rows if self.matches(r,q)]
        return copy.deepcopy(rows[0]) if rows else None
    async def insert_one(self,row):self.rows.append(copy.deepcopy(row));return SimpleNamespace(inserted_id='offline')

class IngestionTransitionTests(unittest.TestCase):
    def test_confirm_fresh_skip_then_expired_snapshot_ingests_and_reveals_same_pieces(self):
        data,base,route,db=environment()
        for key,col in list(db.items()):db[key]=IngestionCollection(col.rows)
        # Initial input only: old pending orders with a recent local snapshot.
        now=datetime(2026,9,9,tzinfo=timezone.utc)
        for row in db.unified_orders.rows:
            row['order_id']='raw-'+row['order_number']
            row['orders_v2_salla_refreshed_at']=now.isoformat()
        warnings=[]
        logger=SimpleNamespace(warning=lambda *a,**k:warnings.append('CATALOG_WARNING'))
        attribution=load_source('salla_marketing_attribution.py')
        orders=load_source('orders_db.py',{k:attribution[k] for k in ('canonical_marketing_source','preserve_salla_raw_attribution','promoted_salla_attribution')})
        orders['logger']=logger
        # Catalogue writes, if needed by the actual upsert, stay in synthetic memory.
        db['products']=IngestionCollection([])
        db['mezan_products']=IngestionCollection([])
        sync=load_source('salla_integration/sync.py',{'promoted_salla_attribution':attribution['promoted_salla_attribution']})
        token='offline-status-ingestion-token-000000000'
        simulator=Fixture(token,'success')
        async def transport(database,owner,method,path,**kwargs):
            target='/admin/v2'+path
            if kwargs.get('params'):target+='?'+urlencode(kwargs['params'])
            status,result=simulator.respond(method,target,'Bearer '+token,kwargs.get('json'))
            if status!=200:raise AssertionError('SIMULATOR_REQUEST_REJECTED')
            return result
        sync['call_salla']=transport
        refresh=load_source('order_engine/salla_refresh.py',{'upsert_order':orders['upsert_order'],
            '_enrich_order_receiving_bank':sync['_enrich_order_receiving_bank'],'_salla_order_to_doc':sync['_salla_order_to_doc'],
            'call_salla':transport,'SallaError':RuntimeError})
        class Clock(datetime):
            current=None
            @classmethod
            def now(cls,tz=None):return cls.current
        Clock.current=now
        refresh['datetime']=Clock
        async def run():
            before_ids=[p['piece_id'] for p in db[route['PIECES']].rows]
            before_files={(f['batch_id'],f['file_number']) for f in data['files']}
            self.assertEqual((await compare(base,route,db))[0],(8,2,8,0,8,0))
            snapshot=copy.deepcopy(db.unified_orders.rows)
            for internal_id in ORDER_IDS:
                await transport(db,'exit2d-unit-owner','POST','/orders/'+internal_id+'/status',json={'status_id':71})
            calls=simulator.counts['simulated_provider_calls']
            for row in snapshot:
                result=await refresh['refresh_order_from_salla'](db,'exit2d-unit-owner',row['order_number'],allow_auto_fulfillment=False)
                self.assertTrue(result['ok'] and result['skipped'] and not result['updated'])
                self.assertEqual(result['reason'],'fresh_local_snapshot')
            self.assertEqual(simulator.counts['simulated_provider_calls'],calls)
            self.assertTrue(db.unified_orders.rows==snapshot,'FRESH_SNAPSHOT_CHANGED')
            self.assertEqual((await compare(base,route,db))[0],(8,2,8,0,8,0))
            # Advance only the test clock, not a stored status or freshness timestamp.
            Clock.current=now+timedelta(seconds=121)
            for row in snapshot:
                result=await refresh['refresh_order_from_salla'](db,'exit2d-unit-owner',row['order_number'],allow_auto_fulfillment=False)
                self.assertTrue(result.get('ok') and result.get('updated') and not result.get('skipped'),'INGESTION_FAILED')
            counts,visible=await compare(base,route,db)
            self.assertEqual(counts,(8,2,8,8,0,2))
            self.assertTrue({(f['batch_id'],f['file_number']) for f in visible['files']}==before_files,'FILE_IDENTITY_CHANGED')
            for stored in db.unified_orders.rows:
                self.assertTrue(stored['order_status']==STATUS[71]['name'],'CANONICAL_STATUS_MISMATCH')
                self.assertTrue(stored['raw_by_source']['salla_direct']['status']['slug']=='reviewed','RAW_STATUS_MISMATCH')
            self.assertTrue([p['piece_id'] for p in db[route['PIECES']].rows]==before_ids,'PIECE_IDENTITY_CHANGED')
            history=copy.deepcopy(db[route['ROUTE_EVENTS']].rows)
            await compare(base,route,db)
            self.assertTrue(db[route['ROUTE_EVENTS']].rows==history,'ROUTE_HISTORY_DUPLICATED')
            self.assertEqual(len(history),16)
            self.assertEqual(simulator.counts['unexpected'],0)
            self.assertEqual(simulator.counts['simulated_provider_calls'],6)
            self.assertEqual(simulator.counts['status_writes'],2)
            self.assertEqual(warnings,[],'ANCILLARY_CATALOG_FAILED')
        asyncio.run(run())

if __name__=='__main__':unittest.main()
