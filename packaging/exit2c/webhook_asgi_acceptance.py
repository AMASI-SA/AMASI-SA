"""Independent ASGI contract, also invoked by the preparation test image.

Uses real module imports, original endpoint body and real installed dispatch.
Registration excludes unrelated routes that import server; verification and
business functions are neither replaced nor filtered. Mongo is mongomock_motor.
Run in a disposable process; environment, networking and output are bounded.
"""
import argparse
import ast
import asyncio
import contextlib
import copy
import hashlib
import hmac
import io
import importlib
import json
import logging
import os
from pathlib import Path
import secrets
import sys


def require(condition, code):
    if not condition:raise AssertionError(code)


class Writes:
    def __init__(self, db):self.raw=db;self.counts={};self.effects={}
    def __getattr__(self,name):return self[name]
    def __getitem__(self,name):
        parent=self
        class Collection:
            def __getattr__(self,method):
                target=getattr(parent.raw[name],method)
                if method not in ('insert_one','insert_many','update_one','update_many',
                                  'replace_one','delete_one','delete_many','find_one_and_update'):
                    return target
                async def write(*args,**kwargs):
                    key=(name,method);parent.counts[key]=parent.counts.get(key,0)+1
                    result=await target(*args,**kwargs)
                    effect=parent.effects.setdefault(name,{'inserted':0,'modified':0,'matched':0})
                    effect['inserted']+=int(getattr(result,'inserted_id',None) is not None)
                    effect['inserted']+=len(getattr(result,'inserted_ids',[]))
                    effect['inserted']+=int(getattr(result,'upserted_id',None) is not None)
                    effect['modified']+=getattr(result,'modified_count',0)
                    effect['matched']+=getattr(result,'matched_count',0)
                    return result
                return write
        return Collection()


