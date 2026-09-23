#!/usr/bin/env python3
"""Host-bound Preview recovery on the persistent /app mount; never Production."""
import argparse, configparser, hashlib, io, json, os, secrets, shutil, socket, subprocess, sys, tarfile
from pathlib import Path
SHA = 'a1fe9dd6e8c769fa399b472212e12569d310b070'
ADAPTER_SHA = '4ccc4247432d075cbd26b64aeba149492b47871b'
HOST = 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
ORIGIN = 'https://salla-analytics.preview.emergentagent.com'
STATE = Path('/app/.worktrees/p02-runtime-lock-preview-20260920')
SOURCE = STATE / 'source'
CONF = Path('/etc/supervisor/conf.d/supervisord.conf')
OWNER = 'p02-runtime-lock-preview-20260920 root agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
LOCK = Path('/tmp/mz2-p01-operational-preview.lock')
SCRIPT = Path(__file__).resolve()
def output(args):
    return subprocess.check_output(args, text=True).strip()
def guard():
    assert socket.gethostname() == HOST, 'Wrong host'
    assert json.loads(output(['python','/app/scripts/production_release_guard.py','status'])).get('active') is False, 'Release active'
    assert LOCK.read_text() == OWNER, 'Reservation is not owned'
    assert output(['findmnt','-T','/app','-n','-o','TARGET']) == '/app', 'Persistent mount missing'
