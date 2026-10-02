import hashlib,json,os,subprocess,sys,time
from pathlib import Path
from pymongo import MongoClient
root=Path('C:/Users/amasi/mz2-rebase-audit-20261002')
out=Path('C:/Users/amasi/mz2-c-evidence-20261001/rebase-audit')
client=MongoClient('mongodb://127.0.0.1:27134/?directConnection=true',serverSelectionTimeoutMS=5000)
client.admin.command('replSetInitiate',{'_id':'mz2audit','members':[{'_id':0,'host':'127.0.0.1:27134'}]})
for _ in range(100):
    if client.admin.command('hello').get('isWritablePrimary'):break
    time.sleep(.2)
else:raise RuntimeError('Replica never became primary')
client.close()
files=subprocess.check_output(['git','ls-files','backend','.github/workflows'],cwd=root,text=True).splitlines()
manifest=lambda:{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files}
before=manifest()
env={k:os.environ[k] for k in ('SystemRoot','WINDIR','TEMP','TMP','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','COMSPEC','PATHEXT') if k in os.environ}
env.update(PYTHONPATH='backend;backend/tests',PYTHON_DOTENV_DISABLED='1',PYTHONIOENCODING='utf-8',MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27134/?replicaSet=mz2audit',JWT_SECRET='synthetic-rebase-audit-only-long-enough-key')
argv=[sys.executable,'-m','pytest','--noconftest','-v','--tb=short','-o','asyncio_mode=auto','scripts/testing/mz2_rebase_audit/test_track_f_proof_boundary.py','--junitxml='+str(out/'proof-boundary.xml')]
(out/'test-started.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'argv':argv,'source_before':before,'new_test_sha256':hashlib.sha256((root/'scripts/testing/mz2_rebase_audit/test_track_f_proof_boundary.py').read_bytes()).hexdigest(),'production_writes':0},indent=2),encoding='utf-8')
start=time.time()
with (out/'proof-boundary.log').open('w',encoding='utf-8') as log:result=subprocess.run(argv,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
report={'exit_code':result.returncode,'seconds':round(time.time()-start,3),'production_source_unchanged':manifest()==before,'production_writes':0}
(out/'test-finished.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report));raise SystemExit(result.returncode)
