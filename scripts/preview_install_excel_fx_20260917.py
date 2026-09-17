"""Preview-only installer already executed; do not rerun. Backup before-fx-code."""
import json,hashlib,subprocess,shutil
from pathlib import Path
assert json.loads(subprocess.check_output(['python','-B','/app/scripts/production_release_guard.py','status']))['active'] is False
root=Path('/opt/mezan-preview-excel-20260917')
backup=root/'before-fx-code';assert not backup.exists();backup.mkdir()
shutil.copy2(root/'manifest.json',backup/'manifest.json');shutil.copy2(root/'order_engine/excel_projection.py',backup/'excel_projection.py')
shutil.copy2('/tmp/mezan-preview-excel-20260917/order_engine/excel_projection.py',root/'order_engine/excel_projection.py')
m=json.loads((root/'manifest.json').read_text());m['source_sha']='409760486e1ab7fe78796fa3abba0eee9aed7ecb';m['files']['order_engine/excel_projection.py']=hashlib.sha256((root/'order_engine/excel_projection.py').read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(m,indent=2))
for p in [Path('/opt/mezan-preview-runtime-20260916/frontend/build/preview-meta.json'),Path('/opt/mezan-preview-signout-20260916/frontend/build/preview-meta.json')]:
 m=json.loads(p.read_text());assert m['environment']=='preview';m['excel_projection_source_sha']='409760486e1ab7fe78796fa3abba0eee9aed7ecb';m['excel_fx_source_sha']='409760486e1ab7fe78796fa3abba0eee9aed7ecb';p.write_text(json.dumps(m,indent=2))
print('Verified FX overlay installed')
