"""Connected actual base/reconciliation/wrapper tests; synthetic in-memory DB only."""
import ast
import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from source_paths import backend_root
from test_supplier_workspace_contract import application, fixture, match, Cursor

class MemoryCollection:
    def __init__(self,rows): self.rows=copy.deepcopy(rows)
    def find(self,q,projection=None): return Cursor([r for r in self.rows if self.matches(r,q)])
    def matches(self,row,q):
        return all(any(self.matches(row,part) for part in value) if key=='$or' else match(row,{key:value}) for key,value in q.items())
    async def create_index(self,*a,**k): return 'synthetic-index'
    async def update_one(self,q,update,upsert=False):
        found=[r for r in self.rows if self.matches(r,q)]
        if not found and upsert:
            row=copy.deepcopy(q);row.update(copy.deepcopy(update.get('$setOnInsert',{})));self.rows.append(row)
            return SimpleNamespace(modified_count=0)
        if not found:return SimpleNamespace(modified_count=0)
        row=found[0];before=copy.deepcopy(row)
        row.update(copy.deepcopy(update.get('$set',{})))
        for key in update.get('$unset',{}):row.pop(key,None)
        for key,value in update.get('$addToSet',{}).items():
            if value not in row.setdefault(key,[]):row[key].append(copy.deepcopy(value))
        return SimpleNamespace(modified_count=int(row!=before))

class DB(dict):
    def __getattr__(self,key):return self[key]


def route_functions(base):
    tree=ast.parse((backend_root()/'preparation_route_history.py').read_text(encoding='utf-8'))
    scope={'datetime':datetime,'timezone':timezone,'uuid':uuid,'ASCENDING':1,'DESCENDING':-1,
           'base':SimpleNamespace(**base),'original':base['_employee_workspace']}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            try:value=ast.literal_eval(node.value)
            except (TypeError,ValueError):continue
            for target in node.targets:
                if isinstance(target,ast.Name):scope[target.id]=value
    funcs=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name!='install_supplier_dispatch_route_guard']
    install=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_supplier_dispatch_route_guard')
    funcs.append(next(n for n in install.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='guarded_employee_workspace'))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+funcs,type_ignores=[])),'actual-route-guard','exec'),scope)
    return scope


def environment(status='بانتظار المراجعة'):
    data=fixture();base=application();route=route_functions(base)
    # Same actual collection names for both modules; only DB operations are substituted.
    base['PIECES']=route['PIECES'];base['REGISTRY']='registries';base['BATCHES']='batches'
    db=DB({k:MemoryCollection(data[k]) for k in ('batches','registries','suppliers','dispatches')})
    db[route['PIECES']]=MemoryCollection(data['pieces'])
    db[route['ROUTE_EVENTS']]=MemoryCollection([])
    db['unified_orders']=MemoryCollection([{'user_id':'exit2d-unit-owner','order_number':n,'order_status':status,
        'raw_by_source':{'salla_direct':{'status':{'name':'بانتظار المراجعة','slug':'under_review'}}}} for n in sorted({p['order_number'] for p in data['pieces']})])
    route['base']=SimpleNamespace(**base)
    return data,base,route,db

async def compare(base,route,db,owner='exit2d-unit-owner',employee='exit2d-unit-employee'):
    args={'user_id':owner,'employee_id':employee,'limit':100,'piece_grain':True}
    pieces=await db[route['PIECES']].find({'user_id':owner,'responsible_employee_id':employee,'experiment_archived_at':None,'status':{'$ne':'cancelled'}}).to_list(50000)
    before=await base['_employee_workspace'](db,**args)
    eligible,outside=await route['reconcile_employee_workspace_route'](db,user_id=owner,employee_id=employee,pieces=copy.deepcopy(pieces))
    after=await route['guarded_employee_workspace'](db,**args)
    return (len(pieces),len(before['files']),sum(len(f['products']) for f in before['files']),len(eligible),len(outside),len(after['files'])),after

