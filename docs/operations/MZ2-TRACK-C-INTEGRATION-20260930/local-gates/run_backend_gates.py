import os,re,sys,json,subprocess,time
from pathlib import Path
import yaml
from cryptography.fernet import Fernet
root=Path.cwd();out=root/'docs/operations/MZ2-TRACK-C-INTEGRATION-20260930/local-gates'
names=['fulfillment-v2','g47-focused-integration','prod-preparation-piece-operations','supplier-invoice-service-policy','auth-passkey-security','security-gate','build20-unified-qoyod','qoyod-rounding-lrm-tests','qoyod-payment-freshness','qoyod-memory-bounds','qoyod-credential-rotation-acceptance']
env=os.environ.copy();env.update(PYTHONPATH=str(root/'backend')+os.pathsep+str(root/'backend/tests'),PYTHONDONTWRITEBYTECODE='1',PYTHON_DOTENV_DISABLED='1',MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27018/?replicaSet=mz2test',MZ2_TEST_STANDALONE_URI='mongodb://127.0.0.1:27019/?directConnection=true',QOYOD_TEST_MONGO_URL='mongodb://127.0.0.1:27018/?replicaSet=mz2test',MONGO_URL='mongodb://127.0.0.1:27018/?replicaSet=mz2test',DB_NAME='mz2_track_c_regression_synthetic',QOYOD_API_BASE='https://synthetic.invalid',QOYOD_TOKEN_ENC_KEY=Fernet.generate_key().decode())
results=[]
for name in names:
 if len(sys.argv)>1 and name not in sys.argv[1:]:continue
 doc=yaml.safe_load((root/'.github/workflows'/f'{name}.yml').read_text(encoding='utf-8'));tests=[]
 for job in doc['jobs'].values():
  for step in job.get('steps',[]):
   run=step.get('run','')
   if ('pytest' not in run and 'unittest' not in run) or 'pip install' in run:continue
   cwd=step.get('working-directory',job.get('defaults',{}).get('run',{}).get('working-directory',''))
   for token in re.findall(r'(?:backend/)?tests/test_[\w*]+\.py',run):
    path=(Path(cwd)/token).as_posix() if cwd else token
    tests.extend(str(x.relative_to(root)).replace('\\','/') for x in root.glob(path))
   # Unittest steps are run separately using their declared runner.
 tests=list(dict.fromkeys(tests));blocked=[]
 for test in tests:
  text=(root/test).read_text(encoding='utf-8')
  if re.search(r'^\s*(?:from server import|import server\b|load_dotenv\()',text,re.M):blocked.append(test)
 if blocked:raise RuntimeError('Unsafe direct test import: '+str(blocked))
 workdir=root/'backend' if name in {'qoyod-payment-freshness','qoyod-memory-bounds','qoyod-credential-rotation-acceptance','g47-focused-integration','prod-preparation-piece-operations'} else root
 command=[sys.executable,'-m','pytest','--import-mode=importlib','--noconftest','-p','no:cacheprovider','-q','--tb=short',*[str(root/test) for test in tests],'--junitxml='+str(out/f'{name}.xml')]
 start=time.time()
 with (out/f'{name}.log').open('w',encoding='utf-8') as log:
  result=subprocess.run(command,cwd=workdir,env=env,stdout=log,stderr=subprocess.STDOUT)
 record={'workflow':name,'command':command,'exit_code':result.returncode,'duration_seconds':round(time.time()-start,2),'test_files':tests}
 results.append(record);(out/f'{name}.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
 print(name,result.returncode,flush=True)
