"""Local caller contract: actual route/refresh/map/merge/upsert/eligibility.

AST omits HTTP registration only. Mongo and provider transport are synthetic.
DTO presentation is a read adapter, not an ingestion or eligibility replacement.
No server imports, sockets, main acceptance changes, or automatic routing proof.
"""
import ast
import asyncio
import copy
from datetime import timedelta
from types import SimpleNamespace
import unittest

from source_paths import backend_root
from test_status_ingestion_transition import ingestion_fixture, load_source
from test_supplier_file_visibility import compare


class BoundaryError(Exception):
    def __init__(self, status_code=403, **kwargs):
        super().__init__('SYNTHETIC_BOUNDARY_FAILURE')
        self.status_code=status_code
        self.needs_reauth=False


def actual_detail_caller(f):
    async def get_order(repository, *, user_id, order_number):
        row=await repository.unified_orders.find_one({'user_id':user_id,'order_number':order_number})
        if row is None:raise LookupError('SYNTHETIC_NOT_FOUND')
        return row
    async def detail(db, owner, order):return order
    scope=load_source('order_review_routes.py',{
        'db':f.db,'repository':f.db,'get_order':get_order,'_detail':detail,
        'HTTPException':BoundaryError,'status':SimpleNamespace(HTTP_403_FORBIDDEN=403),
        'OrderNotFoundError':LookupError,
        'refresh_order_from_salla':f.refresh['refresh_order_from_salla'],
    })
    # Top-level definitions replace _detail; replace only its response projection.
    scope['_detail']=detail
    tree=ast.parse((backend_root()/'order_review_routes.py').read_text(encoding='utf-8'))
    matches=[n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='get_review_detail']
    if len(matches)!=1:raise AssertionError('CALLER_SOURCE_MISSING_OR_AMBIGUOUS')
    node=copy.deepcopy(matches[0]);node.decorator_list=[];node.args.defaults=[]
    import __future__
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
                 'order_review_routes.py','exec',flags=__future__.annotations.compiler_flag),scope)
    return scope['get_review_detail']


