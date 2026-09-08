"""Offline actual supplier workspace builder with an in-memory query adapter."""
import ast
import asyncio
import copy
import hashlib
import json
from collections import defaultdict
from types import SimpleNamespace
import unittest
from source_paths import backend_root
from test_unit_projection import synthetic_documents
from supplier_workspace_contract import workspace_payload, CHECKS
from test_resume_diagnostics import controller, shell


def application():
    source=(backend_root()/'preparation_supplier_dispatch.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    scope={'defaultdict':defaultdict,'hashlib':hashlib,'json':json,
           '_text':lambda x:str(x or '').strip(),'_normalized':lambda x:str(x or '').strip().casefold(),
           'PIECES':'pieces','REGISTRY':'registries','BATCHES':'batches','DISPATCHES':'dispatches',
           'MEZAN_SUPPLIERS_V2':'suppliers'}
    for name in ('ASSIGNED','BLOCKED','CANCELLED','IN_PROGRESS','READY_FOR_ASSEMBLY','READY_FOR_RECEIPT','RECEIVED'):
        scope['PIECE_STATUS_'+name]=name.lower()
    for node in tree.body:
        if isinstance(node,ast.Assign):
            try:value=ast.literal_eval(node.value)
            except (ValueError,TypeError):continue
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id not in scope:scope[target.id]=value
    functions=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name!='make_preparation_supplier_dispatch_router']
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+functions,type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),'supplier-builder-extracted','exec'),scope)
    return scope


def match(row,query):
    for name,wanted in query.items():
        value=row
        exists=True
        for part in name.split('.'):
            if isinstance(value,list) and part.isdigit() and int(part)<len(value):value=value[int(part)]
            elif isinstance(value,dict) and part in value:value=value[part]
            else:value=None;exists=False;break
        if isinstance(wanted,dict):
            for op,arg in wanted.items():
                if op=='$ne' and value==arg:return False
                if op=='$in' and value not in arg:return False
                if op=='$exists' and exists!=arg:return False
                if op not in ('$ne','$in','$exists'):raise AssertionError('UNSUPPORTED_QUERY')
        elif value!=wanted:return False
    return True


class Cursor:
    def __init__(self,rows):self.rows=copy.deepcopy(rows)
    def sort(self,*args):return self
    def limit(self,n):self.rows=self.rows[:n];return self
    async def to_list(self,n):return self.rows[:n]

class Collection:
    def __init__(self,rows):self.rows=rows
    def find(self,q,projection=None):return Cursor([r for r in self.rows if match(r,q)])


def fixture():
    data=synthetic_documents()
    for p in data['pieces']:p['image_url']='http://127.0.0.1:8001/local.png'
    data['suppliers']=[{'user_id':'exit2d-unit-owner','id':'exit2d-supplier','status':'active','service_ids':['synthetic-service']}]
    data['dispatches']=[]
    return data


def build(data,owner='exit2d-unit-owner',employee='exit2d-unit-employee'):
    app=application()
    db={key:Collection(data[key]) for key in ('pieces','batches','registries','suppliers','dispatches')}
    result=asyncio.run(app['_employee_workspace'](db,user_id=owner,employee_id=employee,limit=100,piece_grain=True))
    # These are the literal flags appended by the actual route, not replacement builder data.
    return dict(result,ok=True,mezan_only=True,external_supplier_login_enabled=False,salla_updated=False,qoyod_updated=False)


