"""Independent local ASGI proof. Main preparation runner is not wired here.

Uses real module imports, original endpoint body and real installed dispatch.
Registration excludes unrelated routes that import server; verification and
business functions are neither replaced nor filtered. Mongo is mongomock_motor.
Run in a disposable process; environment, networking and output are bounded.
"""
import argparse
import ast
import asyncio
import contextlib
import hashlib
import hmac
import io
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
    from simulated_status_webhook import confirmed_event, OWNER_MERCHANT, OTHER_MERCHANT
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
    secret=secrets.token_urlsafe(32);os.environ['SALLA_WEBHOOK_SECRET']=secret
    fixture=Fixture(secrets.token_urlsafe(32),'success')
    before=(await compare(base,route,db))[0]
    require(before==(8,2,8,0,8,0),'PRE_EVENT_VISIBILITY')
    report['absent_event']='PASS'
    db.counts.clear()
    db.effects.clear()
    loop=asyncio.get_running_loop()
    previous_factory=loop.get_task_factory()
    def track_task(loop,coro,**kwargs):
        report['application_tasks_created']+=1
        return asyncio.Task(coro,loop=loop,**kwargs)
    loop.set_task_factory(track_task)
    report['_restore_factory']=(loop,previous_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as http:
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
            payload=json.dumps(event,separators=(',',':'),ensure_ascii=True).encode()
            signature=hmac.new(secret.encode(),payload,hashlib.sha256).hexdigest()
            response=await http.post('/api/salla/webhooks/app',content=payload,
                headers={'x-salla-signature':signature,'x-salla-security-strategy':'Signature'})
            report['http_status']=response.status_code
            require(response.status_code==200,'WEBHOOK_HTTP')
            result=response.json()
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
        counts,_=await compare(base,route,db)
        report['eligible_pieces']=counts[3];report['visible_files']=counts[5]
        require(counts==(8,2,8,8,0,2),'POST_EVENT_VISIBILITY')
    report['write_operations']=sum(db.counts.values())
    report['written_collection_count']=len({k[0] for k in db.counts})
    report['simulated_provider_calls']=fixture.counts['simulated_provider_calls']
    report['unexpected']=fixture.counts['unexpected']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dependency-path',action='append',default=[])
    args=parser.parse_args()
    from source_paths import backend_root
    sys.path[:0]=[str(backend_root()),*args.dependency_path]
    keep={k:v for k,v in os.environ.items() if k.upper() in
          ('SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','PATH')}
    os.environ.clear();os.environ.update(keep);os.environ['APP_ENV']='test'
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
            runner.run(exercise(report))
        require(report['network_attempts']==0,'NETWORK_ATTEMPT')
        require(report['log_errors']==0,'APPLICATION_LOG_ERROR')
        require(not output.getvalue(),'UNCLASSIFIED_OUTPUT')
        report['complete']=True
    except Exception as exc:
        report['failure_type']=type(exc).__name__
        allowed={'WEBHOOK_NOT_SYNCED','WEBHOOK_ANCILLARY_FAILURE','POST_EVENT_VISIBILITY',
                 'WEBHOOK_HTTP','NETWORK_ATTEMPT','APPLICATION_LOG_ERROR','UNCLASSIFIED_OUTPUT',
                 'INVALID_SIGNATURE_MUTATED','INVALID_EVENT_MUTATED','COMPOSED_DISPATCH_NOT_INSTALLED',
                 'PRE_EVENT_VISIBILITY','WEBHOOK_SOURCE_AMBIGUOUS','WEBHOOK_ROUTE_CHANGED','PROVIDER_CONFIRMATION'}
        report['check']=exc.args[0] if isinstance(exc,AssertionError) and exc.args and exc.args[0] in allowed else 'ASGI_BOUNDARY_INCOMPLETE'
        if isinstance(exc,ModuleNotFoundError):
            report['missing_dependency']=exc.name if exc.name in ('jwt','bcrypt','dotenv','httpx','fastapi','pymongo','mongomock_motor') else 'OTHER'
    finally:
        restore=report.pop('_restore_factory',None)
        if restore:restore[0].set_task_factory(restore[1])
        runner.close();os.environ.pop('SALLA_WEBHOOK_SECRET',None)
        counts=report.pop('_writes',{})
        report['write_operations']=sum(counts.values())
        report['written_collection_count']=len({key[0] for key in counts})
        known=('unified_orders','products','mezan_products','salla_webhook_event_captures',
               'customer_identities','unified_customers','mezan_customer_identities',
               'mezan_customer_identities_v1','first_party_attribution_orders',
               'order_review_workflows','snapchat_capi_purchase_outbox',
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