def private(path, data):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f: f.write(data)
def prepare():
    guard()
    assert not STATE.exists(), 'Inspect existing preparation instead of overwriting'
    assert output(['git','-C','/app','rev-parse','origin/chatgpt/p01-p02-runtime-lock-20260920']) == SHA
    for p in ['frontend/package.json','frontend/yarn.lock']:
        assert subprocess.check_output(['git','-C','/app','show',SHA+':'+p]) == (Path('/app')/p).read_bytes()
    STATE.mkdir(mode=0o700); SOURCE.mkdir(mode=0o700)
    private(STATE/'supervisor.before',CONF.read_bytes())
    archive=subprocess.check_output(['git','-C','/app','archive',SHA])
    with tarfile.open(fileobj=io.BytesIO(archive)) as t:
        for m in t:
            p=Path(m.name)
            assert not p.is_absolute() and '..' not in p.parts
            if any(v == '.env' or v.startswith('.env.') for v in p.parts): continue
            dest=SOURCE/p
            if m.isdir(): dest.mkdir(parents=True,exist_ok=True)
            elif m.isfile():
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(t.extractfile(m).read());dest.chmod(m.mode&0o777)
            else: raise RuntimeError('Source archive links forbidden')
    (SOURCE/'backend/.env').symlink_to('/app/backend/.env')
    # Install private dependencies; shared release builds may remove /app node_modules.
    tool = SOURCE/'scripts/frontend_release_toolchain.py'
    toolenv = dict(os.environ, XDG_CACHE_HOME=str(STATE/'toolcache'), YARN_CACHE_FOLDER='/tmp/preview-runtime-yarn-cache-20260919')
    with (STATE/'install.log').open('w') as log:
        subprocess.run([sys.executable,str(tool),'exec','--','yarn','--cwd',str(SOURCE/'frontend'),'install','--frozen-lockfile','--non-interactive'],env=toolenv,stdout=log,stderr=subprocess.STDOUT,check=True)
    node = subprocess.check_output([sys.executable,str(tool),'exec','--','node','-p','process.execPath'],env=toolenv,text=True).strip().splitlines()[-1]
    assert Path(node).is_file() and str(STATE) in node
    private(STATE/'preview-session-signing.secret',secrets.token_urlsafe(64).encode())
    adapter=subprocess.check_output(['git','-C','/app','show',ADAPTER_SHA+':scripts/preview_password_runtime.py'],text=True)
    adapter=adapter.replace("ROOT = Path('/opt/mezan-preview-runtime-20260916')","ROOT = Path("+repr(str(STATE))+")")
    adapter=adapter.replace("BACKEND = Path('/app/backend')","BACKEND = ROOT / 'source/backend'")
    adapter=adapter.replace("'base_release_unchanged': True,","'base_release_unchanged': False, 'source_git_sha': "+repr(SHA)+",")
    before_import="""    # Deny external network before importing application modules.
    import ipaddress
    def egress(event,args):
        if event not in {'socket.connect','socket.sendto'}: return
        address=args[-1]
        if isinstance(address,str): return
        host=address[0]
        try: allowed=ipaddress.ip_address(host).is_loopback
        except ValueError: allowed=host=='localhost'
        if not allowed: raise PermissionError('Preview external network disabled')
    sys.addaudithook(egress)
    os.environ['MEZAN_PREVIEW_EXCEL_ORDERS']='1'
"""
    after_import="""    expected=[('snapchat_v2.scheduler','start'),('integrations_control_center.snapchat_capi_purchases','start'),('integrations_control_center.ads_auto_sync_scheduler','start'),('campaign_ai_subprocess_scheduler','_start_campaign_ai_subprocess_scheduler'),('advertising_product_watch_scheduler_v3','_start'),('server','on_startup')]
    require([(f.__module__,f.__name__) for f in server.app.router.on_startup]==expected,'Unexpected automatic workers')
    async def isolated_startup():
        await server.install_process_local_auth_security(server.db)
        server.app.state.startup_phase='preview_isolated_ready'
        server.app.state.readiness='ready'
        server.process_local_readiness_event.set()
    server.app.router.on_startup[:]=[isolated_startup]
    print('PREVIEW_ISOLATION external network denied; automatic startup workers disabled',flush=True)
"""
    needle="    server = importlib.import_module('server')\n"
    assert adapter.count(needle)==1
    adapter=adapter.replace(needle,before_import+needle+after_import)
    (STATE/'preview_password_runtime.py').write_text(adapter)
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','NODE_ENV':'production','REACT_APP_BACKEND_URL':ORIGIN}
    with (STATE/'build.log').open('w') as log:
        subprocess.run([node,'node_modules/vite/bin/vite.js','build','--mode','preview'],cwd=SOURCE/'frontend',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    index=SOURCE/'frontend/build/index.html'
    assert index.is_file() and index.stat().st_size
    assert not (SOURCE/'frontend/build/build-meta.json').exists()
    meta={'environment':'preview','api_origin':ORIGIN,'source_git_sha':SHA,'external_network':'denied','automatic_workers':'disabled','contains_production_release_identity':False,'index_sha256':hashlib.sha256(index.read_bytes()).hexdigest()}
    (SOURCE/'frontend/build/preview-meta.json').write_text(json.dumps(meta))
    (STATE/'frontend/build').mkdir(parents=True)
    (STATE/'frontend/build/preview-meta.json').write_text(json.dumps(meta))
    manifest={}
    for p in sorted(SOURCE.rglob('*')):
        if not p.is_file() or p.is_symlink(): continue
        rel=p.relative_to(SOURCE).as_posix()
        if '/node_modules/' in '/'+rel or '/build/' in '/'+rel: continue
        manifest[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
    (STATE/'source-manifest.json').write_text(json.dumps(manifest))
    (STATE/'adapter.sha256').write_text(hashlib.sha256(adapter.encode()).hexdigest())
    guard()
    assert CONF.read_bytes()==(STATE/'supervisor.before').read_bytes(),'Concurrent Supervisor change'
    (STATE/'ready.json').write_text(json.dumps({'source_git_sha':SHA,'node':node,'env':env}))
    print('PREPARED persistent full-tree Preview; no services changed')
def verify():
    assert socket.gethostname()==HOST
    ready=json.loads((STATE/'ready.json').read_text())
    assert ready['source_git_sha']==SHA
    for rel,digest in json.loads((STATE/'source-manifest.json').read_text()).items():
        assert hashlib.sha256((SOURCE/rel).read_bytes()).hexdigest()==digest,'Candidate source changed: '+rel
    assert hashlib.sha256((STATE/'preview_password_runtime.py').read_bytes()).hexdigest()==(STATE/'adapter.sha256').read_text()
    sys.path.insert(0,str(STATE))
    import preview_password_runtime
    preview_password_runtime.verify_boundary()
    return ready
def serve(kind):
    ready=verify()
    if kind=='backend':
        os.chdir(SOURCE/'backend')
        os.execv('/root/.venv/bin/python',['/root/.venv/bin/python','-B',str(STATE/'preview_password_runtime.py'),'serve'])
    os.chdir(SOURCE/'frontend')
    os.execve(ready['node'],[ready['node'],'node_modules/vite/bin/vite.js','preview','--host','0.0.0.0','--port','3000','--strictPort','--mode','preview'],ready['env'])
def activate():
    guard();verify()
    before=(STATE/'supervisor.before').read_bytes()
    assert CONF.read_bytes()==before,'Concurrent Supervisor change'
    cfg=configparser.RawConfigParser();cfg.read_string(before.decode())
    assert cfg.get('program:backend','command')=='/root/.venv/bin/python -B /opt/mezan-preview-runtime-20260916/preview_fulltree_runtime.py serve'
    assert cfg.get('program:frontend','directory')=='/opt/mezan-preview-signout-20260916/frontend'
    # Keep every unrelated program section byte-for-byte unchanged.
    import re
    text=before.decode()
    for kind in ['backend','frontend']:
        pattern=r'(?ms)^\[program:'+kind+r'\]\n.*?(?=^\[|\Z)'
        match=re.search(pattern,text);assert match
        section=match.group()
        section,n=re.subn(r'(?m)^command=.*$', 'command=/root/.venv/bin/python -B '+str(SCRIPT)+' serve-'+kind,section);assert n==1
        section,n=re.subn(r'(?m)^directory=.*$', 'directory='+str(STATE),section);assert n==1
        text=text[:match.start()]+section+text[match.end():]
    private(STATE/'supervisor.after',text.encode())
    guard();assert CONF.read_bytes()==before
    staging=CONF.with_suffix('.preview-new')
    staging.write_bytes(text.encode());staging.chmod(CONF.stat().st_mode&0o777)
    os.replace(staging,CONF)
    subprocess.run(['supervisorctl','reread'],check=True)
    subprocess.run(['supervisorctl','update','backend','frontend'],check=True)
    print('ACTIVATED; verify Preview and restart persistence before acceptance')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','verify','activate','serve-backend','serve-frontend'])
    a=p.parse_args().action
    if a=='prepare':prepare()
    elif a=='verify':verify();print('BOUNDARY AND SOURCE PASS')
    elif a=='activate':activate()
    else:serve(a.split('-',1)[1])

