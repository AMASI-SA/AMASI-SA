"""Finite isolated verification jobs; never a production/release entrypoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['backend', 'frontend', 'browser'])
parser.add_argument('--head', required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--node', required=True)
parser.add_argument('--mongod')
parser.add_argument('--playwright')
args = parser.parse_args()
out = args.output.resolve()
assert args.output.is_absolute() and not out.exists(), 'Fresh absolute evidence directory required'
assert re.fullmatch('[0-9a-f]{40}', args.head)

def git(*argv):
    return subprocess.check_output(['git', *argv], cwd=ROOT).decode('utf-8').strip()

assert git('rev-parse', 'HEAD') == args.head
assert not git('status', '--porcelain'), 'Committed source required'
assert subprocess.check_output([args.node, '--version'], text=True).strip() == 'v22.23.2'
out.mkdir(parents=True)
env = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','USERPROFILE','APPDATA','LOCALAPPDATA',
    'PROGRAMFILES','PROGRAMFILES(X86)','PROCESSOR_ARCHITECTURE','COMSPEC','PATHEXT',
    'SYSTEMDRIVE','PROGRAMDATA','NUMBER_OF_PROCESSORS'}}
uri = 'mongodb://127.0.0.1:27135/?replicaSet=mz2late'
env.update(PYTHONPATH=str(ROOT/'backend')+os.pathsep+str(ROOT/'backend/tests'),
    PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', JWT_SECRET='synthetic-mz2-verification-only',
    MZ2_TEST_MONGO_URI=uri, MZ2_AD_TEST_MONGO_URI=uri, BUILD20_INVOICE_TEST_MONGO_URL=uri,
    MZ2_TEST_STANDALONE_URI='mongodb://127.0.0.1:27136/?directConnection=true',
    MONGO_URL=uri, DB_NAME='unused_mz2_verification', REACT_APP_BACKEND_URL='http://127.0.0.1:1',
    CI='true')
env['PATH'] = str(Path(args.node).parent)+os.pathsep+env['PATH']
flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
files = git('ls-files', 'backend', 'frontend', '.github/workflows', 'scripts/testing/mz2_late_delivery_evidence').splitlines()
def manifest():
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files if (ROOT/p).is_file()}
before = manifest()
record = {'head':args.head,'tree':git('rev-parse','HEAD^{tree}'),'mode':args.mode,
    'source_before':before,'environment_policy':'OS allowlist; dotenv disabled; synthetic JWT; explicit loopback Mongo',
    'production_financial_writes':0,'commands':[]}
def save(name, value):
    (out/name).write_text(json.dumps(value, indent=2), encoding='utf-8')
save('started.json', record)

def run(name, argv, cwd=ROOT, timeout=None):
    record['commands'].append({'label':name,'argv':argv,'cwd':str(cwd)})
    save('started.json', record)
    with (out/(name+'.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    print(json.dumps({'stage':name,'exit_code':result.returncode}), flush=True)
    return result.returncode

def get_proof():
    with urllib.request.urlopen('http://127.0.0.1:18772/__test/proof', timeout=3) as response:
        return json.load(response)

def backend():
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=4000)
    assert client.admin.command('hello')['isWritablePrimary']
    assert client.admin.command({'getParameter':1,'transactionLifetimeLimitSeconds':1})['transactionLifetimeLimitSeconds'] == 5
    client.close()
    assert args.mongod and Path(args.mongod).is_file()
    with socket.socket() as probe: probe.bind(('127.0.0.1',27136))
    data=out/'standalone-data'; data.mkdir()
    argv=[args.mongod,'--port','27136','--bind_ip','127.0.0.1','--dbpath',str(data),'--logpath',str(out/'standalone.log')]
    process=subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    save('standalone-process.json', {'pid':process.pid,'argv':argv})
    standalone=MongoClient(env['MZ2_TEST_STANDALONE_URI'],serverSelectionTimeoutMS=1000)
    def verify_owned_standalone():
        # Windows serverStatus can contain malformed UTF8 diagnostic counters.
        # Verify the live owned child and its unique dbpath/loopback options
        # without decoding unrelated server diagnostics or changing BSON policy.
        assert process.poll() is None
        options=standalone.admin.command('getCmdLineOpts')['parsed']
        assert Path(options['storage']['dbPath']).resolve() == data.resolve()
        assert options['net']['port'] == 27136 and options['net']['bindIp'] == '127.0.0.1'
    try:
        for _ in range(20):
            assert process.poll() is None, 'Owned standalone exited'
            try: standalone.admin.command('ping'); break
            except Exception: time.sleep(.5)
        verify_owned_standalone()
        paths=(ROOT/'docs/operations/MZ2-LATE-EVIDENCE-20261002/BACKEND-SELECTION.txt').read_text().splitlines()
        assert len(paths)==147 and all((ROOT/p).is_file() for p in paths)
        save('selection.json', paths)
        return run('backend', [sys.executable,'-u','-m','pytest','--noconftest','-v','--tb=short','-o','asyncio_mode=auto',*paths,'--durations=15','--junitxml='+str(out/'backend.xml')],timeout=3600)
    finally:
        if process.poll() is None:
            verify_owned_standalone()
            try: standalone.admin.command('shutdown')
            except Exception: pass  # Mongo closes the connection during shutdown.
            process.wait(timeout=30)
        standalone.close()
        save('standalone-cleanup.json', {'owned_pid':process.pid,'exit_code':process.returncode,'data_retained':True})

def frontend():
    result=run('frontend', [args.node,'node_modules/react-scripts/bin/react-scripts.js','test','--watchAll=false','--runInBand','--json','--outputFile='+str(out/'frontend.json')],ROOT/'frontend',1200)
    # Compile to a fresh evidence directory. This is not the governed release
    # artifact, release-intent generation, lease preparation or deployment.
    build=run('compile', [args.node,'node_modules/vite/bin/vite.js','build','--outDir',str(out/'dist')],ROOT/'frontend',1200)
    return result or build

def browser():
    assert args.playwright and Path(args.playwright).is_dir()
    env.update(MZ2_LATE_DIST=str(out/'dist'), MZ2_LATE_STOP_FILE=str(out/'stop-server'),
        MZ2_LATE_PORT='18772',MZ2_LATE_ORIGIN='http://127.0.0.1:18772',MZ2_LATE_OUTPUT=str(out/'browser'),
        PLAYWRIGHT_MODULE=args.playwright,MZ2_BROWSER_CHANNEL='msedge')
    with socket.socket() as probe: probe.bind(('127.0.0.1',18772))
    assert run('fixture-build',[args.node,'scripts/testing/mz2_late_delivery_evidence/build.cjs']) == 0
    command=[sys.executable,'-u','scripts/testing/mz2_late_delivery_evidence/server.py']
    with (out/'server.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,creationflags=flags)
        save('server-process.json',{'pid':process.pid,'argv':command})
        initial=None
        try:
            for _ in range(40):
                assert process.poll() is None, 'Fixture server exited; inspect server.log'
                try: initial=get_proof(); break
                except Exception: time.sleep(.5)
            assert initial and initial['synthetic_only'] and initial['write_control']['writes_paused'] is True
            save('server-before.json',initial)
            return run('browser',[args.node,'scripts/testing/mz2_late_delivery_evidence/browser.cjs'],timeout=180)
        finally:
            (out/'stop-server').touch()
            process.wait(timeout=45)
            cleanup={'server_exit_code':process.returncode}
            if initial:
                from pymongo import MongoClient
                dbname=initial['database'];assert re.fullmatch('mz2_track_f_[a-f0-9]{32}',dbname)
                client=MongoClient(uri,serverSelectionTimeoutMS=4000)
                cleanup.update(database=dbname,database_removed=dbname not in client.list_database_names());client.close()
                assert cleanup['database_removed'], 'Fixture cleanup incomplete'
            save('browser-cleanup.json',cleanup)

started=time.time()
code=1
try:
    code={'backend':backend,'frontend':frontend,'browser':browser}[args.mode]()
finally:
    after=manifest()
    same=before==after and git('rev-parse','HEAD')==args.head
    save('finished.json',{'head':args.head,'tree':record['tree'],'mode':args.mode,'exit_code':code,
        'source_unchanged':same,'elapsed_seconds':round(time.time()-started,3),'source_after':after,
        'production_financial_writes':0,'release_readiness':'NO'})
    if not same: code=2
sys.exit(code)
