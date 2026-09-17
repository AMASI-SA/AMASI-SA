"""Preview-only source-fidelity overlay installer.
Run once with python -B; candidate files must come from the pinned source SHA.
The private data backup must never be committed. Restart backend after success.
"""
import asyncio,json,hashlib,subprocess,shutil,os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
from bson import json_util
async def main():
 assert json.loads(subprocess.check_output(['python','-B','/app/scripts/production_release_guard.py','status']))['active'] is False
 root=Path('/opt/mezan-preview-excel-20260917'); candidate=Path('/tmp/mezan-preview-excel-20260917')
 backup=root/'before-fidelity'
 assert not backup.exists()
 v=dotenv_values('/app/backend/.env');assert urlsplit(v['MONGO_URL']).hostname in {'localhost','127.0.0.1','::1'}
 c=AsyncIOMotorClient(v['MONGO_URL']);d=c[v['DB_NAME']]
 drafts=await d.accounting_settlements_v2.find({'provider':'salla','statement_reference':'6743152'}).to_list(2);assert len(drafts)==1
 owner=drafts[0]['user_id'];q={'user_id':owner}
 snapshot={}
 for name in ['unified_orders','accounting_settlements_v2','settlement_entries','settlement_files','general_ledger']:
  snapshot[name]=await d[name].find(q).to_list(None)
 backup.mkdir(mode=0o700)
 p=backup/'data.json';p.write_text(json_util.dumps(snapshot));p.chmod(0o600)
 for name in ['manifest.json','order_engine/excel_projection.py']:
  target=backup/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,target)
 shutil.copy2(candidate/'excel_parser.py',root/'excel_parser.py')
 shutil.copy2(candidate/'order_engine/excel_projection.py',root/'order_engine/excel_projection.py')
 m=json.loads((root/'manifest.json').read_text())
 m['source_sha']='03ac5da33111fdbb7a3bc14183b76e13018253c9'
 for name in ['excel_parser.py','order_engine/excel_projection.py']:
  m['files'][name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
 (root/'manifest.json').write_text(json.dumps(m,indent=2))
 print('Backup saved; overlay installed; orders backed up',len(snapshot['unified_orders']))
 c.close()
asyncio.run(main())