class SupplierWorkspaceTests(unittest.TestCase):
    def evaluate(self,data,result=None,status=200):
        result=build(data) if result is None else result
        file={**data['files'][0],'request_id':'synthetic-request'}
        state={}
        def call(*args,**kwargs):return SimpleNamespace(status_code=status,json=lambda:result)
        payload=workspace_payload(call,file,data['pieces'],data['employee'],state)
        return payload,state

    def reject(self,data,identifier,result=None,status=200):
        result=build(data) if result is None else result
        def action(state):
            state['owner_cookie']='PRIVATE_MARKER_NOT_FOR_OUTPUT'
            workspace_payload(lambda *a,**k:SimpleNamespace(status_code=status,json=lambda:result),
                {**data['files'][0],'request_id':'synthetic-request'},data['pieces'],data['employee'],state)
        code,reply=controller('prep-resume',action);parsed=shell('prep-resume',reply)
        self.assertTrue(code==parsed.returncode==1,'REJECTION_MISSING')
        self.assertTrue(identifier in parsed.stdout,'CHECK_ID_MISSING')
        self.assertTrue('PRIVATE_MARKER_NOT_FOR_OUTPUT' not in reply+parsed.stdout+parsed.stderr,'OUTPUT_LEAK')

    def test_actual_builder_healthy_and_payload_planner(self):
        data=fixture();payload,state=self.evaluate(data)
        chosen=application()['plan_piece_selections'](data['pieces'],payload['files'][0]['selections'])
        self.assertEqual(len(chosen),4)
        self.assertEqual({p['piece_id'] for p in chosen},{p['piece_id'] for p in data['pieces'] if p['file_number']==data['files'][0]['file_number']})
        self.assertEqual(dict(state['_supplier_evidence'])['IDENTITY_MATCH'],1)

    def test_supplier_ineligible_missing_or_other_tenant(self):
        for mode in ('inactive','missing','no_services','tenant'):
            d=fixture()
            if mode=='missing':d['suppliers']=[]
            elif mode=='inactive':d['suppliers'][0]['status']='inactive'
            elif mode=='no_services':d['suppliers'][0]['service_ids']=[]
            else:d['suppliers'][0]['user_id']='other-tenant'
            self.reject(d,'SUPPLIER_PRESENT')

    def test_file_missing_and_wrong_batch(self):
        d=fixture();absent=copy.deepcopy(d)
        absent['pieces']=[p for p in absent['pieces'] if p['file_number']!=d['files'][0]['file_number']]
        self.reject(d,'SUPPLIER_FILE',build(absent))
        result=build(d);result['files'][0]['batch_id']='wrong';self.reject(d,'SUPPLIER_FILE',result)

    def test_unavailable_pieces_are_not_selected(self):
        d=fixture()
        for p in d['pieces']:p['supplier_dispatch_status']='sent'
        self.reject(d,'SUPPLIER_SELECTIONS')

    def test_tenant_and_employee_scope_of_actual_query(self):
        d=fixture()
        self.assertEqual(build(d,owner='other-tenant')['files'],[])
        self.assertEqual(build(d,employee='other-employee')['files'],[])
        self.reject(d,'SUPPLIER_SCHEMA',build(d,employee='other-employee'))
        self.reject(d,'SUPPLIER_HTTP',status=403)

    def test_actual_worker_permission_boundary_with_scoped_assignment_adapter(self):
        app=application()
        class Denied(Exception):
            def __init__(self,*,status_code,detail):self.status_code=status_code
        async def assignment(db,*,owner_user_id,user_id):
            return db.get((owner_user_id,user_id))
        app.update(HTTPException=Denied,user_can_manage_preparation=lambda user:False,
                   role_assignment_owner_user_id=lambda user:user.get('created_by'),
                   find_role_assignment=assignment,effective_permissions=lambda row:(row or {}).get('permissions',[]))
        db={('owner','employee'):{'permissions':['preparation.assigned.read']}}
        user={'id':'employee','created_by':'owner'}
        self.assertEqual(asyncio.run(app['_require_preparation_worker'](db,user,permission='preparation.assigned.read')),user)
        for candidate,permission in ((user,'preparation.assigned.work'),({'id':'employee','created_by':'other'},'preparation.assigned.read'),(None,'preparation.assigned.read')):
            with self.assertRaises(Denied) as caught:
                asyncio.run(app['_require_preparation_worker'](db,candidate,permission=permission))
            self.assertEqual(caught.exception.status_code,403)

    def test_actual_route_status_rules_exclude_unreviewed_order(self):
        tree=ast.parse((backend_root()/'preparation_route_history.py').read_text(encoding='utf-8'))
        names={'_text','normalize_order_status','order_status_allows_employee_preparation','route_state_for_order_status'}
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        scope={}
        for n in tree.body:
            if isinstance(n,ast.Assign):
                try:value=ast.literal_eval(n.value)
                except (ValueError,TypeError):continue
                for target in n.targets:
                    if isinstance(target,ast.Name):scope[target.id]=value
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),'route-status-extracted','exec'),scope)
        self.assertEqual(scope['route_state_for_order_status']('under_review'),'outside_preparation')
        self.assertEqual(scope['route_state_for_order_status']('reviewed'),'employee_preparation')
        self.assertEqual(scope['route_state_for_order_status']('processing'),'employee_preparation')

    def test_schema_products_and_identity_rejections(self):
        d=fixture();self.reject(d,'SUPPLIER_SCHEMA',{})
        result=build(d);result['files'][0]['products'][0].pop('piece_id');self.reject(d,'SUPPLIER_PRODUCTS',result)
        result=build(d);result['files'][0]['products'][0]['piece_id']='wrong';self.reject(d,'SUPPLIER_IDENTITIES',result)

    def test_success_diagnostics_all_checks_and_unknown_protocol(self):
        d=fixture();result=build(d)
        def action(state):workspace_payload(lambda *a,**k:SimpleNamespace(status_code=200,json=lambda:result),{**d['files'][0],'request_id':'synthetic-request'},d['pieces'],d['employee'],state)
        code,reply=controller('prep-resume',action);parsed=shell('prep-resume',reply)
        self.assertEqual(code,0);self.assertEqual(parsed.returncode,0)
        for identifier in CHECKS:self.assertIn('CHECK prep-resume '+identifier+' PASS',parsed.stdout)
        parsed=shell('prep-resume','SUPPLIER PRIVATE_MARKER_NOT_FOR_OUTPUT 1\nPASS prep-resume\n')
        self.assertEqual(parsed.returncode,1);self.assertNotIn('PRIVATE_MARKER_NOT_FOR_OUTPUT',parsed.stdout+parsed.stderr)

if __name__=='__main__':unittest.main()
