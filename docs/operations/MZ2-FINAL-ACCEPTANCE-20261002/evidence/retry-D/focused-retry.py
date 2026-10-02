import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path('C:/Users/amasi/mz2-late-evidence-linear-20261002')
OUT=Path(__file__).resolve().parent/'focused-current-carrier'
assert not OUT.exists()
OUT.mkdir()
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT).decode().strip()
head=git('rev-parse','HEAD')
assert head=='88cc9131027fd6783a46e2e00fc6aaec4788b8fa' and not git('status','--porcelain')
env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROCESSOR_ARCHITECTURE','COMSPEC','PATHEXT','SYSTEMDRIVE','PROGRAMDATA','NUMBER_OF_PROCESSORS'}}
env.update(PYTHONPATH=str(ROOT/'backend')+os.pathsep+str(ROOT/'backend/tests'),PYTHON_DOTENV_DISABLED='1',PYTHONIOENCODING='utf-8',JWT_SECRET='synthetic-mz2-verification-only',MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27135/?replicaSet=mz2late',MONGO_URL='mongodb://127.0.0.1:27135/?replicaSet=mz2late',DB_NAME='unused_mz2_verification',TEMP=str(OUT.parent/'temp'),TMP=str(OUT.parent/'temp'))
command=[sys.executable,'-u','-m','pytest','--noconftest','-v','--tb=short','-o','asyncio_mode=auto','backend/tests/test_mz2_shipping_current_carrier_native.py','--junitxml='+str(OUT/'focused.xml')]
(OUT/'started.json').write_text(json.dumps({'head':head,'tree':git('rev-parse','HEAD^{tree}'),'argv':command,'cwd':str(ROOT),'environment_policy':'Same OS allowlist; dotenv disabled; synthetic JWT; owned loopback replica; TEMP on D'},indent=2)+'\n')
started=time.monotonic()
with (OUT/'focused.log').open('w',encoding='utf-8') as log:
 result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
unchanged=git('rev-parse','HEAD')==head and not git('status','--porcelain')
report={'head':head,'exit_code':result.returncode,'source_unchanged':unchanged,'elapsed_seconds':time.monotonic()-started,'production_financial_writes':0}
(OUT/'finished.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
sys.exit(result.returncode if unchanged else 2)