class RouteVisibilityTests(unittest.TestCase):
    def test_pending_fixture_first_loss_is_reconciliation_and_filter(self):
        data,base,route,db=environment()
        counts,result=asyncio.run(compare(base,route,db))
        self.assertEqual(counts,(8,2,8,0,8,0))
        self.assertTrue(result['route_status_enforced'])
        self.assertEqual(result['outside_preparation_piece_count'],8)
        self.assertEqual(len(db[route['PIECES']].rows),8)
    def test_eligible_order_survives_full_guard(self):
        _,base,route,db=environment('reviewed')
        self.assertEqual(asyncio.run(compare(base,route,db))[0],(8,2,8,8,0,2))
    def test_missing_order_wrong_link_and_other_tenant(self):
        for mode in ('missing','link','tenant'):
            _,base,route,db=environment('reviewed')
            if mode=='missing':db.unified_orders.rows=[]
            else:
                for row in db.unified_orders.rows:row['order_number' if mode=='link' else 'user_id']='other'
            self.assertEqual(asyncio.run(compare(base,route,db))[0],(8,2,8,0,8,0))
    def test_employee_and_tenant_scope(self):
        for kwargs in ({'owner':'other'},{'employee':'other'}):
            _,base,route,db=environment('reviewed')
            self.assertEqual(asyncio.run(compare(base,route,db,**kwargs))[0],(0,0,0,0,0,0))
    def test_repeated_reads_do_not_duplicate_history(self):
        _,base,route,db=environment()
        asyncio.run(compare(base,route,db));before=copy.deepcopy(db[route['ROUTE_EVENTS']].rows)
        asyncio.run(compare(base,route,db))
        self.assertEqual(db[route['ROUTE_EVENTS']].rows,before)
        self.assertTrue(all(len(p['preparation_route_history'])==1 for p in db[route['PIECES']].rows))
    def test_effective_status_priority_actual(self):
        _,_,route,_=environment();effective=route['_effective_status_from_order']
        self.assertEqual(effective({'order_status':'بانتظار المراجعة','raw_by_source':{'salla_direct':{'status':{'name':'reviewed'}}}}),'بانتظار المراجعة')
        self.assertEqual(effective({'order_status':'بانتظار المراجعة','raw_by_source':{'salla_direct':{'status':{'customized':{'name':'reviewed'}}}}}),'reviewed')
        self.assertEqual(effective({'order_status':'cancelled','raw_by_source':{'salla_direct':{'status':{'customized':'reviewed'}}}}),'cancelled')

    def test_actual_review_confirmation_does_not_write_local_order(self):
        from salla_http_simulator import Fixture, ORDER_IDS
        tree=ast.parse((backend_root()/'order_review_routes.py').read_text(encoding='utf-8'))
        names={'_text','_walk_dicts','_reviewed_status_id','_sync_salla_reviewed'}
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        scope={'SallaError':RuntimeError}
        for n in tree.body:
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='REVIEWED_STATUS_NAMES' for t in n.targets):
                scope['REVIEWED_STATUS_NAMES']=ast.literal_eval(n.value)
        token='local-visibility-fixture-token-000000000'
        simulator=Fixture(token,'success')
        async def transport(db,user,method,path,**kwargs):
            status,result=simulator.respond(method,'/admin/v2'+path,'Bearer '+token,kwargs.get('json'))
            if status!=200:raise AssertionError('SIMULATOR_RESPONSE_REJECTED')
            return result
        scope['call_salla']=transport
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),'actual-review-sync','exec'),scope)
        _,base,route,db=environment();before=copy.deepcopy(db.unified_orders.rows)
        order=SimpleNamespace(source=SimpleNamespace(source_order_id=next(iter(ORDER_IDS))),order_id='unused')
        self.assertEqual(asyncio.run(scope['_sync_salla_reviewed'](db,'exit2d-unit-owner',order)),('sent',None))
        self.assertEqual(db.unified_orders.rows,before)
        self.assertEqual(simulator.counts['status_writes'],1)
        self.assertEqual(asyncio.run(compare(base,route,db))[0],(8,2,8,0,8,0))

    def test_guard_evidence_reaches_parser_without_sensitive_values(self):
        from supplier_workspace_contract import workspace_payload
        from test_resume_diagnostics import controller,shell
        data,base,route,db=environment()
        _,result=asyncio.run(compare(base,route,db))
        result.update(ok=True,mezan_only=True,salla_updated=False,qoyod_updated=False,external_supplier_login_enabled=False)
        for mode in ('valid','missing','invalid'):
            response=copy.deepcopy(result)
            if mode=='missing':response.pop('route_status_enforced')
            if mode=='invalid':response['outside_preparation_piece_count']='PRIVATE_ROUTE_MARKER'
            def action(state):
                state['owner_cookie']='PRIVATE_SESSION_MARKER'
                workspace_payload(lambda *a,**k:SimpleNamespace(status_code=200,json=lambda:response),
                    {**data['files'][0],'request_id':'local-request'},data['pieces'],data['employee'],state)
            code,reply=controller('prep-resume',action);parsed=shell('prep-resume',reply)
            self.assertTrue(code==parsed.returncode==1)
            self.assertIn('SUPPLIER_FILE',parsed.stdout)
            if mode=='valid':
                self.assertIn('SUPPLIER ROUTE_ENFORCED 1',parsed.stdout)
                self.assertIn('SUPPLIER OUTSIDE_PIECES 8',parsed.stdout)
            else:
                self.assertIn('SUPPLIER ROUTE_UNAVAILABLE 1',parsed.stdout)
                self.assertNotIn('SUPPLIER OUTSIDE_PIECES',parsed.stdout)
            self.assertNotIn('PRIVATE_',reply+parsed.stdout+parsed.stderr)

if __name__=='__main__':unittest.main()
