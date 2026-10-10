"""Frozen, single-session acceptance harness. Synthetic loopback only.

No runtime monkeypatch; production source loaded from exact git blobs. Real
Collector process calls real authenticated diagnostics route in synthetic ASGI
host. Mongo callbacks are synthetic as in existing Phase1 harness; no DB client.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import http.server
import json
import os
from pathlib import Path
import platform
import secrets
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.request import build_opener, ProxyHandler, Request

from obs1316_load_client import client, BASE, TARGET, MODULES

ROOT = Path(__file__).resolve().parents[2]
BUDGET = json.loads(Path(__file__).with_name('obs1316_acceptance_budget.json').read_text())


def request(port, path):
    with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}{path}', timeout=5) as r:
        return json.load(r)


async def server(args):
    sys.path.insert(0, args.modules)
    from fastapi import FastAPI
    from observability_metrics import metrics
    import observability_metrics as registry
    from observability_middleware import DiagnosticsMiddleware
    from runtime_diagnostics import start_lag_monitor
    from runtime_diagnostics_routes import attach_diagnostics_routes
    from mongo_observability import mongo_metrics
    from resource_governor import governor

    enabled_expected = args.variant == 'C'
    monitor = start_lag_monitor()
    deadline = time.monotonic()+4
    while enabled_expected and not metrics.enabled and time.monotonic()<deadline:
        await asyncio.sleep(.02)
    assert bool(metrics.enabled)==enabled_expected
    app = FastAPI()
    app.add_middleware(DiagnosticsMiddleware)
    attach_diagnostics_routes(app, mongo_client=None, state=lambda: {'fixture':'synthetic'})
    counts = dict(requests=0, disabled_observations=0, errors=[])
    diag_requests=[]
    phase_starts={}
    done=asyncio.Event()

    async def synthetic(scope, receive, send):
        # Existing Phase1 callback/admission workload. No DB or provider call.
        mongo_metrics.connection_check_out_started(None)
        mongo_metrics.connection_checked_out(None)
        for name in ('find','commitTransaction'):
            mongo_metrics.started(SimpleNamespace(command_name=name,command={name:'synthetic'}))
            mongo_metrics.succeeded(SimpleNamespace(command_name=name,duration_micros=2000))
        mongo_metrics.connection_checked_in(None)
        token,_=await governor.acquire('dashboard',task_name='synthetic-acceptance')
        try:
            value=sum(i*i for i in range(128))
            await asyncio.sleep(.002)
        finally:
            await governor.release(token)
        body=json.dumps({'synthetic':value},separators=(',',':')).encode()
        await send({'type':'http.response.start','status':200})
        await send({'type':'http.response.body','body':body})

    measured=DiagnosticsMiddleware(synthetic)
    def state():
        ctl=getattr(registry,'_control',None)
        return dict(counts=counts.copy(),metrics=metrics.snapshot(),
                    control=dict(present=ctl is not None,latched=ctl.latched if ctl else None),
                    diagnostics=diag_requests.copy(),cpu=time.process_time(),wall=time.monotonic())

    async def connection(reader,writer):
        try:
            header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3)
            lines=header.split(b'\r\n'); method,path,_=lines[0].decode().split(' ')
            headers=[tuple(x.split(b':',1)) for x in lines[1:] if b':' in x]
            headers=[(k.lower(),v.strip()) for k,v in headers]
            code=200
            if path.startswith('/harness/'):
                if path.startswith('/harness/start/'):
                    phase_starts[path.rsplit('/',1)[1]]=state()
                payload=state()
                if path.startswith('/harness/end/'):
                    phase=path.rsplit('/',1)[1]; payload['start']=phase_starts[phase]
                if path=='/harness/stop': done.set()
                body=json.dumps(payload).encode()
            else:
                messages=[]
                async def receive(): return {'type':'http.request','body':b''}
                async def send(m): messages.append(m)
                scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method=method,
                           scheme='http',path=path,raw_path=path.encode(),query_string=b'',
                           root_path='',headers=headers,server=('127.0.0.1',0),client=('127.0.0.1',0))
                if path=='/api/health/diagnostics':
                    before=time.monotonic(); cpu=time.process_time()
                    await app(scope,receive,send)
                    diag_requests.append(dict(start=before,end=time.monotonic(),
                                              bracket_process_cpu_seconds=time.process_time()-cpu))
                else:
                    if enabled_expected and not metrics.enabled:
                        counts['disabled_observations']+=1
                    await measured(scope,receive,send)
                    counts['requests']+=1
                code=next(m['status'] for m in messages if m['type']=='http.response.start')
                body=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
            writer.write(f'HTTP/1.1 {code} OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n'.encode()+body)
            await writer.drain()
        except Exception as e:
            counts['errors'].append(type(e).__name__)
        finally:
            writer.close(); await writer.wait_closed()

    host=await asyncio.start_server(connection,'127.0.0.1',0)
    Path(args.ready).write_text(json.dumps({'port':host.sockets[0].getsockname()[1]}))
    await asyncio.wait_for(done.wait(),90)
    host.close(); await host.wait_closed()
    monitor.cancel(); await asyncio.gather(monitor,return_exceptions=True)


def health_server(args):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body=b'{"ok":true}'
            self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    h=http.server.HTTPServer(('127.0.0.1',0),Handler)
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(args.cert,args.key)
    h.socket=ctx.wrap_socket(h.socket,server_side=True)
    Path(args.ready).write_text(json.dumps({'port':h.server_port}))
    h.serve_forever()


def start_child(argv, env, ready):
    ready.unlink(missing_ok=True)
    p=subprocess.Popen([sys.executable,'-B',__file__]+argv,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    deadline=time.monotonic()+10
    while not ready.exists():
        if p.poll() is not None or time.monotonic()>deadline:
            if p.poll() is None: p.kill()
            out,err=p.communicate(); raise RuntimeError('child startup failure '+err[:1500])
        time.sleep(.025)
    return p,json.loads(ready.read_text())['port']


def load(port,seconds,rate):
    r=subprocess.run([sys.executable,'-B',__file__,'--mode','client','--port',str(port),
                      '--seconds',str(seconds),'--rate',str(rate)],capture_output=True,text=True,timeout=seconds+12)
    if r.returncode: raise RuntimeError('synthetic client failure '+r.stderr[:500])
    return json.loads(r.stdout)


def snapshot_counts(s):
    return {'counters':s['metrics']['counters'],
            'hist_counts':{k:v['count'] for k,v in s['metrics']['histograms'].items()}}


def phase(port,worker,collector,label,seconds,rate):
    import psutil
    begin=request(port,'/harness/start/'+label)
    start=time.monotonic(); samples=[]; stop=threading.Event()
    before=collector.cpu_times() if collector else None
    def sample():
        while not stop.is_set():
            try:
                samples.append(dict(at=time.monotonic()-start,worker_rss=worker.memory_info().rss,
                                    collector_rss=collector.memory_info().rss if collector else 0))
            except psutil.Error: break
            stop.wait(.25)
    t=threading.Thread(target=sample); t.start()
    try: result=load(port,seconds,rate)
    finally: stop.set();t.join()
    end=request(port,'/harness/end/'+label)
    after=collector.cpu_times() if collector else None
    wall=end['wall']-end['start']['wall']
    n=end['counts']['requests']-end['start']['counts']['requests']
    delta=end['metrics']['counters'].get('api.status.2xx',0)-end['start']['metrics']['counters'].get('api.status.2xx',0)
    enabled=end['metrics']['enabled']; ctl=end['control']
    diag_count=len(end['diagnostics'])-len(end['start']['diagnostics'])
    active_recording=(delta==n+diag_count if enabled else delta==0)
    xs=[r['at'] for r in samples]; ys=[r['worker_rss'] for r in samples]
    xm=statistics.mean(xs);ym=statistics.mean(ys)
    slope=sum((x-xm)*(y-ym) for x,y in zip(xs,ys))/sum((x-xm)**2 for x in xs) if len(xs)>1 else None
    return dict(p95_ms=result['p95_ms'],p99_ms=result['p99_ms'],throughput=result['throughput'],
                cpu_percent=100*(end['cpu']-end['start']['cpu'])/wall,
                rss_peak_bytes=max(ys),rss_start_bytes=ys[0],rss_end_bytes=ys[-1],rss_slope_bytes_per_second=slope,
                errors=len(result['errors'])+len(end['counts']['errors']),timeouts=result['timeouts'],
                collector_cpu_percent=100*((after.user+after.system)-(before.user+before.system))/wall if before else 0,
                collector_rss_peak_bytes=max(s['collector_rss'] for s in samples),
                raw_client=result,rss_samples=samples,worker_begin=begin,worker_end=end,
                request_count=n,diagnostic_request_count=diag_count,recorded_count=delta,recording_valid=active_recording,
                enabled=enabled,latched=ctl['latched'])


def cell(block,variant,modules,collector_path,health_origin,cert,directory):
    import psutil
    result=dict(block=block,variant=variant,phases={},valid=False)
    env=os.environ.copy()
    env.pop('OBS_METRICS_ENABLED',None);env.pop('OBS_CONTROL_FILE',None)
    token=secrets.token_urlsafe(32)  # Isolated synthetic credential; never persisted.
    env['INTERNAL_DIAGNOSTICS_TOKEN']=token
    control=directory/'control'
    if variant in ('B','C'):
        control.write_text('enabled' if variant=='C' else 'disabled');control.chmod(0o600)
        env['OBS_CONTROL_FILE']=str(control)
    worker=collector=None
    try:
        worker,port=start_child(['--mode','server','--variant',variant,'--modules',str(modules),
                                 '--ready',str(directory/'ready.json')],env,directory/'ready.json')
        result['warmup']=load(port,5,300)
        inventory=directory/'inventory.json'
        inventory.write_text(json.dumps({'workers':{'worker-1':f'http://127.0.0.1:{port}'},'health_origin':health_origin}))
        inventory.chmod(0o600)
        store=directory/'store';store.mkdir(mode=0o700)
        env['SSL_CERT_FILE']=str(cert)
        collector=subprocess.Popen([sys.executable,'-B',str(collector_path),'--enabled','--inventory',str(inventory),
                                    '--output',str(store),'--interval','30'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        wp=psutil.Process(worker.pid); cp=psutil.Process(collector.pid)
        for label,seconds,rate in [('paced',30,300),('capacity',10,0)]:
            result['phases'][label]=phase(port,wp,cp,label,seconds,rate)
        final=request(port,'/harness/state')
        # Stop only the synthetic infinite collector process, never Production.
        collector.terminate();out,err=collector.communicate(timeout=5)
        result['collector_stop_returncode']=collector.returncode
        result['collector_clean_logs']=not out and not err
        records=[]
        for f in sorted(store.glob('*.jsonl')):
            records.extend(json.loads(line) for line in f.read_text().splitlines())
        result['collector_records']=records
        result['collector_storage_bytes']=sum(f.stat().st_size for f in store.glob('*.jsonl'))
        result['collector_private_modes']=all((f.stat().st_mode & 0o777)==0o600 for f in store.glob('*.jsonl')) and (store.stat().st_mode & 0o777)==0o700
        result['collector_privacy_pass']=token not in json.dumps(records)+out+err
        worker_records=[r for r in records if r['target']=='worker-1']
        result['collector_success']=len(worker_records)>=2 and all(r['outcome']=='ok' and r['status']==200 and r.get('metrics',{}).get('enabled')==(variant=='C') for r in worker_records)
        result['health_fixture_success']=any(r['target']=='external-health' and r['outcome']=='ok' for r in records)
        result['diagnostic_requests']=final['diagnostics']
        # Explicit disable only AFTER both measured phases. Verify callbacks stop.
        if variant=='C':
            replacement=directory/'control-new';replacement.write_text('disabled');replacement.chmod(0o600)
            started=time.monotonic();replacement.replace(control)
            while True:
                disabled=request(port,'/harness/state')
                if not disabled['metrics']['enabled'] or time.monotonic()-started>3: break
                time.sleep(.05)
            result['disable_seconds']=time.monotonic()-started
            frozen=snapshot_counts(disabled)
            result['disable_load']=load(port,1,20)
            after=request(port,'/harness/state')
            result['disable_verified']=not after['metrics']['enabled'] and snapshot_counts(after)==frozen and after['metrics']['api']['active']==0
        else: result['disable_verified']=not final['metrics']['enabled']
        wanted=variant=='C'
        result['functional_pass']=(result['collector_success'] and result['health_fixture_success'] and result['collector_clean_logs']
                                  and result['collector_private_modes'] and result['collector_privacy_pass'] and result['disable_verified']
                                  and final['metrics']['api']['active']==0 and not final['counts']['errors']
                                  and final['counts']['disabled_observations']==0
                                  and not result['warmup']['errors']
                                  and all(p['recording_valid'] and p['enabled']==wanted and not p['latched'] for p in result['phases'].values()))
        result['valid']=True
        request(port,'/harness/stop');out,err=worker.communicate(timeout=5)
        result['worker_exit']=worker.returncode
        if worker.returncode or err: result['functional_pass']=False;result['worker_error']=err[:1500]
    except Exception as e:
        result['infrastructure_error']=repr(e)
    finally:
        for p in (collector,worker):
            if p and p.poll() is None:
                p.terminate()
                try: p.communicate(timeout=5)
                except subprocess.TimeoutExpired: p.kill();p.communicate()
    return result


def main(args):
    assert platform.system()=='Linux'
    report=dict(source=TARGET,base=BASE,budget=BUDGET,results=[],runtime_hashes={},
                tool_head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                environment=dict(python=sys.version,platform=platform.platform(),cpus=os.cpu_count()))
    def save(): Path(args.output).write_text(json.dumps(report,indent=2))
    with tempfile.TemporaryDirectory(prefix='obs-accept-') as tmp:
        root=Path(tmp);root.chmod(0o700)
        dirs={}
        for name,ref in [('old',BASE),('new',TARGET)]:
            dirs[name]=root/name;dirs[name].mkdir()
            report['runtime_hashes'][name]={}
            for module in MODULES+('runtime_diagnostics_routes.py',):
                blob=subprocess.check_output(['git','show',f'{ref}:backend/{module}'],cwd=ROOT)
                (dirs[name]/module).write_bytes(blob)
                report['runtime_hashes'][name][module]=hashlib.sha256(blob).hexdigest()
        collector=root/'collector.py'
        blob=subprocess.check_output(['git','show',f'{TARGET}:scripts/observability_phase1_collector.py'],cwd=ROOT)
        collector.write_bytes(blob);report['collector_sha256']=hashlib.sha256(blob).hexdigest()
        cert,key=root/'cert.pem',root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),
                        '-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],check=True,capture_output=True)
        key.chmod(0o600)
        health,hport=start_child(['--mode','health','--cert',str(cert),'--key',str(key),'--ready',str(root/'health-ready')],os.environ.copy(),root/'health-ready')
        try:
            save()
            for block,order in enumerate(BUDGET['order']):
                for variant in order:
                    directory=root/f'{block}-{variant}';directory.mkdir(mode=0o700)
                    r=cell(block,variant,dirs['old' if variant in ('A1','A2') else 'new'],collector,
                           f'https://127.0.0.1:{hport}',cert,directory)
                    report['results'].append(r);save()
                    print(json.dumps(dict(block=block,variant=variant,valid=r['valid'],functional_pass=r.get('functional_pass'),failure=r.get('infrastructure_error'))),flush=True)
        finally:
            health.terminate();health.communicate(timeout=5)
        report['runtime_hashes_after']={name:{m:hashlib.sha256((directory/m).read_bytes()).hexdigest() for m in MODULES+('runtime_diagnostics_routes.py',)} for name,directory in dirs.items()}
        report['collector_hash_after']=hashlib.sha256(collector.read_bytes()).hexdigest()
    report['complete']=len(report['results'])==32;save()
    return 0 if report['complete'] else 1  # Collection complete is NOT acceptance.


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',default='session',choices=['session','server','client','health'])
    for name in ('modules','variant','ready','cert','key'):p.add_argument('--'+name)
    p.add_argument('--port',type=int);p.add_argument('--seconds',type=float,default=30);p.add_argument('--rate',type=float,default=300)
    p.add_argument('--output',default='evidence/raw.json');args=p.parse_args()
    if args.mode=='server':asyncio.run(server(args))
    elif args.mode=='client':asyncio.run(client(args))
    elif args.mode=='health':health_server(args)
    else:raise SystemExit(main(args))
