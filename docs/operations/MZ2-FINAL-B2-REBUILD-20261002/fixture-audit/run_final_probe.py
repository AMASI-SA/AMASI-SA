"""Reproducer for the retained probe. Not rerun after parent retained ownership.

Usage: <existing-venv-python> -B run_probe.py
Requires the pre-existing untouched original-B archive shown below.
Only invokes the two synthetic, network-denying in-memory probes.
"""
import os
import pathlib
import subprocess
import sys

roots={
    'oldB':r'C:/Users/amasi/AppData/Local/Temp/codex-security-artifacts-742e3d7504381c2d1bcbab5139791fc8856c05c3d3cdd9de0aa987463eb6c6a1/artifacts/baseline',
    'currentB2':r'C:/Users/amasi/mz2-final-b2-rebuild-20261002',
}
allow={'SYSTEMROOT','WINDIR','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT','SYSTEMDRIVE','PROGRAMDATA','TEMP','TMP'}
env={k:v for k,v in os.environ.items() if k.upper() in allow}
env.update(PYTHON_DOTENV_DISABLED='1',PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',JWT_SECRET='isolated-attribution-readonly-audit-not-production-20261002',MONGO_URL='mongodb://127.0.0.1:1',DB_NAME='unused_fixture_audit',CI='true')
child=pathlib.Path(__file__).with_name('probe.py').read_text(encoding='utf-8')
failed=False
for label,path in roots.items():
    env['PYTHONPATH']=path+'/backend'+os.pathsep+path+'/backend/tests'
    r=subprocess.run([sys.executable,'-B','-c',child],cwd=path,env=env,capture_output=True,text=True,timeout=60)
    print(label,r.returncode,r.stdout,r.stderr[-3500:] if r.returncode else '')
    failed |= bool(r.returncode)
raise SystemExit(1 if failed else 0)
