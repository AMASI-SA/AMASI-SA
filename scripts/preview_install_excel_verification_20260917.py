"""Install verified Preview projection after tests; run once, then restart backend."""
import json,hashlib,subprocess,shutil
from pathlib import Path
assert json.loads(subprocess.check_output(['python','-B','/app/scripts/production_release_guard.py','status']))['active'] is False
root=Path('/opt/mezan-preview-excel-20260917')
backup=root/'before-verification-code';assert not backup.exists();backup.mkdir()
shutil.copy2(root/'manifest.json',backup/'manifest.json');shutil.copy2(root/'order_engine/excel_projection.py',backup/'excel_projection.py')
shutil.copy2('/tmp/mezan-preview-excel-20260917/order_engine/excel_projection.py',root/'order_engine/excel_projection.py')
m=json.loads((root/'manifest.json').read_text());m['source_sha']='b42471b1db090f379ae270a7f778cc448f9461ab';m['files']['order_engine/excel_projection.py']=hashlib.sha256((root/'order_engine/excel_projection.py').read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(m,indent=2))
for p in [Path('/opt/mezan-preview-runtime-20260916/frontend/build/preview-meta.json'),Path('/opt/mezan-preview-signout-20260916/frontend/build/preview-meta.json')]:
 m=json.loads(p.read_text());assert m['environment']=='preview';m['excel_projection_source_sha']='b42471b1db090f379ae270a7f778cc448f9461ab';m['excel_verification_source_sha']='b42471b1db090f379ae270a7f778cc448f9461ab';p.write_text(json.dumps(m,indent=2))
print('Verified evidence overlay installed')