class StatusIngestionCallerTests(unittest.TestCase):
    def scenario(self, mode):
        f=ingestion_fixture()
        foreign=copy.deepcopy(f.db.unified_orders.rows[0])
        foreign['user_id']='synthetic-other-owner'
        f.db.unified_orders.rows.append(copy.deepcopy(foreign))
        f.refresh['SallaError']=BoundaryError
        original_transport=f.transport
        async def transport(db, owner, method, path, **kwargs):
            # Tenant-bound credentials: transport cannot access another store.
            if owner!='exit2d-unit-owner':raise BoundaryError()
            if mode=='read_failure' and method=='GET':raise BoundaryError(503)
            result=await original_transport(db,owner,method,path,**kwargs)
            if mode=='reference_mismatch' and method=='GET' and path.startswith('/orders/'):
                result=copy.deepcopy(result)
                if isinstance(result.get('data'),dict):result['data']['reference_id']='synthetic-other-reference'
            return result
        f.refresh['call_salla']=transport
        f.sync['call_salla']=transport
        results=[]
        actual_refresh=f.refresh['refresh_order_from_salla']
        async def observed_refresh(*args,**kwargs):
            result=await actual_refresh(*args,**kwargs)
            results.append(result)
            return result
        f.refresh['refresh_order_from_salla']=observed_refresh
        caller=actual_detail_caller(f)
        async def run():
            user={'id':'exit2d-unit-owner','role':'owner'}
            orders=copy.deepcopy([r for r in f.db.unified_orders.rows if r['user_id']==user['id']])
            pieces=copy.deepcopy(f.db[f.route['PIECES']].rows)
            await compare(f.base,f.route,f.db)
            if mode not in ('ineligible','provider_denied'):
                for row in orders:
                    await original_transport(f.db,user['id'],'POST','/orders/'+row['order_id']+'/status',json={'status_id':71})
            if mode=='provider_denied':
                f.simulator.mode='deny'
                for row in orders:
                    status,_=f.simulator.respond('POST','/admin/v2/orders/'+row['order_id']+'/status',
                        'Bearer '+f.token,{'status_id':71})
                    self.assertEqual(status,403,'DENIAL_NOT_EXERCISED')
                self.assertEqual(f.simulator.counts['status_writes'],0)
            f.Clock.current=f.now+timedelta(seconds=121)
            if mode=='fresh':f.Clock.current=f.now
            # Expiry alone has no callback: files remain hidden before the GET.
            self.assertEqual((await compare(f.base,f.route,f.db))[0],(8,2,8,0,8,0))
            if mode=='wrong_tenant':
                user={'id':'synthetic-other-owner','role':'owner'}
            baseline=copy.deepcopy(f.db.unified_orders.rows)
            for row in orders:
                if mode=='wrong_tenant' and row!=orders[0]:continue
                await caller(row['order_number'],user)
            expected_codes={'read_failure':'salla_refresh_failed',
                            'reference_mismatch':'salla_order_reference_mismatch',
                            'wrong_tenant':'salla_refresh_failed'}
            if mode in expected_codes:
                self.assertTrue(all(not r.get('ok') and not r.get('updated') and
                    r.get('code')==expected_codes[mode] for r in results),'INGESTION_FAILURE_NOT_IDENTIFIED')
            elif mode=='fresh':
                self.assertTrue(all(r.get('ok') and not r.get('updated') and r.get('skipped') and
                    r.get('reason')=='fresh_local_snapshot' for r in results),'FRESH_SKIP_NOT_IDENTIFIED')
            else:
                self.assertTrue(all(r.get('ok') and r.get('updated') for r in results),'READ_NOT_INGESTED')
            counts,visible=await compare(f.base,f.route,f.db)
            if mode=='success':
                self.assertEqual(counts,(8,2,8,8,0,2))
                self.assertTrue({(r['batch_id'],r['file_number']) for r in visible['files']}==
                    {(r['batch_id'],r['file_number']) for r in f.data['files']},'FILE_IDENTITY_CHANGED')
                # The caller default enters optional routing; imports are blocked.
                # This test proves ingestion only, not BNPL/automatic routing.
            else:
                self.assertEqual(counts,(8,2,8,0,8,0),'FAILED_BOUNDARY_REVEALED_FILES')
                if mode in ('fresh','read_failure','reference_mismatch','wrong_tenant'):
                    self.assertTrue(f.db.unified_orders.rows==baseline,'FAILED_BOUNDARY_MUTATED_ORDER')
            self.assertTrue([(p['piece_id'],p['batch_id'],p['order_number']) for p in f.db[f.route['PIECES']].rows]==
                [(p['piece_id'],p['batch_id'],p['order_number']) for p in pieces],'PIECE_LINK_CHANGED')
            fields=('order_item_id','unit_index','specifications_snapshot','product_options_snapshot')
            self.assertTrue([[p.get(k) for k in fields] for p in f.db[f.route['PIECES']].rows]==
                [[p.get(k) for k in fields] for p in pieces],'PIECE_CONTENT_CHANGED')
            history=copy.deepcopy(f.db[f.route['ROUTE_EVENTS']].rows)
            await compare(f.base,f.route,f.db)
            self.assertTrue(history==f.db[f.route['ROUTE_EVENTS']].rows,'DUPLICATE_TRANSITION')
            if mode=='wrong_tenant':
                foreign_counts,_=await compare(f.base,f.route,f.db,owner=user['id'])
                self.assertEqual(foreign_counts[0],0,'TENANT_PIECE_DISCLOSURE')
            self.assertEqual(f.simulator.counts['unexpected'],0)
            self.assertTrue([r for r in f.db.unified_orders.rows if r['user_id']==foreign['user_id']]==
                [foreign],'OTHER_TENANT_ORDER_CHANGED')
        asyncio.run(run())

    def test_actual_detail_caller_ingests_and_reveals_same_files(self):self.scenario('success')
    def test_actual_detail_caller_fresh_success_does_not_transition(self):self.scenario('fresh')
    def test_provider_denial_does_not_transition(self):self.scenario('provider_denied')
    def test_read_failure_is_nonblocking_but_does_not_transition(self):self.scenario('read_failure')
    def test_reference_mismatch_does_not_transition(self):self.scenario('reference_mismatch')
    def test_other_tenant_cannot_ingest_or_reveal_owner_files(self):self.scenario('wrong_tenant')
    def test_successful_read_of_ineligible_status_does_not_transition(self):self.scenario('ineligible')


if __name__=='__main__':unittest.main()
