import json, os, subprocess, sys, time, hashlib
from pathlib import Path
from pymongo import MongoClient
root=Path('C:/Users/amasi/mz2-smoke-b-acceptance-20261002'); out=Path('C:/Users/amasi/mz2-c-evidence-20261001/full-regression-final'); out.mkdir(exist_ok=True)
head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
assert head=='33fd0dc0e3555ce3ad14fa7db93ad59d2ff5fb2d'
base=Path('C:/Users/amasi/mz2-final-integration-evidence-20261001/manual-pos-backend-selection.txt')
paths=[s.strip() for s in base.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
extra=['test_mz2_shipping_current_carrier','test_mz2_shipping_current_carrier_native','test_g47_current_shipping','test_salla_current_shipping','test_shipping_label_current_guard','test_order_engine_salla_refresh','test_mz2_shipping_native_rich_contracts','test_mz2_driver_review_history','test_mz2_driver_cash_chronology','test_mz2_driver_physical_cash']
paths=list(dict.fromkeys(paths+['backend/tests/'+p+'.py' for p in extra]))
source_files=subprocess.check_output(['git','-C',str(root),'ls-files','backend','.github/workflows'],text=True).splitlines()
def manifest(): return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in source_files if (root/p).is_file()}
before=manifest()
tree=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD^{tree}'],text=True).strip()
assert tree=='96de6449d7bc74c8761ecaff5da4062493f8fe8f'
assert not subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True).strip()
assert len(paths)==142
assert all((root/p).is_file() for p in paths)
uri='mongodb://127.0.0.1:27132/?replicaSet=mz2final'
client=MongoClient(uri,serverSelectionTimeoutMS=4000)
assert client.admin.command('hello')['isWritablePrimary']
assert client.admin.command({'getParameter':1,'transactionLifetimeLimitSeconds':1})['transactionLifetimeLimitSeconds']==5
client.close()
client=MongoClient('mongodb://127.0.0.1:27133/?directConnection=true',serverSelectionTimeoutMS=4000)
assert client.admin.command('ping')['ok']==1
client.close()
env={k:os.environ[k] for k in ('SystemRoot','WINDIR','TEMP','TMP','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROCESSOR_ARCHITECTURE','COMSPEC','PATHEXT') if k in os.environ}; env.update(PYTHONPATH='backend;backend/tests',PYTHON_DOTENV_DISABLED='1',PYTHONIOENCODING='utf-8',JWT_SECRET='synthetic-c3-regression-only-20261001',MZ2_TEST_MONGO_URI=uri,MZ2_AD_TEST_MONGO_URI=uri,BUILD20_INVOICE_TEST_MONGO_URL=uri,MZ2_TEST_STANDALONE_URI='mongodb://127.0.0.1:27133/?directConnection=true',MONGO_URL=uri,DB_NAME='unused_c3_full_regression',REACT_APP_BACKEND_URL='http://127.0.0.1:1')
env['PATH']='C:/Users/amasi/AppData/Local/npm-cache/_npx/7b6227974258eb8d/node_modules/node/bin;'+env['PATH']
argv=[sys.executable,'-u','-m','pytest','--noconftest','-v','--tb=short','-o','asyncio_mode=auto',*paths,'--durations=15','--junitxml='+str(out/'backend.xml')]
metadata={'head':head,'tree':tree,'selection_files':len(paths),'selection':paths,'argv':argv,'source_before':before,'replica_uri':uri,'standalone_uri':env['MZ2_TEST_STANDALONE_URI'],'transaction_lifetime_seconds':5,'environment_policy':'OS runtime allowlist only; synthetic credentials and explicit loopback test endpoints; dotenv disabled'}
if '--prepare-only' in sys.argv:
    (out/'prepared.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in metadata.items() if k not in ('selection','argv','source_before')})); sys.exit(0)
assert not (out/'started.json').exists(), 'Do not overwrite an existing run'
started=time.time()
(out/'started.json').write_text(json.dumps(dict(metadata,started_at=started,production_writes=0),indent=2),encoding='utf-8')
with (out/'backend.log').open('w',encoding='utf-8') as log: result=subprocess.run(argv,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
after=manifest(); report={'exit_code':result.returncode,'source_unchanged':before==after,'selection_files':len(paths),'head':head,'tree':tree,'duration_seconds':round(time.time()-started,3),'source_after':after,'clean_after':not subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True).strip()}
(out/'finished.json').write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps({k:v for k,v in report.items() if k!='source_after'})); sys.exit(result.returncode or (0 if before==after else 2))