async def exercise(report):
    from fastapi import FastAPI
    import httpx
    from mongomock_motor import AsyncMongoMockClient
    from salla_integration import routes
    from salla_integration.cart_order_reconciliation import install_verified_order_cart_reconciliation
    from simulated_status_webhook import confirmed_event, OWNER_MERCHANT, OTHER_MERCHANT, signed_body, require_ingestion
    from salla_http_simulator import Fixture, ORDER_IDS
    from test_supplier_file_visibility import environment, compare
    from source_paths import backend_root

    # Use actual installer, not a reconstructed/no-op business wrapper.
    install_verified_order_cart_reconciliation()
    # Match package attach order: installation precedes Easy Mode's by-name import.
    from salla_integration import easy_mode_webhook
    require(getattr(easy_mode_webhook.capture_unknown_event,
                    '_mezan_cart_reconciliation_installed',False),'COMPOSED_DISPATCH_NOT_INSTALLED')
    report['composed_dispatch_installed']=True
    tree=ast.parse((backend_root()/'salla_integration/routes.py').read_text(encoding='utf-8'))
    nodes=[n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='salla_app_webhook']
    require(len(nodes)==1,'WEBHOOK_SOURCE_AMBIGUOUS')
    node=nodes[0]
    require(ast.literal_eval(node.decorator_list[0].args[0])=='/webhooks/app','WEBHOOK_ROUTE_CHANGED')
    node.decorator_list=[]
    data,base,route,memory=environment()
    client=AsyncMongoMockClient();db=Writes(client['mezan_exit2d_webhook'])
    report['_writes']=db.counts
    report['_effects']=db.effects
    for name,col in memory.items():
        if col.rows:await db[name].insert_many(col.rows)
    await db['mezan_preparation_unit_allocations_v2'].insert_many(copy.deepcopy(data['allocations']))
    await db.salla_integrations.insert_many([
        {'store_id':OWNER_MERCHANT,'user_id':'exit2d-unit-owner','status':'connected'},
        {'store_id':OTHER_MERCHANT,'user_id':'exit2d-other-owner','status':'connected'},
    ])
    # Unrelated store is present from the beginning, not reseeded mid-cycle.
    await db.unified_orders.insert_one({'user_id':'exit2d-other-owner',
        'order_number':'synthetic-other-order','order_status':'بانتظار المراجعة'})
    scope={**vars(routes),**vars(easy_mode_webhook),'db':db}
    import __future__
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
                 'salla_integration/routes.py','exec',flags=__future__.annotations.compiler_flag),scope)
    app=FastAPI()
    app.add_api_route('/api/salla/webhooks/app',scope['salla_app_webhook'],methods=['POST'])
    secret=os.environ['SALLA_WEBHOOK_SECRET']
    fixture=Fixture(secrets.token_urlsafe(32),'success')
    before=(await compare(base,route,db))[0]
    require(before==(8,2,8,0,8,0),'PRE_EVENT_VISIBILITY')
    report['absent_event']='PASS'
    db.counts.clear()
    db.effects.clear()
    initial_pieces=await db[route['PIECES']].find({}, {'_id':0}).to_list(length=100)
    initial_allocations=await db['mezan_preparation_unit_allocations_v2'].find({}, {'_id':0}).to_list(length=100)
    piece_fields=('piece_id','batch_id','file_number','order_number','order_item_id','unit_index',
                  'quantity','image_url','specifications_snapshot','product_options_snapshot','responsible_employee_id')
    def piece_identity(rows):return sorted([tuple(json.dumps(r.get(k),sort_keys=True) for k in piece_fields)
                                           for r in rows])
    foreign_before=await db.unified_orders.find({'user_id':'exit2d-other-owner'},{'_id':0}).to_list(length=10)
    events=[]
    loop=asyncio.get_running_loop()
    previous_factory=loop.get_task_factory()
    def track_task(loop,coro,**kwargs):
        report['application_tasks_created']+=1
        return asyncio.Task(coro,loop=loop,**kwargs)
    loop.set_task_factory(track_task)
    report['_restore_factory']=(loop,previous_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as http:
        async def post_event(event):
            payload,headers=signed_body(event,secret)
            response=await http.post('/api/salla/webhooks/app',content=payload,headers=headers)
            require(response.status_code==200,'WEBHOOK_HTTP')
            return response.json()
        # Original verifier rejects before dispatch and before any Mongo write.
        bad=await http.post('/api/salla/webhooks/app',content=b'{}',
            headers={'x-salla-signature':'0'*64,'x-salla-security-strategy':'Signature'})
        require(bad.status_code==401 and not db.counts,'INVALID_SIGNATURE_MUTATED')
        report['invalid_signature']='PASS'
        invalid=b'not-json'
        signature=hmac.new(secret.encode(),invalid,hashlib.sha256).hexdigest()
        bad=await http.post('/api/salla/webhooks/app',content=invalid,
            headers={'x-salla-signature':signature,'x-salla-security-strategy':'Signature'})
        require(bad.status_code==400 and not db.counts,'INVALID_EVENT_MUTATED')
        report['invalid_json']='PASS'
        for internal_id in sorted(ORDER_IDS):
            code,_=fixture.respond('POST','/admin/v2/orders/'+internal_id+'/status',
                'Bearer '+fixture.token,{'status_id':71})
            require(code==200,'PROVIDER_CONFIRMATION')
            event=confirmed_event(fixture,internal_id)
            events.append(event)
            payload=json.dumps(event,separators=(',',':'),ensure_ascii=True).encode()
            signature=hmac.new(secret.encode(),payload,hashlib.sha256).hexdigest()
            response=await http.post('/api/salla/webhooks/app',content=payload,
                headers={'x-salla-signature':signature,'x-salla-security-strategy':'Signature'})
            report['http_status']=response.status_code
            require(response.status_code==200,'WEBHOOK_HTTP')
            result=response.json()
            captured=result.get('capture',{})
            report['ancillary_flags']={
                'ledger_synced':captured.get('order_sync',{}).get('attribution_ledger',{}).get('synced') is True,
                'snap_disabled':captured.get('snapchat_capi',{}).get('reason')=='capi_disabled',
                'routing_error':bool(captured.get('order_sync',{}).get('auto_fulfillment',{}).get('error')),
                'first_party_error':bool(captured.get('order_sync',{}).get('first_party_attribution',{}).get('error_type')),
                'cart_error':captured.get('abandoned_cart_conversion',{}).get('reason')=='reconciliation_exception',
                'shipment_error':bool(captured.get('shipment_sync',{}).get('error'))}
            accepted=require_ingestion(result)
            report['ledger_synced']=report.get('ledger_synced',0)+int(accepted['order_sync']['attribution_ledger']['synced'])
            report['capi_disabled']=report.get('capi_disabled',0)+int(accepted['snapchat_capi']['reason']=='capi_disabled')
            stored=await db.unified_orders.find_one({'user_id':'exit2d-unit-owner','order_number':event['data']['reference_id']})
            require(stored is not None and stored['order_status']==event['data']['status']['name']
                    and stored['raw_by_source']['salla_direct']['status']==event['data']['status'],'CONFIRMED_STATUS_MISMATCH')
            # Keep response evidence in memory; never log raw response/error text.
            report['response_keys']=sorted(k for k in result if k in ('ok','stored','capture'))
            captures=await db.salla_webhook_event_captures.find({}).to_list(length=10)
            report['capture_count']=len(captures)
            report['synced_count']=sum(bool(c.get('order_sync',{}).get('synced')) for c in captures)
            report['ancillary_failure_count']=sum(
                bool(c.get(section,{}).get('error')) for c in captures
                for section in ('order_sync','shipment_sync','snapchat_capi','abandoned_cart_sync'))
            report['auto_routing_evaluation_failed']=sum(c.get('order_sync',{}).get(
                'auto_fulfillment',{}).get('reason')=='evaluation_failed' for c in captures)
            report['ledger_bridge_unavailable']=sum(c.get('order_sync',{}).get(
                'attribution_ledger',{}).get('reason')=='ledger_bridge_unavailable' for c in captures)
            require(report['synced_count']==len(captures),'WEBHOOK_NOT_SYNCED')
            require(report['ancillary_failure_count']==0,'WEBHOOK_ANCILLARY_FAILURE')
            require(report['auto_routing_evaluation_failed']==0 and report['ledger_bridge_unavailable']==0,
                    'WEBHOOK_ANCILLARY_FAILURE')
        counts,visible=await compare(base,route,db)
        report['eligible_pieces']=counts[3];report['visible_files']=counts[5]
        require(counts==(8,2,8,8,0,2),'POST_EVENT_VISIBILITY')
        require({(r['batch_id'],r['file_number']) for r in visible['files']}==
                {(r['batch_id'],r['file_number']) for r in data['files']},'FILE_IDENTITY_CHANGED')
        pieces=await db[route['PIECES']].find({}, {'_id':0}).to_list(length=100)
        require(piece_identity(pieces)==piece_identity(initial_pieces),'PIECE_IDENTITY_CHANGED')
        require(await db.unified_orders.find({'user_id':'exit2d-other-owner'},{'_id':0}).to_list(length=10)==foreign_before,
                'FOREIGN_ORDER_CHANGED')
        report['two_events']='PASS';report['piece_identity']='PASS'
        before_history=await db[route['ROUTE_EVENTS']].find({}, {'_id':0}).to_list(length=100)
        stable_names=('unified_orders',route['PIECES'],'mezan_customer_identities_v1','mezan_preparation_unit_allocations_v2','mezan_attribution_order_ledger_v1',
                      'mezan_fulfillment_decisions_v2','products','mezan_inventory_reservations_v2')
        stable_counts={name:await db[name].count_documents({}) for name in stable_names}
        for event in events:require_ingestion(await post_event(event))
        require(all([await db[name].count_documents({})==count for name,count in stable_counts.items()]),'DUPLICATE_ENTITIES')
        await compare(base,route,db)
        require(await db[route['ROUTE_EVENTS']].find({}, {'_id':0}).to_list(length=100)==before_history,'DUPLICATE_ROUTE_TRANSITION')
        captures=await db.salla_webhook_event_captures.find({}).to_list(length=10)
        require(len(captures)==2 and all(c['delivery_count']==2 for c in captures),'DELIVERY_AUDIT_MISMATCH')
        report['duplicate']='PASS';report['duplicate_capture_documents']=2;report['delivery_attempts']=4
        report['route_transition_count']=len(before_history)
        # Signed but unknown merchant / semantically missing order must not become ingestion success.
        for label,event in (
            ('unmapped_merchant',{**copy.deepcopy(events[0]),'merchant':'exit2d-unmapped-store'}),
            ('invalid_semantics',{'event':'order.status.updated','merchant':OWNER_MERCHANT,'data':{}})):
            owner_before=await db.unified_orders.find({}, {'_id':0}).to_list(length=100)
            rejected=await post_event(event)
            require(rejected['capture']['order_sync'].get('synced') is False,'INVALID_EVENT_SYNCED')
            try:require_ingestion(rejected)
            except ValueError as exc:require(exc.args==('WEBHOOK_NOT_SYNCED',),'REJECTION_CLASSIFICATION')
            else:raise AssertionError('HTTP200_FALSE_ACCEPTED')
            require(await db.unified_orders.find({}, {'_id':0}).to_list(length=100)==owner_before,'INVALID_EVENT_MUTATED')
            report[label]='PASS'
        # A mapped other merchant writes only its own scope, never the owner's rows or pieces.
        owner_before=await db.unified_orders.find({'user_id':'exit2d-unit-owner'},{'_id':0}).to_list(length=10)
        other_event=copy.deepcopy(events[0]);other_event['merchant']=OTHER_MERCHANT
        require_ingestion(await post_event(other_event))
        require(await db.unified_orders.find({'user_id':'exit2d-unit-owner'},{'_id':0}).to_list(length=10)==owner_before,
                'TENANT_OWNER_MUTATED')
        other_counts,_=await compare(base,route,db,owner='exit2d-other-owner')
        require(other_counts[0]==0 and other_counts[5]==0,'TENANT_PIECES_EXPOSED')
        require(piece_identity(await db[route['PIECES']].find({}, {'_id':0}).to_list(length=100))==piece_identity(initial_pieces),
                'PIECE_IDENTITY_CHANGED')
        require(await db['mezan_preparation_unit_allocations_v2'].find({}, {'_id':0}).to_list(length=100)==initial_allocations,
                'ALLOCATION_IDENTITY_CHANGED')
        report['allocations_preserved']=len(initial_allocations)
        report['tenant_isolation']='PASS'
        report['outbox_records']=await db['mezan_snapchat_capi_outbox_v1'].count_documents({})
        report['scheduler_records']=await db['mezan_snapchat_capi_scheduler_v1'].count_documents({})
        report['inventory_reservations']=await db['mezan_inventory_reservations_v2'].count_documents({})
        require(report['outbox_records']==report['scheduler_records']==report['inventory_reservations']==0,'EXECUTABLE_WORK_CREATED')
    report['write_operations']=sum(db.counts.values())
    report['written_collection_count']=len({k[0] for k in db.counts})
    report['simulated_provider_calls']=fixture.counts['simulated_provider_calls']
    report['unexpected']=fixture.counts['unexpected']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    from source_paths import backend_root
    sys.path.insert(0,str(backend_root()))
    keep={k:v for k,v in os.environ.items() if k.upper() in
          ('SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','PATH')}
    # The real guard and its stdlib-only dependencies are the only backend
    # imports permitted before validation. Use its existing fixture contract.
    import independent_runtime as runtime
    os.environ.clear();os.environ.update(keep);os.environ.update(runtime.SYNTHETIC)
    os.environ['MEZAN_ACCEPTANCE_PROFILE']=runtime.SALLA_SIMULATOR_PROFILE
    os.environ['SALLA_API_BASE']='http://127.0.0.1:8093/admin/v2'
    os.environ['SALLA_AUTH_BASE']='http://127.0.0.1:8093'
    os.environ['MEZAN_WORKER_ENABLED']='0'
    os.environ['SALLA_WEBHOOK_SECRET']=secrets.token_urlsafe(32)
    os.environ['MEZAN_SNAPCHAT_CAPI_ENABLED']='false'
    # Same public fixture already approved for the simulator profile; never live data.
    os.environ['SALLA_TOKEN_ENC_KEY']=runtime.SALLA_SIMULATOR_KEY
    report={'scope':'original_webhook_gate_probe','network_attempts':0,'dotenv_attempts':0,
            'log_errors':0,'application_tasks_created':0,'complete':False}
    class SafeLogs(logging.Handler):
        def emit(self,record):
            if record.levelno>=logging.ERROR:
                report['log_errors']+=1
                if record.exc_info and record.exc_info[1]:
                    exc=record.exc_info[1]
                    report['ancillary_exception_type']=type(exc).__name__
                    if isinstance(exc,ModuleNotFoundError):
                        report['ancillary_missing_module']=exc.name if exc.name in ('jwt','bcrypt','dotenv','requests') else 'OTHER'
    logging.getLogger().handlers[:]=[SafeLogs()]
    logging.getLogger().setLevel(logging.INFO)
    runner=asyncio.Runner();runner.get_loop()  # Interpreter self-pipe precedes application boundary.
    def audit(event,args):
        if event in ('socket.connect','socket.bind','socket.getaddrinfo'):
            report['network_attempts']+=1;raise RuntimeError('NETWORK_FORBIDDEN')
        if event=='open' and isinstance(args[0],str) and Path(args[0]).name.startswith('.env'):
            report['dotenv_attempts']+=1;raise RuntimeError('DOTENV_FORBIDDEN')
    sys.addaudithook(audit)
    output=io.StringIO()
    try:
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            try:
                runtime.validate_before_import('web')
            except Exception:
                raise AssertionError('ASGI_RUNTIME_GUARD_REJECTED') from None
            report['runtime_guard_passed']=True
            if args.preflight_only:
                failures={}
                modules=('salla_integration.routes','salla_integration.webhook_order_sync',
                         'mezan_attribution_ledger_sync','first_party_attribution',
                         'fulfillment_v2_routes','integrations_control_center.snapchat_capi_purchases',
                         'salla_integration.cart_order_reconciliation','bnpl.billing_eligible')
                for name in modules:
                    try:importlib.import_module(name)
                    except ModuleNotFoundError as exc:
                        failures[name]={'type':'ModuleNotFoundError','dependency':exc.name}
                    except Exception as exc:failures[name]={'type':type(exc).__name__}
                report['import_failures']=failures
                report['imports_checked']=len(modules)
                require(not failures,'DEPENDENCY_PREFLIGHT')
            else:
                runner.run(exercise(report))
        require(report['network_attempts']==0,'NETWORK_ATTEMPT')
        require(report['log_errors']==0,'APPLICATION_LOG_ERROR')
        require(report['application_tasks_created']==0,'EXECUTABLE_WORK_CREATED')
        require(not output.getvalue(),'UNCLASSIFIED_OUTPUT')
        report['complete']=True
    except Exception as exc:
        report['failure_type']=type(exc).__name__
        allowed={'WEBHOOK_NOT_SYNCED','WEBHOOK_ANCILLARY_FAILURE','POST_EVENT_VISIBILITY',
                 'WEBHOOK_HTTP','NETWORK_ATTEMPT','APPLICATION_LOG_ERROR','UNCLASSIFIED_OUTPUT',
                 'INVALID_SIGNATURE_MUTATED','INVALID_EVENT_MUTATED','COMPOSED_DISPATCH_NOT_INSTALLED',
                 'PRE_EVENT_VISIBILITY','WEBHOOK_SOURCE_AMBIGUOUS','WEBHOOK_ROUTE_CHANGED','PROVIDER_CONFIRMATION'}
        allowed.add('DEPENDENCY_PREFLIGHT')
        allowed.add('ASGI_RUNTIME_GUARD_REJECTED')
        allowed.update(('CONFIRMED_STATUS_MISMATCH','PIECE_IDENTITY_CHANGED','FOREIGN_ORDER_CHANGED',
                        'DUPLICATE_ENTITIES','DUPLICATE_ROUTE_TRANSITION','DELIVERY_AUDIT_MISMATCH',
                        'INVALID_EVENT_SYNCED','REJECTION_CLASSIFICATION','HTTP200_FALSE_ACCEPTED',
                        'TENANT_OWNER_MUTATED','TENANT_PIECES_EXPOSED','EXECUTABLE_WORK_CREATED'))
        allowed.update(('FILE_IDENTITY_CHANGED','ALLOCATION_IDENTITY_CHANGED'))
        report['check']=exc.args[0] if isinstance(exc,(AssertionError,ValueError)) and exc.args and exc.args[0] in allowed else 'ASGI_BOUNDARY_INCOMPLETE'
        if isinstance(exc,ModuleNotFoundError):
            report['missing_dependency']=exc.name if exc.name in ('jwt','bcrypt','dotenv','httpx','fastapi','pymongo','mongomock_motor') else 'OTHER'
    finally:
        restore=report.pop('_restore_factory',None)
        if restore:restore[0].set_task_factory(restore[1])
        runner.close();os.environ.pop('SALLA_WEBHOOK_SECRET',None)
        os.environ.pop('SALLA_TOKEN_ENC_KEY',None)
        counts=report.pop('_writes',{})
        report['write_operations']=sum(counts.values())
        report['written_collection_count']=len({key[0] for key in counts})
        known=('unified_orders','products','mezan_products','salla_webhook_event_captures',
               'customer_identities','unified_customers','mezan_customer_identities',
               'mezan_customer_identities_v1','first_party_attribution_orders',
               'order_review_workflows','snapchat_capi_purchase_outbox',
               'mezan_attribution_order_ledger_v1','mezan_preparation_pieces_v1','mezan_preparation_route_events_v1',
               'mezan_fulfillment_decisions_v2','mezan_inventory_reservations_v2')
        report['write_counts']={name:sum(n for (collection,_),n in counts.items() if collection==name)
                               for name in known if any(k[0]==name for k in counts)}
        report['unclassified_collection_writes']=sum(n for (name,_),n in counts.items() if name not in known)
        # Collection names are source-defined metadata, never document values.
        report['written_collections']=sorted({name for name,_ in counts})
        report['write_effects']={name:effect for name,effect in report.pop('_effects',{}).items() if name in known}
    print(json.dumps(report,sort_keys=True))
    return 0 if report['complete'] else 1


if __name__=='__main__':raise SystemExit(main())
